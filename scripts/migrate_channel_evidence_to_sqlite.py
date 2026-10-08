#!/usr/bin/env python3
"""Verify and migrate every versioned channel observation into SQLite."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_traffic_agent.channel_evidence import ChannelEvidenceStore


def migrate(state_root: Path):
    root = Path(state_root) / "content" / "channel_evidence"
    old_count = sum(1 for _ in root.glob("*/*.json")) if root.exists() else 0
    store = ChannelEvidenceStore(root)
    result = store.migrate_legacy()
    with sqlite3.connect(root / "evidence.sqlite3") as conn:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        row_count = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        revisions = conn.execute("SELECT COUNT(DISTINCT identity_hash) FROM evidence").fetchone()[0]
    if check != "ok" or row_count < old_count:
        raise RuntimeError("channel evidence migration integrity mismatch")
    report = {
        "status": "VERIFIED_SQLITE",
        "legacy_files": old_count,
        "indexed_observations": row_count,
        "distinct_series": revisions,
        "source_references": len(store.all_evidence_refs()),
        "sqlite_integrity": check,
        "database": str(root / "evidence.sqlite3"),
        "legacy_receipt_files_preserved": True,
    }
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(migrate(args.state_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
