#!/usr/bin/env python3
"""Retire completed-cycle input caches and unreferenced outcome snapshots.

Keep durable memories, sources, unfinished work, published receipts, and
anything referenced by a committed ChannelEvidence row. No directory removal.
Dry-run unless --apply. Can run repeatedly and in batches.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path

TERMINAL_RESULTS = {
    "PUBLISHED", "COGNITION_ONLY", "X_EXPRESSION_READY",
    "HELD_BY_CHECKS", "HELD_BY_EVIDENCE_FLOOR",
    "HELD_BY_EXPRESSION_EVIDENCE", "NO_RELEVANT_EVIDENCE",
    "ERROR", "MEMORY_BUDGET_EXHAUSTED", "RECOVERY_INVALIDATED",
    "NO_FRESH_EVIDENCE",
}
SNAPSHOT_KINDS = {"website-engagement-snapshot.v1"}
SNAPSHOT_PREFIXES = (
    "nginx-pageview-snapshot:sha256:",
    "website-engagement-snapshot:sha256:",
    "x-snapshot:sha256:",
    "okx-orbit-snapshot:sha256:",
)


def _source_refs(state_root: Path) -> set[str]:
    out: set[str] = set()
    root = state_root / "content" / "channel_evidence"
    for path in root.rglob("*.json"):
        # Any unreadable evidence means we cannot prove a snapshot unreferenced.
        row = json.loads(path.read_text(encoding="utf-8"))
        for ref in row.get("evidence_refs", ()):
            if not isinstance(ref, str):
                continue
            for prefix in SNAPSHOT_PREFIXES:
                if ref.startswith(prefix):
                    out.add(ref.removeprefix(prefix))
    return out


def prune(state_root: Path, *, now: datetime,
          apply: bool = False, limit: int = 10000,
          min_cycle_age_days: int = 1,
          min_source_age_days: int = 2) -> dict:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    if limit < 1:
        raise ValueError("limit must be positive")
    state_root = Path(state_root)
    cycle_root = state_root / "content/continuous/cycles"
    source_root = state_root / "content/channel_evidence_sources"
    eligible = Counter()
    reclaimed = Counter()
    seen = 0
    # Do not touch a cycle before its terminal outcome is committed. A failed
    # or interrupted invocation can still need exactly the saved input.
    cycle_cutoff = (now - timedelta(days=min_cycle_age_days)).timestamp()
    for folder in cycle_root.iterdir() if cycle_root.exists() else ():
        if seen >= limit:
            break
        if not folder.is_dir() or folder.is_symlink():
            continue
        target = folder / "inputs.json"
        result = folder / "result.json"
        if not target.is_file() or target.is_symlink() or not result.is_file():
            continue
        if (folder / "memory_pending.json").exists() or (folder / "waiting_for_chatgpt.json").exists():
            continue
        if target.stat().st_mtime >= cycle_cutoff or result.stat().st_mtime >= cycle_cutoff:
            continue
        try:
            outcome = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if outcome.get("status") not in TERMINAL_RESULTS:
            continue
        if outcome.get("retryable") is True:
            # An abandoned ERROR may be marked retryable even though run_once
            # already persists result.json and won't reuse the old snapshot.
            # Retain those inputs for 7 days, then allow fresh re-acquisition.
            stale_error = (
                outcome.get("status") == "ERROR"
                and result.stat().st_mtime < (now - timedelta(days=7)).timestamp()
            )
            if not stale_error:
                continue
        size = target.stat().st_size
        eligible["finished_cycle_inputs"] += 1
        reclaimed["finished_cycle_inputs"] += size
        seen += 1
        if apply:
            target.unlink()

    # Only delete derived website engagement metric snapshots that are
    # demonstrably not referred to by any committed channel evidence.
    referenced = _source_refs(state_root)
    source_cutoff = (now - timedelta(days=min_source_age_days)).timestamp()
    if source_root.is_dir():
        with os.scandir(source_root) as entries:
            for entry in entries:
                if seen >= limit:
                    break
                if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                    continue
                if entry.name[:-5] in referenced:
                    continue
                info = entry.stat(follow_symlinks=False)
                if info.st_mtime >= source_cutoff:
                    continue
                # Unknown schema (including potentially unique X evidence)
                # is retained, not guessed to be a disposable observation.
                path = Path(entry.path)
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if row.get("schema_version") not in SNAPSHOT_KINDS:
                    continue
                eligible["unused_website_metric_snapshots"] += 1
                reclaimed["unused_website_metric_snapshots"] += info.st_size
                seen += 1
                if apply:
                    path.unlink()

    return {
        "status": "APPLIED" if apply else "DRY_RUN",
        "file_count": dict(eligible),
        "bytes": dict(reclaimed),
        "total_bytes": sum(reclaimed.values()),
        "kept_channel_evidence_snapshot_refs": len(referenced),
        "cutoffs_days": {"cycle_input": min_cycle_age_days,
                         "unused_source": min_source_age_days},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state-root", type=Path, required=True)
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--min-cycle-age-days", type=int, default=1)
    p.add_argument("--min-source-age-days", type=int, default=2)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    print(json.dumps(prune(
        args.state_root, now=datetime.now(timezone.utc), apply=args.apply,
        limit=args.limit, min_cycle_age_days=args.min_cycle_age_days,
        min_source_age_days=args.min_source_age_days,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
