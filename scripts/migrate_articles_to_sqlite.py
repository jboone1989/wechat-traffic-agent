#!/usr/bin/env python3
"""Migrate Ferro's historical articles.json into its durable SQLite source.

The old JSON is retained as a migration backup; new releases never write it.
No website or Nginx edits, no user-visible content changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_traffic_agent.article_repository import ArticleRepository
from wechat_traffic_agent.site_cli import _load_artifacts
from wechat_traffic_agent.channels.web import _route_for
from wechat_traffic_agent.web_editorial import digest


def migrate(state_root: Path, public_root: Path) -> dict:
    private = Path(state_root) / "content" / "automatic_website"
    legacy = private / "articles.json"
    database = ArticleRepository(private / "articles.sqlite3")
    content_link = Path(public_root) / "content-current"
    site_link = Path(public_root) / "current"
    if not content_link.is_symlink() or not site_link.is_symlink():
        raise RuntimeError("content and website must already have independent public releases")
    if not legacy.is_file():
        if not database.initialized():
            raise FileNotFoundError("neither SQLite nor historic JSON inventory exists")
        artifacts = database.list_artifacts()
        source = "sqlite"
    else:
        artifacts = _load_artifacts(legacy)
        source = "legacy_json"
        if not database.initialized():
            database.initialize(artifacts)

    imported = database.list_artifacts()
    old = {row.artifact_id: row.model_dump(mode="json") for row in artifacts}
    new = {row.artifact_id: row.model_dump(mode="json") for row in imported}
    if old != new:
        raise RuntimeError("SQLite migration content mismatch; do not activate")
    for row in imported:
        route = _route_for(row)
        if not (content_link / route.lstrip("/") / "index.html").is_file():
            raise RuntimeError("public article page missing: " + route)
    with database.connect() as conn:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        topics = conn.execute("SELECT COUNT(*) FROM article_topics").fetchone()[0]
    if check != "ok":
        raise RuntimeError("database integrity_check failed: " + check)
    report = {
        "status": "VERIFIED_SQLITE",
        "database": str(database.path),
        "article_count": len(imported),
        "topic_associations": topics,
        "content_hash": digest(new),
        "source": source,
        "legacy_backup_retained": legacy.is_file(),
        "public_content_release": content_link.resolve().name,
        "website_design_release": site_link.resolve().name,
        "checks": ["sqlite_integrity_ok", "all_article_records_identical", "all_article_urls_rendered"],
    }
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state-root", type=Path, required=True)
    p.add_argument("--public-root", type=Path, required=True)
    opts = p.parse_args()
    print(json.dumps(migrate(opts.state_root, opts.public_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
