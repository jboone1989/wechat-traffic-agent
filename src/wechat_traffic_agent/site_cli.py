"""CLI for building the Content Agent static website channel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .channels.web import StaticWebChannel, StaticWebsiteConfig
from .content_artifact import ContentArtifact


def build_site(config_path: Path, artifacts_path: Path, output_dir: Path) -> dict[str, object]:
    config = StaticWebsiteConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    files = _artifact_files(artifacts_path)
    artifacts = [
        ContentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        for path in files
    ]
    StaticWebChannel(config).build(artifacts, output_dir)
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "artifact_count": len(artifacts),
        "artifact_ids": [item.artifact_id for item in artifacts],
    }


def _artifact_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"artifact path does not exist: {path}")
    return sorted(item for item in path.glob("*.json") if item.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Content Agent static website")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = build_site(args.config, args.artifacts, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
