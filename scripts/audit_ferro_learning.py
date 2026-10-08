#!/usr/bin/env python3
"""Read-only Ferro learning-quality audit, not an activity or memory-size score.

Re-run after new research cycles; compare evidence-backed knowledge,
settled transfer tests, and observed procedure variants. Never count
unresolved predictions or repetitive life logs as learned capability.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3


def _rows(db, kind):
    return db.execute(
        "SELECT text, status, payload_json, evidence_refs_json "
        "FROM memories WHERE kind=?", (kind,),
    ).fetchall()


def _records(db, kind):
    records = _rows(db, kind)
    return {
        "total": len(records),
        "active": sum(row["status"] == "ACTIVE" for row in records),
        "unique_texts": len({row["text"] for row in records}),
        "with_evidence": sum(bool(json.loads(row["evidence_refs_json"] or "[]"))
                             for row in records),
    }


def _understandings(db):
    results = Counter()
    for row in _rows(db, "RESEARCH_UNDERSTANDING"):
        data = json.loads(row["payload_json"] or "{}")
        results[data.get("question_status") or "unknown"] += 1
    return dict(results)


def _applications(db):
    scores = Counter()
    for row in _rows(db, "LEARNING_APPLICATION_OUTCOME"):
        data = json.loads(row["payload_json"] or "{}")
        result = (data.get("assessment") or {}).get("status", "UNKNOWN")
        scores[result] += 1
        if not data.get("evidence"):
            scores["without_new_evidence"] += 1
    return dict(scores)


def _procedures(db):
    active = _rows(db, "PROCEDURE")
    variants = successes = failures = 0
    for row in active:
        if row["status"] != "ACTIVE":
            continue
        payload = json.loads(row["payload_json"] or "{}")
        for item in payload.get("variants") or ():
            variants += 1
            successes += int(item.get("success_count") or 0)
            failures += int(item.get("failure_count") or 0)
    return {"active_variants": variants, "observed_successes": successes,
            "observed_failures": failures}


def _action_transfer(state_root: Path) -> dict:
    """Separate precommitted action choices from independently observed effects."""
    path = state_root / "content/learning/transfer.sqlite3"
    if not path.is_file():
        return {"status": "NO_TRIALS", "measured": 0, "mature_external": 0}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        grouped = {
            arm: {"assigned": n, "verified": scored, "success": successes}
            for arm, n, scored, successes in db.execute(
                "SELECT arm,COUNT(*),COUNT(success),"
                " SUM(CASE WHEN success=1 THEN 1 ELSE 0 END)"
                " FROM trials GROUP BY arm"
            )
        }
        count = db.execute("SELECT COUNT(DISTINCT outcome_id) FROM world_outcomes").fetchone()[0]
        revisions = db.execute("SELECT COUNT(*) FROM world_outcomes").fetchone()[0]
    return {
        "status": "RECORDED",
        "arms": grouped,
        "measured": sum(row["verified"] for row in grouped.values()),
        "mature_external": count,
        "world_outcome_revisions": revisions,
        "causal_improvement_proven": False,
    }


def audit(state_root: Path):
    source = state_root / "application/memory_system/memory.db"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        kinds = {kind: _records(db, kind) for kind in (
            "EPISODE", "EXPERIENCE_KNOWLEDGE", "PROCEDURE",
            "RESEARCH_OBSERVATION", "RESEARCH_UNDERSTANDING",
            "LEARNING_APPLICATION_OUTCOME",
        )}
        return {
            "schema_version": "ferro-learning-quality.v1",
            "source": str(source),
            "memories": kinds,
            "research_question_status": _understandings(db),
            "application_validation": _applications(db),
            "procedure_outcomes": _procedures(db),
            "action_transfer": _action_transfer(state_root),
            "interpretation": (
                "Memory volume and self-reported reflections are not learning. "
                "Only evidence-backed new understanding, changed action and "
                "independently measured outcomes support a capability claim."
            ),
            "causal_improvement_proven": False,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.state_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
