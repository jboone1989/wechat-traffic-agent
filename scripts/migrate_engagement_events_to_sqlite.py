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


def migrate(state_root: Path, *, retire_legacy: bool = False):
    root = Path(state_root) / "content" / "website_engagement"
    old = sorted((root / "events").glob("*/*.json"))
    store = WebsiteEngagementStore(root)
    result = store.migrate_legacy_events()
    path = root / "events.sqlite3"
    with sqlite3.connect(path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        indexed = dict(conn.execute("SELECT event_id, payload FROM events"))
    for original in old:
        event = WebsiteEngagementEvent.model_validate_json(
            original.read_text(encoding="utf-8")
        )
        indexed_event = indexed.get(event.event_id)
        if indexed_event is None or WebsiteEngagementEvent.model_validate_json(indexed_event) != event:
            raise RuntimeError("event migration content mismatch: " + event.event_id)
    if integrity != "ok" or count < len(old):
        raise RuntimeError("engagement migration failed integrity verification")
    if retire_legacy:
        for original in old:
            original.unlink()
    return {
        "status": "VERIFIED_SQLITE",
        "legacy_event_files": len(old),
        "indexed_events": count,
        "integrity": integrity,
        "database": str(path),
        "retired_legacy_event_files": len(old) if retire_legacy else 0,
        **result,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--retire-legacy", action="store_true")
    opts = parser.parse_args()
    print(json.dumps(migrate(opts.state_root, retire_legacy=opts.retire_legacy)))


if __name__ == "__main__":
    main()
