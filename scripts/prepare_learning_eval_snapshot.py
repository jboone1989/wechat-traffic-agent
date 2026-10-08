#!/usr/bin/env python3
"""Create a small source-grounded, isolated AWE memory snapshot by explicit IDs.

Does not copy secrets, sessions, all memories or production runtime state.
The caller owns the versioned evaluation dataset and gold responses.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.memory_system import CanonicalMemoryStore

ALLOWED = {"RESEARCH_OBSERVATION", "RESEARCH_UNDERSTANDING", "PROCEDURE"}
FIELDS = (
    "memory_id", "memory_type", "kind", "text", "created_at",
    "source_type", "source_ref", "topics", "evidence_refs",
    "entities", "importance", "confidence", "payload",
)


def prepare(source_root: Path, snapshot_root: Path, ids: list[str]) -> dict:
    source_root, snapshot_root = source_root.resolve(), snapshot_root.resolve()
    if snapshot_root == source_root or source_root in snapshot_root.parents:
        raise ValueError("evaluation snapshot must not be nested under production state")
    if snapshot_root.exists() and list(snapshot_root.iterdir()):
        raise FileExistsError("evaluation snapshot must start empty")
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("provide distinct explicit memory IDs")
    source = CanonicalMemoryStore(source_root)
    rows = []
    for memory_id in ids:
        row = source.get_memory(memory_id)
        if row is None or row["kind"] not in ALLOWED or row["status"] != "ACTIVE":
            raise ValueError("missing, inactive, or out-of-scope memory ID")
        if not row.get("evidence_refs"):
            raise ValueError("evaluation memory must be evidence backed")
        rows.append({field: row[field] for field in FIELDS if field in row})
    snapshot_root.mkdir(parents=True)
    (snapshot_root / AWE_SENTINEL).write_text("isolated memory benchmark\n")
    target = CanonicalMemoryStore(snapshot_root)
    for row in rows:
        target.append_memory(row)
    return {"status": "SEEDED_ISOLATED_SNAPSHOT", "memory_count": len(rows),
            "selected_memory_ids": [row["memory_id"] for row in rows],
            "snapshot_root": str(snapshot_root)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state-root", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--memory-ids-file", type=Path, required=True)
    args = parser.parse_args()
    ids = json.loads(args.memory_ids_file.read_text(encoding="utf-8"))
    if not isinstance(ids, list):
        raise ValueError("memory IDs file must be a JSON array")
    print(json.dumps(prepare(args.source_state_root, args.snapshot_root, ids)))


if __name__ == "__main__":
    main()
