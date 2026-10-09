#!/usr/bin/env python3
"""Remove duplicated neural input from acknowledged events, never queued work.

Completed request-only projections are disposable after the caller's deadline.
Fault receipts retain result, event identity and a checksum of the original
input for provenance. Other event types are left unchanged.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path

TRANSIENT = {
    "BEHAVIORAL_PROJECTION", "PUBLIC_SNAPSHOT", "DREAM_PROJECTION",
    "BRAIN_LAB_PROJECTION", "BRAIN_LAB_LIVE_PROJECTION",
}


def compact(applied_root: Path, *, now: datetime, age_hours: int = 24,
            limit: int = 2000, apply: bool = False):
    cutoff = now - timedelta(hours=age_hours)
    counts = {"compact_fault": 0, "expire_query": 0}
    reclaimed = inspected = 0
    for p in sorted(Path(applied_root).glob("*.json")):
        if sum(counts.values()) >= limit:
            break
        if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) > cutoff:
            continue
        row = json.loads(p.read_text(encoding="utf-8"))
        before_bytes = p.stat().st_size
        kind = row.get("kind")
        if kind == "INTEROCEPTIVE_FAULT" and "payload" in row:
            source = json.dumps(row["payload"], ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")).encode("utf-8")
            compacted = {
                key: row[key] for key in
                ("schema_version", "event_id", "kind", "observed_at", "applied_at", "result")
                if key in row
            }
            compacted["input_sha256"] = sha256(source).hexdigest()
            replacement = json.dumps(
                compacted, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
            after = len(replacement.encode("utf-8"))
            if apply:
                temp = p.with_suffix(".json.compact-tmp")
                try:
                    temp.write_text(replacement, encoding="utf-8")
                    temp.chmod(0o600)
                    os.replace(temp, p)
                finally:
                    temp.unlink(missing_ok=True)
            counts["compact_fault"] += 1
            reclaimed += max(0, before_bytes - after)
        elif kind in TRANSIENT:
            size = p.stat().st_size
            if apply:
                p.unlink()
            counts["expire_query"] += 1
            reclaimed += size
        inspected += 1
    return {"status": "APPLIED" if apply else "DRY_RUN",
            "counts": counts, "estimated_bytes_reclaimed": reclaimed,
            "inspected_eligible": inspected, "age_hours": age_hours,
            "limit": limit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--age-hours", type=int, default=24)
    opts = parser.parse_args()
    root = opts.state_root / "application" / "state_events" / "applied"
    print(json.dumps(compact(root, now=datetime.now(timezone.utc),
                             apply=opts.apply, age_hours=opts.age_hours,
                             limit=opts.limit), sort_keys=True))


if __name__ == "__main__":
    main()
