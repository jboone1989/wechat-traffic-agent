from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_context_builder_effect import PAIRS
from wechat_traffic_agent.decision_context import build_decision_context, candidate

NOW = datetime(2026, 9, 28, 13, 50, tzinfo=timezone.utc)
DEFAULT_SCALES = ((20, 80), (50, 250), (50, 550))


def make_case(spec, variant: str, history_n: int, cross_n: int) -> dict[str, Any]:
    goal_id = f"goal:{spec.pair_id}"
    mission_id = f"mission:{spec.pair_id}"
    expected = spec.action_a if variant == "a" else spec.action_b
    opposite = spec.action_b if variant == "a" else spec.action_a
    fact = spec.fact_a if variant == "a" else spec.fact_b

    current = candidate(
        context_id=f"current:{spec.pair_id}",
        source="goal",
        kind="CURRENT_DECISION",
        payload={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "target": spec.target,
            "question": spec.question,
        },
        text=f"{spec.question} Active goal={goal_id}; mission={mission_id}.",
        importance=0.98,
        confidence=1.0,
        current=True,
        refs=[goal_id, mission_id, spec.target],
    )
    critical = candidate(
        context_id=f"critical:{spec.pair_id}:{variant}",
        source=spec.source,
        kind="LATEST_DECISION_EVIDENCE",
        payload={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "target": spec.target,
            "fact": fact,
            "observed_at": NOW.isoformat(),
            "episode": history_n + 1,
            "supersedes": "prior target history",
        },
        text=(
            f"LATEST VERIFIED episode={history_n + 1} "
            f"observed_at={NOW.isoformat()} {fact} "
            f"This supersedes older TARGET={spec.target} records."
        ),
        importance=0.97,
        confidence=0.99,
        current=spec.source
        in {"internal", "goal", "capability_belief", "commitment", "relationship"},
        refs=[goal_id, mission_id, spec.target],
    )

    rows = [current, critical]
    for index in range(history_n):
        observed_at = NOW - timedelta(hours=history_n - index)
        historical_action = (
            opposite
            if index >= max(0, history_n - 8)
            else (expected if index % 3 == 0 else opposite)
        )
        rows.append(
            candidate(
                context_id=f"stale:{spec.pair_id}:{index:03d}",
                source=spec.source,
                kind="HISTORICAL_EVIDENCE",
                payload={
                    "goal_id": goal_id,
                    "mission_id": mission_id,
                    "target": spec.target,
                    "observed_at": observed_at.isoformat(),
                    "episode": index + 1,
                    "historical_action": historical_action,
                    "stale": True,
                },
                text=(
                    f"STALE historical episode={index + 1} "
                    f"observed_at={observed_at.isoformat()} TARGET={spec.target}. "
                    f"For {spec.options[0]} versus {spec.options[1]}, the old "
                    f"evidence supported {historical_action}. This predates the "
                    "latest verified state."
                ),
                importance=0.97,
                confidence=0.99,
                refs=[goal_id, mission_id, spec.target],
            )
        )

    noise_sources = ("experience", "memory", "belief", "relationship")
    for index in range(cross_n):
        other = f"other_{spec.pair_id}_{index:03d}"
        rows.append(
            candidate(
                context_id=f"noise:{spec.pair_id}:{index:03d}",
                source=noise_sources[index % len(noise_sources)],
                kind="DISTRACTOR",
                payload={"goal_id": f"goal:noise:{index}", "target": other},
                text=(
                    f"TARGET={other} archival evidence discusses "
                    f"{spec.options[0]} versus {spec.options[1]} for a different "
                    "goal. failure success verify resource commitment history."
                ),
                importance=0.95,
                confidence=0.98,
                refs=[f"goal:noise:{index}", other],
            )
        )

    result = build_decision_context(
        prompt=spec.question,
        decision_frame={
            "decision_type": "SELECTION",
            "decision_question": spec.question,
            "surface": "scaled_temporal_eval",
        },
        owner_scope={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "description": spec.question,
            "topic": spec.target,
        },
        missions=[
            {
                "goal_id": goal_id,
                "mission_id": mission_id,
                "description": spec.question,
            }
        ],
        local_candidates=rows,
        state_root=None,
        resource_pressure="NORMAL",
        budget_chars=5200,
        max_facts=12,
        now=NOW,
    )
    selected = set(result["selected_ids"])
    stale_selected = [item for item in selected if item.startswith("stale:")]
    return {
        "case": f"{spec.pair_id}:{variant}",
        "family": spec.family,
        "expected": expected,
        "candidates": len(rows),
        "history": history_n,
        "cross_noise": cross_n,
        "critical_selected": critical["context_id"] in selected,
        "stale_selected": len(stale_selected),
        "selected_count": result["selected_count"],
        "selected_ids": result["selected_ids"],
        "need_coverage": result.get("need_coverage"),
    }


def run(scales=DEFAULT_SCALES) -> dict[str, Any]:
    rows = [
        make_case(spec, variant, history, cross)
        for history, cross in scales
        for spec in PAIRS
        for variant in ("a", "b")
    ]
    summary: dict[str, Any] = {}
    for scale in sorted({row["candidates"] for row in rows}):
        subset = [row for row in rows if row["candidates"] == scale]
        summary[str(scale)] = {
            "cases": len(subset),
            "critical_recall": sum(row["critical_selected"] for row in subset)
            / len(subset),
            "avg_selected": sum(row["selected_count"] for row in subset)
            / len(subset),
            "cases_with_stale_selected": sum(
                bool(row["stale_selected"]) for row in subset
            ),
            "avg_stale_selected": sum(row["stale_selected"] for row in subset)
            / len(subset),
        }
    bad = [
        row
        for row in rows
        if not row["critical_selected"] or bool(row["stale_selected"])
    ]
    return {"summary": summary, "bad_cases": bad}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="/tmp/ferro-context-scaled-temporal-structural.json",
    )
    parser.add_argument(
        "--assert-clean",
        action="store_true",
        help="exit nonzero unless every scale has full critical recall and no stale selection",
    )
    args = parser.parse_args()

    report = run()
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    concise = {
        "summary": report["summary"],
        "bad_case_count": len(report["bad_cases"]),
        "sample_bad": report["bad_cases"][:8],
        "output": args.output,
    }
    print(json.dumps(concise, ensure_ascii=False, indent=2))
    if args.assert_clean and report["bad_cases"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
