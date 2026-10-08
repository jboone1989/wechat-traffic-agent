#!/usr/bin/env python3
"""One-time decoupling of Ferro's immutable website shell from published content."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.automatic_website import AutomaticAgentWebChannel
from wechat_traffic_agent.channels.web import StaticWebsiteConfig
from wechat_traffic_agent.content_artifact import ContentKind
from wechat_traffic_agent.content_publication_store import ContentPublicationStore, COLLECTIONS
from wechat_traffic_agent.site_audit import audit_static_site
from wechat_traffic_agent.site_cli import _load_artifacts, _curate_web_taxonomy
from wechat_traffic_agent.web_editorial import resolve_rendered_inventory


def migrate(*, state_root: Path, public_root: Path, config: StaticWebsiteConfig, starter_path: Path) -> dict:
    site_link = public_root / "current"
    old_design = site_link.resolve(strict=True)
    inventory = resolve_rendered_inventory(
        fallback_path=starter_path,
        site_root=site_link,
        inventories_dir=state_root / "content" / "automatic_website" / "inventories",
    )
    store = ContentPublicationStore(
        state_root=state_root, public_root=public_root, config=config,
    )
    bootstrap = store.bootstrap(site_root=site_link, legacy_inventory=inventory)
    artifacts = sorted(_curate_web_taxonomy(_load_artifacts(store.inventory)),
                       key=lambda a: a.updated_at or a.created_at, reverse=True)
    categories = {
        "Articles": [a for a in artifacts if a.kind is ContentKind.ARTICLE],
        "Guides": [a for a in artifacts if a.kind is ContentKind.GUIDE],
        "Research": [a for a in artifacts if a.kind is ContentKind.RESEARCH],
        "Notes": [a for a in artifacts if a.kind is ContentKind.NOTE],
    }
    homepage = AutomaticAgentWebChannel(config)._home(artifacts, categories)
    if "data:image/webp;base64" not in homepage or "/api/agent/status?content=articles" not in homepage:
        raise ValueError("home must retain the original image and load the independent catalogue")
    name = "ferro-content-mounted-" + sha256((homepage + "mount:v1").encode("utf-8")).hexdigest()[:18]
    target = public_root / "releases" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        stage = Path(tempfile.mkdtemp(prefix=".site-design-", dir=public_root / "releases"))
        try:
            shutil.rmtree(stage)
            shutil.copytree(old_design, stage, copy_function=os.link, symlinks=True)
            temporary_home = stage / ".home-data.tmp"
            temporary_home.write_text(homepage, encoding="utf-8")
            temporary_home.chmod(0o644)
            os.replace(temporary_home, stage / "index.html")
            # The dynamic /screeps/ endpoint is served by 8104. Its legacy
            # static placeholder predates the normal page metadata contract.
            screeps = stage / "screeps" / "index.html"
            if screeps.is_file():
                page = screeps.read_text(encoding="utf-8")
                extra = ""
                if 'name="description"' not in page:
                    extra += '<meta name="description" content="Ferro Screeps experiments and neural sensorimotor work">'
                if 'rel="canonical"' not in page:
                    extra += '<link rel="canonical" href="' + config.base_url + '/screeps/">'
                if extra:
                    if "</head>" not in page:
                        raise ValueError("legacy Screeps metadata cannot be repaired")
                    temp_screeps = stage / "screeps" / ".index.tmp"
                    temp_screeps.write_text(page.replace("</head>", extra + "</head>", 1), encoding="utf-8")
                    temp_screeps.chmod(0o644)
                    os.replace(temp_screeps, screeps)
            # The public server follows these stable mount paths without any
            # Nginx change. Future articles only move content-current.
            for name in (*COLLECTIONS, "sitemap.xml", "feed.xml", "data"):
                mount = stage / name
                if mount.is_symlink() or mount.is_file():
                    mount.unlink()
                elif mount.is_dir():
                    shutil.rmtree(mount)
                mount.symlink_to(store.current / name)
            if not all((stage / name).exists() for name in (*COLLECTIONS, "sitemap.xml", "feed.xml", "data")):
                raise RuntimeError("content mounts are incomplete")
            audit = audit_static_site(stage, expect_indexable=config.indexable)
            if not audit.passed:
                from wechat_traffic_agent.site_audit import _PageParser
                missing = []
                for document in sorted(stage.rglob("*.html")):
                    parser = _PageParser()
                    parser.feed(document.read_text(encoding="utf-8"))
                    if not (parser.has_title and parser.has_description and parser.has_canonical):
                        missing.append(document.relative_to(stage).as_posix())
                raise ValueError("new design audit failed: " + json.dumps({
                    **audit.model_dump(mode="json"), "missing_metadata": missing[:30],
                }))
            os.replace(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if site_link.resolve(strict=True) not in {old_design, target}:
        raise RuntimeError("website design changed during migration")
    if site_link.resolve(strict=True) != target:
        pending = public_root / "current.design.next"
        pending.unlink(missing_ok=True)
        pending.symlink_to(target)
        os.replace(pending, site_link)
    report = {
        "status": "MIGRATED",
        "site_design_release": target.name,
        "previous_design_release": old_design.name,
        "content_release": store.current.resolve(strict=True).name,
        "article_count": len(artifacts),
        "hero_preserved": True,
        "data_feed_in_home": True,
        "site_shell_independent_of_content": True,
        "content_mounts": list(COLLECTIONS) + ["sitemap.xml", "feed.xml", "data"],
        "bootstrap": bootstrap,
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--starter", type=Path, required=True)
    args = parser.parse_args()
    config = StaticWebsiteConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    print(json.dumps(migrate(state_root=args.state_root, public_root=args.public_root,
                             config=config, starter_path=args.starter), ensure_ascii=False))


if __name__ == "__main__":
    main()
