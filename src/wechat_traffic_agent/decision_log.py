"""Append-only decision log for the genre pipeline (learning-loop input data).

The JSONL file is single-writer (one process appends per post); readers tolerate corrupt lines.
"""
from __future__ import annotations

import json
from pathlib import Path

DISCARD_STREAK_ALERT = 3


def decision_log_path(state_root) -> Path:
    return Path(state_root) / "square_editorial" / "genre_decision_log.jsonl"


def append_decision(state_root, record) -> dict:
    path = decision_log_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def recent_decisions(state_root, *, limit=10) -> list[dict]:
    # Reads the whole file before taking the tail - acceptable at expected volumes
    # (hundreds of rows/day; the learner reads the full file anyway).
    try:
        lines = decision_log_path(state_root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def discard_streak(state_root) -> int:
    streak = 0
    for row in reversed(recent_decisions(state_root)):
        if (row.get("review") or {}).get("verdict") != "discarded":
            break
        streak += 1
    return streak
