#!/usr/bin/env python3
"""Retire invalid historical 'learned outcome' projections without deleting evidence.

Unresolved transfer attempts belong in short task feedback, not active
knowledge retrieval. Canonical history, sources and application receipts
remain intact. Dry-run unless --apply.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def candidates(state_root: Path):
    path = state_root / "application/memory_system/memory.db"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT memory_id,payload_json FROM memories "
            "WHERE kind='LEARNING_APPLICATION_OUTCOME' AND status='ACTIVE' "
            "AND source_type='APPLICATION_RECEIPT'"
        ).fetchall()
    return [id for id, payload in rows
            if (json.loads(payload or "{}").get("assessment") or {}).get("status")
            == "UNRESOLVED"]


def archive(state_root: Path, *, apply=False, limit=30):
    ids = candidates(state_root)[:max(1, limit)]
    if apply and ids:
        from wechat_traffic_agent.memory_service import MemoryService
        service = MemoryService(state_root)
        for memory_id in ids:
            service.store.transition_memory(
                memory_id, status="ARCHIVED", event_type="MEMORY_RECLASSIFIED",
            )
        service.sync()
    return {
        "status": "APPLIED" if apply else "DRY_RUN",
        "unresolved_archived": len(ids),
        "canonically_deleted": 0,
        "verified_results_preserved": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    opts = ap.parse_args()
    print(json.dumps(archive(opts.state_root, apply=opts.apply, limit=opts.limit)))


if __name__ == "__main__":
    main()
