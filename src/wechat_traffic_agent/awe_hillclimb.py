"""AWE-backed mutation experiments for Ferro.

This bridge does not own evaluator cases. In particular, hidden AWE cases and
their traces stay outside Ferro's state tree. The evaluator may submit only a
sanitized aggregate hidden result plus train-visible failure details.

One experiment represents one bounded mutation. AWE is the selection authority:
PROMOTE permits adoption; any rollback decision is explicit counterevidence;
infrastructure-invalid runs remain unevaluated rather than becoming capability
failures.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "ferro-awe-hillclimb.v1"
MAX_EXPERIMENTS = 128

_ALLOWED_HIDDEN_KEYS = frozenset({
    "baseline_mean",
    "candidate_mean",
    "gain",
    "noise_floor",
    "n_baseline",
    "n_candidate",
    "infrastructure_error_count",
})


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _utc(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWE hillclimb timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _experiment_id(capability_id: str, baseline_ref: str, mutation_ref: str) -> str:
    digest = sha256(
        f"{capability_id}\0{baseline_ref}\0{mutation_ref}".encode("utf-8")
    ).hexdigest()[:24]
    return f"awe-hillclimb:{digest}"


def _clean_hidden(raw: Mapping[str, Any]) -> dict[str, Any]:
    extra = sorted(set(raw) - _ALLOWED_HIDDEN_KEYS)
    if extra:
        raise ValueError(
            "hidden AWE receipt attempted to cross evaluator boundary: "
            + ", ".join(extra)
        )
    return {key: raw.get(key) for key in _ALLOWED_HIDDEN_KEYS if key in raw}


class FerroAWEHillclimb:
    """Persist one-mutation AWE experiments without importing hidden cases."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "awe_hillclimb.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "updated_at": None,
            "experiments": {},
            "promotion_count": 0,
            "rollback_count": 0,
            "infrastructure_invalid_count": 0,
        }

    def start_experiment(
        self,
        *,
        capability_id: str,
        hypothesis: str,
        baseline_ref: str,
        mutation_ref: str,
        mutation_kind: str,
        target_domains: tuple[str, ...] = (),
        source_ref: str | None = None,
        target_ref: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _utc(now)
        capability_id = str(capability_id).strip()
        hypothesis = str(hypothesis).strip()
        baseline_ref = str(baseline_ref).strip()
        mutation_ref = str(mutation_ref).strip()
        mutation_kind = str(mutation_kind).strip()
        if not all((capability_id, hypothesis, baseline_ref, mutation_ref, mutation_kind)):
            raise ValueError("AWE experiment requires capability, hypothesis, baseline, mutation, and mutation kind")
        if baseline_ref == mutation_ref:
            raise ValueError("AWE mutation must differ from the baseline")

        experiment_id = _experiment_id(capability_id, baseline_ref, mutation_ref)
        existing = (self.state.get("experiments") or {}).get(experiment_id)
        if isinstance(existing, Mapping):
            return dict(existing)

        row = {
            "experiment_id": experiment_id,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "status": "AWAITING_AWE",
            "capability_id": capability_id,
            "hypothesis": hypothesis,
            "baseline_ref": baseline_ref,
            "mutation_ref": mutation_ref,
            "mutation_kind": mutation_kind,
            "mutation_count": 1,
            "source_ref": source_ref,
            "target_ref": target_ref,
            "target_domains": [str(value) for value in target_domains if str(value)],
            "train_visibility": "FAILURE_DETAILS_ALLOWED",
            "hidden_visibility": "AGGREGATE_ONLY",
            "hidden_cases_stored_in_ferro": False,
            "promotion_authority": "AWE_HILLCLIMB_GATE",
            "evaluator_receipt": None,
            "decision": None,
        }
        experiments = dict(self.state.get("experiments") or {})
        experiments[experiment_id] = row
        if len(experiments) > MAX_EXPERIMENTS:
            ordered = sorted(
                experiments.values(),
                key=lambda item: str(item.get("created_at") or ""),
            )[-MAX_EXPERIMENTS:]
            experiments = {str(item["experiment_id"]): item for item in ordered}
        self.state["experiments"] = experiments
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return dict(row)

    def observe_research_dream(
        self,
        research_result: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        dream = (
            research_result.get("dream")
            if isinstance(research_result.get("dream"), Mapping)
            else {}
        )
        if str(dream.get("decision") or "") != "START_RESEARCH_POLICY_TRIAL":
            return None
        incumbent = dream.get("incumbent") if isinstance(dream.get("incumbent"), Mapping) else {}
        winner = dream.get("winner") if isinstance(dream.get("winner"), Mapping) else {}
        baseline_ref = str(incumbent.get("policy_id") or "").strip()
        mutation_ref = str(winner.get("policy_id") or "").strip()
        trial_id = str(dream.get("prospective_trial_id") or "").strip()
        if not baseline_ref or not mutation_ref or baseline_ref == mutation_ref:
            return None
        if not trial_id:
            raise ValueError("research-policy mutation has no prospective trial id")
        return self.start_experiment(
            capability_id="repair_priority_policy",
            hypothesis=(
                "The single research-policy weight mutation selected by offline replay "
                "should improve repair prioritization and transfer to unseen AWE agency cases."
            ),
            baseline_ref=baseline_ref,
            mutation_ref=mutation_ref,
            mutation_kind="RESEARCH_POLICY_WEIGHT_MUTATION",
            target_domains=("recovery", "learning", "efficiency", "goal_pursuit"),
            source_ref=str(dream.get("dream_id") or "") or None,
            target_ref=trial_id,
            now=now,
        )


    def ensure_research_trial_experiment(
        self,
        trial: Mapping[str, Any],
        *,
        source_ref: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        trial_id = str(trial.get("trial_id") or "").strip()
        baseline = (
            trial.get("baseline_policy")
            if isinstance(trial.get("baseline_policy"), Mapping)
            else {}
        )
        challenger = (
            trial.get("challenger_policy")
            if isinstance(trial.get("challenger_policy"), Mapping)
            else {}
        )
        baseline_ref = str(baseline.get("policy_id") or "").strip()
        mutation_ref = str(challenger.get("policy_id") or "").strip()
        if not trial_id or not baseline_ref or not mutation_ref:
            raise ValueError("open research trial lacks AWE experiment provenance")
        if baseline_ref == mutation_ref:
            raise ValueError("research trial challenger is identical to baseline")
        return self.start_experiment(
            capability_id="repair_priority_policy",
            hypothesis=(
                "The single research-policy weight mutation should improve repair "
                "prioritization online and generalize to evaluator-hidden cases."
            ),
            baseline_ref=baseline_ref,
            mutation_ref=mutation_ref,
            mutation_kind="RESEARCH_POLICY_WEIGHT_MUTATION",
            target_domains=("recovery", "learning", "efficiency", "goal_pursuit"),
            source_ref=source_ref,
            target_ref=trial_id,
            now=now,
        )

    def ingest_evaluator_receipt(
        self,
        experiment_id: str,
        receipt: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _utc(now)
        experiments = dict(self.state.get("experiments") or {})
        if experiment_id not in experiments:
            raise KeyError(f"unknown AWE hillclimb experiment: {experiment_id}")
        decision = str(receipt.get("decision") or "").strip().upper()
        if not decision:
            raise ValueError("AWE evaluator receipt requires decision")

        train = receipt.get("train") if isinstance(receipt.get("train"), Mapping) else {}
        hidden_raw = receipt.get("hidden") if isinstance(receipt.get("hidden"), Mapping) else {}
        hidden = _clean_hidden(hidden_raw)
        regression = (
            dict(receipt.get("regression"))
            if isinstance(receipt.get("regression"), Mapping)
            else None
        )
        sanitized = {
            "decision": decision,
            "reason": str(receipt.get("reason") or "")[:1000],
            "train": dict(train),
            "hidden": hidden,
            "regression": regression,
        }

        row = dict(experiments[experiment_id])
        prior_status = str(row.get("status") or "")
        if prior_status in {"PROMOTION_APPROVED", "ROLLBACK_REQUIRED"}:
            if row.get("evaluator_receipt") == sanitized:
                return row
            raise ValueError("terminal AWE experiment cannot be overwritten by a different receipt")

        if decision == "PROMOTE":
            status = "PROMOTION_APPROVED"
            self.state["promotion_count"] = int(self.state.get("promotion_count") or 0) + 1
        elif decision.startswith("ROLLBACK"):
            status = "ROLLBACK_REQUIRED"
            self.state["rollback_count"] = int(self.state.get("rollback_count") or 0) + 1
        elif decision == "INVALID_INFRASTRUCTURE":
            status = "EVAL_INFRASTRUCTURE_INVALID"
            self.state["infrastructure_invalid_count"] = int(
                self.state.get("infrastructure_invalid_count") or 0
            ) + 1
        elif decision.startswith("WAIT"):
            status = "AWAITING_AWE"
        else:
            raise ValueError(f"unsupported AWE hillclimb decision: {decision}")

        row["status"] = status
        row["decision"] = decision
        row["updated_at"] = now.isoformat()
        row["evaluator_receipt"] = sanitized
        experiments[experiment_id] = row
        self.state["experiments"] = experiments
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return dict(row)

    def optimizer_view(self, experiment_id: str) -> dict[str, Any]:
        row = (self.state.get("experiments") or {}).get(experiment_id)
        if not isinstance(row, Mapping):
            raise KeyError(f"unknown AWE hillclimb experiment: {experiment_id}")
        value = dict(row)
        receipt = value.get("evaluator_receipt")
        if isinstance(receipt, Mapping):
            sanitized = dict(receipt)
            if isinstance(sanitized.get("hidden"), Mapping):
                sanitized["hidden"] = _clean_hidden(sanitized["hidden"])
            value["evaluator_receipt"] = sanitized
        return value

    def projection(self) -> dict[str, Any]:
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        experiments = [
            dict(value)
            for value in (self.state.get("experiments") or {}).values()
            if isinstance(value, Mapping)
        ]
        experiments.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "active_count": sum(
                1 for row in experiments
                if row.get("status") in {"AWAITING_AWE", "EVAL_INFRASTRUCTURE_INVALID"}
            ),
            "promotion_count": int(self.state.get("promotion_count") or 0),
            "rollback_count": int(self.state.get("rollback_count") or 0),
            "infrastructure_invalid_count": int(
                self.state.get("infrastructure_invalid_count") or 0
            ),
            "recent_experiments": experiments[:16],
            "hidden_case_storage": "EVALUATOR_ONLY",
        }


__all__ = ["FerroAWEHillclimb", "SCHEMA"]
