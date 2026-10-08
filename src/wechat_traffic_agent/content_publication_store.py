"""An independent content data plane underneath a stable website presentation.

Only article HTML and derived indexes are materialized. Site templates, hero,
navigation and website deployments are never rebuilt or activated by an article.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile

from .channels.web import _route_for
from .site_cli import _load_artifacts, _curate_web_taxonomy
from .web_editorial import digest


COLLECTIONS = ("articles", "guides", "research", "notes", "archive", "topics")


class ContentPublicationStore:
    def __init__(self, *, state_root: Path, public_root: Path, config):
        self.state_root = Path(state_root)
        self.public_root = Path(public_root)
        self.config = config
        self.private_root = self.state_root / "content" / "automatic_website"
        self.inventory = self.private_root / "articles.json"
        self.data_releases = self.public_root / "content-releases"
        self.current = self.public_root / "content-current"

    def ready(self) -> bool:
        return self.current.is_symlink() and (self.current / "articles.json").is_file()

    def bootstrap(self, *, site_root: Path, legacy_inventory: Path) -> dict:
        """One-time extraction of existing published data from the website."""
        if self.ready():
            return {"status": "ALREADY_READY", "revision": self.current.resolve().name}
        if self.current.exists() or self.current.is_symlink():
            raise ValueError("content-current exists but is not a valid content release")
        site_root = Path(site_root)
        if not site_root.is_symlink():
            raise ValueError("site shell must already be activated")
        artifacts = _load_artifacts(legacy_inventory)
        payload = [a.model_dump(mode="json") for a in artifacts]
        revision = "bootstrap-" + digest(payload)[:20]
        target = self.data_releases / revision
        self.data_releases.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            stage = Path(tempfile.mkdtemp(prefix=".content-bootstrap-", dir=self.data_releases))
            try:
                for collection in COLLECTIONS:
                    source = site_root / collection
                    if source.is_dir():
                        shutil.copytree(source, stage / collection, copy_function=os.link)
                for name in ("sitemap.xml", "feed.xml"):
                    shutil.copy2(site_root / name, stage / name)
                self._write(stage / "articles.json", json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                self._write(stage / "data" / "articles.json", self._catalog(artifacts))
                stage.chmod(0o755)
                os.replace(stage, target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        self.private_root.mkdir(parents=True, exist_ok=True)
        if self.inventory.exists():
            if json.loads(self.inventory.read_text(encoding="utf-8")) != payload:
                raise ValueError("existing independent inventory differs from rendered website")
        else:
            self._atomic(self.inventory, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        pending = self.public_root / "content-current.next"
        pending.unlink(missing_ok=True)
        pending.symlink_to(target)
        os.replace(pending, self.current)
        return {"status": "READY", "revision": revision, "count": len(artifacts)}

    @staticmethod
    def _write(path: Path, value: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o644)

    @classmethod
    def _atomic(cls, path: Path, value: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".content-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(value)
            Path(name).chmod(0o644)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def _catalog(artifacts):
        ordered = sorted(artifacts, key=lambda a: a.updated_at or a.created_at, reverse=True)
        return json.dumps({
            "schema_version": "ferro-content-catalog.v1",
            "articles": [{
                "artifact_id": a.artifact_id,
                "title": a.title,
                "summary": a.summary,
                "kind": a.kind.value,
                "tags": list(a.tags),
                "url": _route_for(a),
                "updated_at": (a.updated_at or a.created_at).isoformat(),
            } for a in ordered],
        }, ensure_ascii=False, sort_keys=True) + "\n"

    @staticmethod
    def _validate_pages(root: Path, routes: list[str]):
        # All changed pages must remain indexable, self-contained and readable.
        for route in routes:
            path = root / route.lstrip("/") / "index.html"
            if not path.is_file():
                raise ValueError("content page missing: " + route)
            html_text = path.read_text(encoding="utf-8")
            if "<title>" not in html_text or 'rel="canonical"' not in html_text or 'name="description"' not in html_text:
                raise ValueError("content page metadata invalid: " + route)

    def publish(self, artifact, *, website_channel) -> dict:
        """Persist one approved artifact and atomically switch only content data.

        Returning an unchanged website release name proves that page design is
        independent of the publication. No whole-site build is executed here.
        """
        if not artifact.public_safe or not self.ready():
            raise ValueError("approved content and initialized data plane required")
        self.private_root.mkdir(parents=True, exist_ok=True)
        with (self.private_root / ".content-publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            artifacts = _load_artifacts(self.inventory)
            for old in artifacts:
                if old.artifact_id == artifact.artifact_id:
                    if old != artifact:
                        raise ValueError("published artifact ID conflict")
                    return {
                        "status": "ALREADY_PUBLISHED",
                        "revision": self.current.resolve().name,
                        "content_revision": self.current.resolve().name,
                        "route": _route_for(artifact),
                    }
                if _route_for(old) == _route_for(artifact):
                    raise ValueError("public article route conflict")
            artifacts.append(artifact)
            ordered = _curate_web_taxonomy(artifacts)
            payload = [a.model_dump(mode="json") for a in artifacts]
            revision = digest(payload)
            target = self.data_releases / revision
            if not target.exists():
                stage = Path(tempfile.mkdtemp(prefix=".content-update-", dir=self.data_releases))
                try:
                    shutil.rmtree(stage)
                    shutil.copytree(self.current.resolve(strict=True), stage, copy_function=os.link)
                    route = _route_for(artifact)
                    self._atomic(
                        stage / route.lstrip("/") / "index.html",
                        website_channel._artifact_page(artifact, route, ordered),
                    )
                    categories = {
                        "Articles": [a for a in ordered if a.kind.value == "article"],
                        "Guides": [a for a in ordered if a.kind.value == "guide"],
                        "Research": [a for a in ordered if a.kind.value == "research"],
                        "Notes": [a for a in ordered if a.kind.value == "note"],
                    }
                    name = artifact.kind.value
                    collection = {
                        "article": ("Articles", "articles"),
                        "guide": ("Guides", "guides"),
                        "research": ("Research", "research"),
                        "note": ("Notes", "notes"),
                    }
                    heading, prefix = collection[name]
                    self._atomic(
                        stage / prefix / "index.html",
                        website_channel._listing(heading, categories[heading], "/" + prefix + "/"),
                    )
                    self._atomic(stage / "archive" / "index.html", website_channel._archive(ordered))
                    # Only topic-derived views are rendered, not the website.
                    topic_root = Path(tempfile.mkdtemp(prefix=".content-topics-", dir=self.data_releases))
                    try:
                        website_channel._render_topics(topic_root, ordered)
                        for page in topic_root.rglob("*.html"):
                            relative = page.relative_to(topic_root)
                            self._atomic(stage / relative, page.read_text(encoding="utf-8"))
                    finally:
                        shutil.rmtree(topic_root)
                    routes = ["/", "/articles/", "/guides/", "/research/", "/notes/",
                              "/archive/", "/topics/", *(_route_for(a) for a in ordered),
                              *website_channel._topic_routes(ordered)]
                    try:
                        import xml.etree.ElementTree as ET
                        ET.fromstring(website_channel._sitemap(routes, ordered))
                        ET.fromstring(website_channel._feed(ordered))
                    except Exception as exc:
                        raise ValueError("invalid content sitemap/feed") from exc
                    self._atomic(stage / "sitemap.xml", website_channel._sitemap(routes, ordered))
                    self._atomic(stage / "feed.xml", website_channel._feed(ordered))
                    self._atomic(stage / "articles.json", json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                    self._atomic(stage / "data" / "articles.json", self._catalog(ordered))
                    self._validate_pages(stage, [route])
                    stage.chmod(0o755)
                    os.replace(stage, target)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
            # Public content pointer moves first. If interrupted before the
            # inventory update, replay reconstructs the same content revision
            # and finishes the private catalogue (no loss of the new article).
            pending = self.public_root / "content-current.next"
            pending.unlink(missing_ok=True)
            pending.symlink_to(target)
            os.replace(pending, self.current)
            self._atomic(self.inventory, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            return {
                "status": "PUBLISHED",
                "revision": (self.public_root / "current").resolve().name,
                "content_revision": revision,
                "route": _route_for(artifact),
            }
