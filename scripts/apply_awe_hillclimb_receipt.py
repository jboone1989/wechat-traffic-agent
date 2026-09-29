from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.awe_hillclimb import FerroAWEHillclimb
from wechat_traffic_agent.research_dream import ResearchDreamEngine


def apply_receipt(
    *,
    state_root: Path,
    experiment_id: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    gate = FerroAWEHillclimb(state_root)
    evaluated = gate.ingest_evaluator_receipt(experiment_id, receipt)
    experiment = gate.optimizer_view(experiment_id)

    target = None
    if str(experiment.get("capability_id") or "") == "repair_priority_policy":
        target_ref = str(experiment.get("target_ref") or "")
        if not target_ref:
            raise ValueError("repair-priority AWE experiment has no bound research trial")
        research = ResearchDreamEngine(state_root)
        target = research.apply_awe_gate(
            experiment_id=experiment_id,
            decision=str(evaluated.get("decision") or ""),
        )
        if str(target.get("trial_id") or "") != target_ref:
            raise ValueError("AWE receipt resolved a different research trial than its target_ref")

    return {
        "experiment_id": experiment_id,
        "decision": evaluated.get("decision"),
        "experiment_status": evaluated.get("status"),
        "target": target,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a sanitized AWE hillclimb receipt to Ferro state."
    )
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()

    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("AWE receipt must be a JSON object")

    result = apply_receipt(
        state_root=Path(args.state_root),
        experiment_id=str(args.experiment_id),
        receipt=receipt,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
