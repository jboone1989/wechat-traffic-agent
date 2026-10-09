#!/usr/bin/env python3
"""Compact the duplicated recent-application feedback cache.

Canonical facts already live in durable memory and original immutable cycle
receipts. Does not modify either source or remove provenance records.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_traffic_agent.learning_application import _feedback_summary


def compact(cache: Path, apply=False):
    old_bytes = cache.read_bytes()
    state = json.loads(old_bytes)
    old = state["history"]
    slim = [_feedback_summary(row) for row in old]
    old_ids = [row["cycle"] for row in old]
    if old_ids != [row["cycle"] for row in slim]:
        raise ValueError("learning application receipt identity changed")
    projected = dict(state)
    projected["history"] = slim
    new_bytes = (json.dumps(projected, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if apply and new_bytes != old_bytes:
        # Only write a cache; durable long-term memory and learning receipts
        # remain completely untouched.
        fd, temp = tempfile.mkstemp(prefix=".applications-", dir=cache.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(new_bytes)
            os.replace(temp, cache)
        finally:
            Path(temp).unlink(missing_ok=True)
    return {
        "status": "APPLIED" if apply else "DRY_RUN",
        "history_count": len(old), "old_bytes": len(old_bytes),
        "new_bytes": len(new_bytes), "bytes_saved": max(0, len(old_bytes)-len(new_bytes)),
        "old_sha256": sha256(old_bytes).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    opts = parser.parse_args()
    p = opts.state_root / "content/learning/applications.json"
    print(json.dumps(compact(p, apply=opts.apply)))


if __name__ == "__main__":
    main()
