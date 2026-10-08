"""CLI for building the Content Agent static website channel."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from .channels.agent_web import ContentAgentWebChannel
from .channels.web import (
    StaticWebsiteConfig,
    _tag_slug,
    assess_adsense_readiness,
)
from .content_artifact import ContentArtifact


_MIN_INDEXED_TOPIC_ITEMS = 1


def build_site(
    config_path: Path,
    artifacts_path: Path,
    output_dir: Path,
    *,
    approved_content_root: Path | None = None,
) -> dict[str, object]:
    if approved_content_root is not None:
        private = approved_content_root.resolve()
        public = output_dir.resolve()
        if private == public or private in public.parents or public in private.parents:
            raise ValueError("website output must be separate from private content state")
    config = StaticWebsiteConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    artifacts = _site_artifacts(artifacts_path, approved_content_root)
    web_artifacts = _curate_web_taxonomy(artifacts)
    readiness = assess_adsense_readiness(config, artifacts)
    ContentAgentWebChannel(config).build(web_artifacts, output_dir)
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "artifact_count": len(artifacts),
        "artifact_ids": [item.artifact_id for item in artifacts],
        "indexed_topic_count": len(_indexed_topic_slugs(artifacts)),
        "readiness": readiness.model_dump(mode="json"),
    }


def inspect_site_readiness(
    config_path: Path,
    artifacts_path: Path,
    *,
    approved_content_root: Path | None = None,
) -> dict[str, object]:
    config = StaticWebsiteConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    artifacts = _site_artifacts(artifacts_path, approved_content_root)
    return assess_adsense_readiness(config, artifacts).model_dump(mode="json")


def _site_artifacts(path: Path, approved_content_root: Path | None) -> list[ContentArtifact]:
    artifacts = _load_artifacts(path)
    if approved_content_root is not None:
        from .content_admission import ContentAdmission

        artifacts.extend(ContentAdmission(approved_content_root).approved_artifacts())
    ids = [item.artifact_id for item in artifacts]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate artifact_id across starter and admitted content")
    return artifacts


def _curate_web_taxonomy(artifacts: list[ContentArtifact]) -> list[ContentArtifact]:
    """Expose every non-empty canonical Topic from its first real published artifact."""
    allowed = _indexed_topic_slugs(artifacts)
    curated: list[ContentArtifact] = []
    for item in artifacts:
        tags = tuple(tag for tag in item.tags if _tag_slug(tag) in allowed)
        curated.append(item.model_copy(update={"tags": tags}))
    return curated


def _indexed_topic_slugs(artifacts: list[ContentArtifact]) -> set[str]:
    counts: Counter[str] = Counter()
    for item in artifacts:
        slugs = {_tag_slug(tag) for tag in item.tags}
        counts.update(slug for slug in slugs if slug)
    return {slug for slug, count in counts.items() if count >= _MIN_INDEXED_TOPIC_ITEMS}


def _load_artifacts(path: Path) -> list[ContentArtifact]:
    path = Path(path)
    if path.suffix in {".sqlite3", ".db"}:
        from .article_repository import ArticleRepository
        return ArticleRepository(path).list_artifacts()
    files = _artifact_files(path)
    artifacts: list[ContentArtifact] = []
    for file_path in files:
        raw: Any = json.loads(file_path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else [raw]
        for row in rows:
            artifacts.append(ContentArtifact.model_validate(row))
    if not artifacts:
        raise ValueError("no content artifacts found")
    ids = [item.artifact_id for item in artifacts]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate artifact_id in input")
    return artifacts


def _artifact_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"artifact path does not exist: {path}")
    return sorted(item for item in path.glob("*.json") if item.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and validate the Content Agent static website")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--approved-content-root", type=Path,
                        help="Explicitly include hash-bound, manually approved drafts")
    parser.add_argument(
        "--check-readiness",
        action="store_true",
        help="Print AdSense launch-readiness checks without building the site.",
    )
    args = parser.parse_args(argv)

    if args.check_readiness:
        result = inspect_site_readiness(args.config, args.artifacts,
                                        approved_content_root=args.approved_content_root)
    else:
        if args.output is None:
            parser.error("--output is required unless --check-readiness is used")
        result = build_site(args.config, args.artifacts, args.output,
                            approved_content_root=args.approved_content_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
