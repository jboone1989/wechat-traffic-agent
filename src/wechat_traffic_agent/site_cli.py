"""CLI for building the Content Agent static website channel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .channels.web import (
    StaticWebChannel,
    StaticWebsiteConfig,
    assess_adsense_readiness,
)
from .content_artifact import ContentArtifact


def build_site(
    config_path: Path,
    artifacts_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    config = StaticWebsiteConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    artifacts = _load_artifacts(artifacts_path)
    readiness = assess_adsense_readiness(config, artifacts)
    StaticWebChannel(config).build(artifacts, output_dir)
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "artifact_count": len(artifacts),
        "artifact_ids": [item.artifact_id for item in artifacts],
        "readiness": readiness.model_dump(mode="json"),
    }


def inspect_site_readiness(
    config_path: Path,
    artifacts_path: Path,
) -> dict[str, object]:
    config = StaticWebsiteConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    artifacts = _load_artifacts(artifacts_path)
    return assess_adsense_readiness(config, artifacts).model_dump(mode="json")


def _load_artifacts(path: Path) -> list[ContentArtifact]:
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
    parser.add_argument(
        "--check-readiness",
        action="store_true",
        help="Print AdSense launch-readiness checks without building the site.",
    )
    args = parser.parse_args(argv)

    if args.check_readiness:
        result = inspect_site_readiness(args.config, args.artifacts)
    else:
        if args.output is None:
            parser.error("--output is required unless --check-readiness is used")
        result = build_site(args.config, args.artifacts, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
