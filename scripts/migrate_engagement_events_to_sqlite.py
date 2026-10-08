#!/usr/bin/env python3
"""Migrate first-party engagement events into their indexed SQLite store."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_traffic_agent.website_engagement import (
    WebsiteEngagementStore, WebsiteEngagementEvent,
)


def migrate(state_root: Path):
    root = Path(state_root) / "content" / "website_engagement"
    old = sorted((root / "events").glob("*/*.json"))
    store = WebsiteEngagementStore(root)
    result = store.migrate_legacy_events()
    path = root / "events.sqlite3"
    with sqlite3.connect(path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        indexed = {
            key for (key,) in conn.execute("SELECT event_id FROM events").fetchall()
        }
    for original in old:
        event = WebsiteEngagementEvent.model_validate_json(
            original.read_text(encoding="utf-8")
        )
        if event.event_id not in indexed:
            raise RuntimeError("event migration lost " + event.event_id)
    if integrity != "ok" or count < len(old):
        raise RuntimeError("engagement migration failed integrity verification")
    return {
        "status": "VERIFIED_SQLITE",
        "legacy_event_files": len(old),
        "indexed_events": count,
        "integrity": integrity,
        "database": str(path),
        "legacy_event_files_preserved": True,
        **result,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    opts = parser.parse_args()
    print(json.dumps(migrate(opts.state_root)))


if __name__ == "__main__":
    main()
