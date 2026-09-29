"""AWE-gated owner-goal scheduler with prospective real-outcome settlement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .awe_hillclimb import FerroAWEHillclimb


SCHEMA = "ferro-goal-scheduler-evolution.v1"
POLICY_SCHEMA = "ferro-goal-scheduler-policy.v1"
DEFAULT_PRIORITY_BONUS_SECONDS = 300.0
MIN_PRIORITY_BONUS_SECONDS = 0.0
MAX_PRIORITY_BONUS_SECONDS = 900.0
MUTATION_STEP_SECONDS = 120.0
MIN_HISTORY = 8
MIN_GOAL_OUTCOMES = 2
MIN_GAIN = 0.02
MIN_PROSPECTIVE_PER_ARM = 3
MAX_PROSPECTIVE_PER_ARM = 8
PROSPECTIVE_WINDOW = timedelta(days=7)
MAX_DECISIONS = 512


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("goal scheduler timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _read(path: Path) -> dict[str, Any]:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return row if isinstance(row, dict) else {}


def _write(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _bounded(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("goal scheduler bonus must be finite")
    if not MIN_PRIORITY_BONUS_SECONDS <= value <= MAX_PRIORITY_BONUS_SECONDS:
        raise ValueError("goal scheduler bonus outside bounded range")
    return value


def _policy_ref(value: float) -> str:
    return "goal.scheduler.priority-bonus:" + sha256(
        f"{_bounded(value):.6f}".encode("utf-8")
    ).hexdigest()[:20]


def _mutations(value: float) -> tuple[float, ...]:
    value = _bounded(value)
    options = {
        max(MIN_PRIORITY_BONUS_SECONDS, value - MUTATION_STEP_SECONDS),
        min(MAX_PRIORITY_BONUS_SECONDS, value + MUTATION_STEP_SECONDS),
    }
    return tuple(sorted(item for item in options if abs(item - value) > 1e-12))


def _rank(candidates: Sequence[Mapping[str, Any]], bonus_seconds: float) -> dict[str, Any]:
    if not candidates:
        raise ValueError("goal scheduler requires at least one candidate")
    bonus_seconds = _bounded(bonus_seconds)
    ranked = sorted(
        (dict(row) for row in candidates),
        key=lambda row: (
            float(row.get("age_seconds") or 0.0)
            + max(0.0, min(1.0, float(row.get("priority") or 0.0))) * bonus_seconds,
            float(row.get("priority") or 0.0),
            str(row.get("created_at") or ""),
        ),
        reverse=True,
    )
    return ranked[0]


def _world_utility(value: Any, depth: int = 0) -> float | None:
    if depth > 5 or not isinstance(value, Mapping):
        return None
    direct = value.get("world_utility")
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return max(-1.0, min(1.0, float(direct)))
    for nested in value.values():
        if isinstance(nested, Mapping):
            found = _world_utility(nested, depth + 1)
            if found is not None:
                return found
    return None


def _score_outcome(outcome: Mapping[str, Any]) -> dict[str, Any]:
    status = str(outcome.get("status") or "UNKNOWN").upper()
    utility = _world_utility(outcome)
    if status in {
        "ERROR", "FAILED", "DEGRADED", "CHATGPT_UNAVAILABLE",
        "OWNER_COGNITION_PROVIDER_UNAVAILABLE", "WAITING_FOR_CHATGPT",
    }:
        return {
            "valid": False,
            "score": None,
            "reason": "execution_or_infrastructure_failure",
            "status": status,
            "world_utility": utility,
        }
    if status in {"WAIT_SCHEDULED", "OWNER_AGENT_WAITING"}:
        return {
            "valid": False,
            "score": None,
            "reason": "time_dependent_wait_has_no_goal_effect_yet",
            "status": status,
            "world_utility": utility,
        }
    if utility is not None:
        return {
            "valid": True,
            "score": round((utility + 1.0) / 2.0, 6),
            "reason": "explicit_world_utility",
            "status": status,
            "world_utility": utility,
        }
    if status in {"PUBLISHED", "POSTED", "SATISFIED"}:
        return {
            "valid": True,
            "score": 1.0,
            "reason": "material_world_state_changed",
            "status": status,
            "world_utility": None,
        }
    if status in {"COGNITION_ONLY", "NO_MATERIAL_CHANGE", "UNRESOLVED_ACTION_REQUIRED"}:
        return {
            "valid": True,
            "score": 0.0,
            "reason": "executed_without_goal_relevant_state_change",
            "status": status,
            "world_utility": None,
        }
    return {
        "valid": False,
        "score": None,
        "reason": "no_scheduler_attributable_goal_effect",
        "status": status,
        "world_utility": None,
    }


class GoalSchedulerEvolution:
    """Hillclimb only priority-vs-age weighting; owner authority is immutable."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "goal_scheduler"
        self.state_path = self.root / "evolution.json"
        self.active_policy_path = self.root / "active_policy.json"
        self.awe = FerroAWEHillclimb(self.state_root)

    def _state(self) -> dict[str, Any]:
        row = _read(self.state_path)
        if row.get("schema_version") != SCHEMA:
            return {"schema_version": SCHEMA, "decisions": [], "trial": None, "updated_at": None}
        row.setdefault("decisions", [])
        row.setdefault("trial", None)
        return row

    def _save(self, state: Mapping[str, Any]) -> None:
        payload = dict(state)
        payload["schema_version"] = SCHEMA
        payload["decisions"] = list(payload.get("decisions") or [])[-MAX_DECISIONS:]
        _write(self.state_path, payload)

    def active_bonus_seconds(self) -> float:
        row = _read(self.active_policy_path)
        if (
            row.get("schema_version") == POLICY_SCHEMA
            and row.get("status") == "PROMOTED"
            and row.get("capability_version_id")
        ):
            return _bounded(float(row.get("priority_bonus_seconds")))
        return DEFAULT_PRIORITY_BONUS_SECONDS

    @staticmethod
    def _goal_means(decisions: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        buckets: dict[str, list[float]] = {}
        for row in decisions:
            outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
            if not outcome.get("valid") or outcome.get("score") is None:
                continue
            goal_id = str(row.get("selected_goal_id") or "")
            if goal_id:
                buckets.setdefault(goal_id, []).append(float(outcome["score"]))
        return {
            key: sum(values) / len(values)
            for key, values in buckets.items()
            if len(values) >= MIN_GOAL_OUTCOMES
        }

    @staticmethod
    def _train_utility(
        decisions: Sequence[Mapping[str, Any]],
        *,
        bonus_seconds: float,
        goal_means: Mapping[str, float],
    ) -> tuple[float | None, int]:
        values: list[float] = []
        for row in decisions:
            candidates = [
                dict(item) for item in row.get("candidates") or ()
                if isinstance(item, Mapping)
            ]
            if len(candidates) < 2:
                continue
            selected = _rank(candidates, bonus_seconds)
            goal_id = str(selected.get("goal_id") or "")
            if goal_id in goal_means:
                values.append(float(goal_means[goal_id]))
        return ((sum(values) / len(values), len(values)) if values else (None, 0))

    def _maybe_start_trial(self, state: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        trial = state.get("trial")
        if isinstance(trial, Mapping):
            trial_status = str(trial.get("status") or "")
            if trial_status == "AWAITING_PROSPECTIVE":
                return state
            if trial_status in {"PROMOTION_APPROVED", "ROLLBACK_REQUIRED"}:
                completed_raw = str(trial.get("completed_at") or "")
                try:
                    completed_at = datetime.fromisoformat(
                        completed_raw.replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                except ValueError:
                    completed_at = now
                has_new_evidence = any(
                    isinstance(row, Mapping)
                    and isinstance(row.get("outcome"), Mapping)
                    and row["outcome"].get("valid")
                    and str(row.get("settled_at") or "")
                    and datetime.fromisoformat(
                        str(row["settled_at"]).replace("Z", "+00:00")
                    ).astimezone(timezone.utc) > completed_at
                    for row in state.get("decisions") or ()
                )
                if not has_new_evidence:
                    return state
                state["trial"] = None
        decisions = [
            dict(row) for row in state.get("decisions") or ()
            if isinstance(row, Mapping)
            and isinstance(row.get("outcome"), Mapping)
            and row["outcome"].get("valid")
        ]
        if len(decisions) < MIN_HISTORY:
            state["status"] = "INSUFFICIENT_SETTLED_GOAL_EFFECT_EVIDENCE"
            return state
        goal_means = self._goal_means(decisions)
        if len(goal_means) < 2:
            state["status"] = "INSUFFICIENT_CROSS_GOAL_OUTCOME_EVIDENCE"
            return state
        baseline = self.active_bonus_seconds()
        baseline_mean, baseline_cases = self._train_utility(
            decisions, bonus_seconds=baseline, goal_means=goal_means
        )
        if baseline_mean is None:
            state["status"] = "INSUFFICIENT_COUNTERFACTUAL_TRAIN_CASES"
            return state
        candidates: list[tuple[float, float, int]] = []
        for mutation in _mutations(baseline):
            mean, cases = self._train_utility(
                decisions, bonus_seconds=mutation, goal_means=goal_means
            )
            if mean is not None:
                candidates.append((mean, mutation, cases))
        if not candidates:
            state["status"] = "NO_TRAIN_CHALLENGER"
            return state
        candidates.sort(reverse=True)
        candidate_mean, mutation, candidate_cases = candidates[0]
        gain = candidate_mean - baseline_mean
        if gain < MIN_GAIN:
            state["status"] = "HEALTHY_NO_MUTATION"
            state["train"] = {
                "baseline_mean": round(baseline_mean, 6),
                "candidate_mean": round(candidate_mean, 6),
                "gain": round(gain, 6),
                "case_count": min(baseline_cases, candidate_cases),
            }
            return state
        attempt_ref = sha256(
            (
                f"{baseline:.6f}|{mutation:.6f}|"
                + "|".join(str(row.get("decision_id") or "") for row in decisions[-64:])
            ).encode("utf-8")
        ).hexdigest()
        awe = self.awe.start_experiment(
            capability_id="owner_goal_scheduler",
            hypothesis=(
                "Changing only priority_bonus_seconds should improve realized goal-effect "
                "utility without changing owner authority, pending continuation, or starvation bounds."
            ),
            baseline_ref=_policy_ref(baseline),
            mutation_ref=_policy_ref(mutation),
            mutation_kind="GOAL_SCHEDULER_PRIORITY_BONUS_CHANGE",
            target_domains=("owner_goal_progress", "goal_effect", "scheduler_fairness"),
            source_ref="goal-scheduler-settled-history",
            target_ref="wakeful.owner_goal_scheduler",
            attempt_ref=attempt_ref,
            now=now,
        )
        state["trial"] = {
            "status": "AWAITING_PROSPECTIVE",
            "awe_experiment_id": awe["experiment_id"],
            "baseline_bonus_seconds": baseline,
            "challenger_bonus_seconds": mutation,
            "frozen_at": now.isoformat(),
            "expires_at": (now + PROSPECTIVE_WINDOW).isoformat(),
            "train": {
                "baseline_mean": round(baseline_mean, 6),
                "candidate_mean": round(candidate_mean, 6),
                "gain": round(gain, 6),
                "case_count": min(baseline_cases, candidate_cases),
            },
        }
        state["status"] = "AWAITING_PROSPECTIVE"
        return state

    @staticmethod
    def _arm_counts(decisions: Sequence[Mapping[str, Any]], experiment_id: str) -> dict[str, int]:
        counts = {"BASELINE": 0, "CANDIDATE": 0}
        for row in decisions:
            if str(row.get("awe_experiment_id") or "") != experiment_id:
                continue
            arm = str(row.get("arm") or "")
            if arm in counts:
                counts[arm] += 1
        return counts

    def _assign_arm(self, state: Mapping[str, Any], signature: str) -> str:
        trial = state.get("trial") if isinstance(state.get("trial"), Mapping) else {}
        experiment_id = str(trial.get("awe_experiment_id") or "")
        counts = self._arm_counts(state.get("decisions") or (), experiment_id)
        if counts["BASELINE"] < counts["CANDIDATE"]:
            return "BASELINE"
        if counts["CANDIDATE"] < counts["BASELINE"]:
            return "CANDIDATE"
        bit = int(sha256((experiment_id + "|" + signature).encode()).hexdigest()[:8], 16)
        return "CANDIDATE" if bit % 2 else "BASELINE"

    def select(self, candidates: Sequence[Mapping[str, Any]], *, now: datetime) -> dict[str, Any]:
        now = _utc(now)
        state = self._maybe_start_trial(self._state(), now=now)
        trial = state.get("trial") if isinstance(state.get("trial"), Mapping) else None
        baseline = self.active_bonus_seconds()
        signature = sha256(
            json.dumps(
                [
                    {
                        "goal_id": str(row.get("goal_id") or ""),
                        "priority": float(row.get("priority") or 0.0),
                        "age_seconds": round(float(row.get("age_seconds") or 0.0), 3),
                        "created_at": str(row.get("created_at") or ""),
                    }
                    for row in candidates
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:24]
        arm = "ACTIVE"
        bonus = baseline
        awe_experiment_id = None
        if trial and str(trial.get("status") or "") == "AWAITING_PROSPECTIVE":
            baseline_choice = _rank(
                candidates, float(trial["baseline_bonus_seconds"])
            )
            candidate_choice = _rank(
                candidates, float(trial["challenger_bonus_seconds"])
            )
            if (
                len(candidates) >= 2
                and str(baseline_choice.get("goal_id") or "")
                != str(candidate_choice.get("goal_id") or "")
            ):
                awe_experiment_id = str(trial.get("awe_experiment_id") or "")
                arm = self._assign_arm(state, signature)
                bonus = (
                    float(trial["challenger_bonus_seconds"])
                    if arm == "CANDIDATE"
                    else float(trial["baseline_bonus_seconds"])
                )
        selected = _rank(candidates, bonus)
        decision_id = "goal-scheduler:" + sha256(
            f"{now.isoformat()}|{signature}|{arm}|{bonus:.6f}".encode()
        ).hexdigest()[:24]
        decisions = [
            dict(row) for row in state.get("decisions") or ()
            if isinstance(row, Mapping)
        ]
        if not any(str(row.get("decision_id") or "") == decision_id for row in decisions):
            decisions.append({
                "decision_id": decision_id,
                "selected_at": now.isoformat(),
                "arm": arm,
                "awe_experiment_id": awe_experiment_id,
                "priority_bonus_seconds": bonus,
                "active_policy_ref": _policy_ref(baseline),
                "selected_goal_id": str(selected.get("goal_id") or ""),
                "candidates": [
                    {
                        "goal_id": str(row.get("goal_id") or ""),
                        "priority": float(row.get("priority") or 0.0),
                        "age_seconds": round(float(row.get("age_seconds") or 0.0), 6),
                        "created_at": str(row.get("created_at") or ""),
                    }
                    for row in candidates
                ],
                "outcome": None,
                "settled_at": None,
            })
        state["decisions"] = decisions[-MAX_DECISIONS:]
        state["updated_at"] = now.isoformat()
        self._save(state)
        return {
            "goal_id": str(selected.get("goal_id") or ""),
            "decision_id": decision_id,
            "arm": arm,
            "priority_bonus_seconds": bonus,
            "awe_experiment_id": awe_experiment_id,
            "candidate_count": len(candidates),
            "policy_ref": _policy_ref(bonus),
        }

    def settle(self, decision_id: str, *, outcome: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        now = _utc(now)
        state = self._state()
        decisions = [
            dict(row) for row in state.get("decisions") or ()
            if isinstance(row, Mapping)
        ]
        index = next(
            (i for i, row in enumerate(decisions)
             if str(row.get("decision_id") or "") == str(decision_id or "")),
            None,
        )
        if index is None:
            return {"status": "UNKNOWN_DECISION", "decision_id": str(decision_id or "")}
        if isinstance(decisions[index].get("outcome"), Mapping):
            return {
                "status": "ALREADY_SETTLED",
                "decision_id": str(decision_id),
                "outcome": dict(decisions[index]["outcome"]),
            }
        decisions[index]["outcome"] = _score_outcome(outcome)
        decisions[index]["settled_at"] = now.isoformat()
        state["decisions"] = decisions
        trial = state.get("trial") if isinstance(state.get("trial"), Mapping) else None
        gate = None
        if trial and str(trial.get("status") or "") == "AWAITING_PROSPECTIVE":
            experiment_id = str(trial.get("awe_experiment_id") or "")
            scores: dict[str, list[float]] = {"BASELINE": [], "CANDIDATE": []}
            infra_errors = 0
            for row in decisions:
                if str(row.get("awe_experiment_id") or "") != experiment_id:
                    continue
                arm = str(row.get("arm") or "")
                observed = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
                if (
                    arm in scores and observed.get("valid")
                    and observed.get("score") is not None
                ):
                    scores[arm].append(float(observed["score"]))
                elif observed.get("reason") == "execution_or_infrastructure_failure":
                    infra_errors += 1
            nb = len(scores["BASELINE"])
            nc = len(scores["CANDIDATE"])
            expires = datetime.fromisoformat(
                str(trial["expires_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            enough = (
                nb >= MIN_PROSPECTIVE_PER_ARM
                and nc >= MIN_PROSPECTIVE_PER_ARM
            )
            if enough:
                baseline_mean = sum(scores["BASELINE"]) / nb
                candidate_mean = sum(scores["CANDIDATE"]) / nc
                gain = candidate_mean - baseline_mean
                saturated = (
                    nb >= MAX_PROSPECTIVE_PER_ARM
                    and nc >= MAX_PROSPECTIVE_PER_ARM
                )
                if gain >= MIN_GAIN:
                    decision = "PROMOTE"
                    reason = "prospective real goal effects improved under the scheduler mutation"
                elif saturated or now >= expires:
                    decision = (
                        "ROLLBACK_REGRESSION"
                        if gain < -MIN_GAIN
                        else "ROLLBACK_NO_GENERALIZATION"
                    )
                    reason = "scheduler train advantage did not generalize to prospective goal effects"
                else:
                    decision = "WAIT_FOR_EVIDENCE"
                    reason = "prospective effect difference is not yet decisive"
            elif now >= expires:
                baseline_mean = (
                    sum(scores["BASELINE"]) / nb if nb else None
                )
                candidate_mean = (
                    sum(scores["CANDIDATE"]) / nc if nc else None
                )
                gain = (
                    candidate_mean - baseline_mean
                    if baseline_mean is not None and candidate_mean is not None
                    else None
                )
                decision = "ROLLBACK_INSUFFICIENT_EVIDENCE"
                reason = "prospective scheduler window expired before both arms reached minimum evidence"
            else:
                decision = None
                baseline_mean = candidate_mean = gain = None

            if decision is not None:
                gate = self.awe.ingest_evaluator_receipt(
                    experiment_id,
                    {
                        "decision": decision,
                        "reason": reason,
                        "train": dict(trial.get("train") or {}),
                        "hidden": {
                            "baseline_mean": (
                                round(baseline_mean, 6)
                                if baseline_mean is not None else None
                            ),
                            "candidate_mean": (
                                round(candidate_mean, 6)
                                if candidate_mean is not None else None
                            ),
                            "gain": (
                                round(gain, 6) if gain is not None else None
                            ),
                            "noise_floor": 0.0,
                            "n_baseline": nb,
                            "n_candidate": nc,
                            "infrastructure_error_count": infra_errors,
                        },
                        "regression": {
                            "owner_authority_immutable": True,
                            "pending_continuation_immutable": True,
                            "priority_bonus_upper_bound_seconds": MAX_PRIORITY_BONUS_SECONDS,
                        },
                    },
                    now=now,
                )
                if gate["status"] == "PROMOTION_APPROVED":
                    challenger = _bounded(float(trial["challenger_bonus_seconds"]))
                    _write(self.active_policy_path, {
                        "schema_version": POLICY_SCHEMA,
                        "status": "PROMOTED",
                        "authority": "AWE_HILLCLIMB_GATE",
                        "capability_version_id": _policy_ref(challenger),
                        "awe_experiment_id": experiment_id,
                        "priority_bonus_seconds": challenger,
                        "promoted_at": now.isoformat(),
                    })
                    trial = {
                        **dict(trial), "status": "PROMOTION_APPROVED",
                        "decision": gate.get("decision"), "completed_at": now.isoformat(),
                    }
                elif gate["status"] == "ROLLBACK_REQUIRED":
                    trial = {
                        **dict(trial), "status": "ROLLBACK_REQUIRED",
                        "decision": gate.get("decision"), "completed_at": now.isoformat(),
                    }
                state["trial"] = trial
        state["updated_at"] = now.isoformat()
        self._save(state)
        return {
            "status": "SETTLED",
            "decision_id": str(decision_id),
            "outcome": dict(decisions[index]["outcome"]),
            "awe_gate": (
                {"status": gate.get("status"), "decision": gate.get("decision")}
                if isinstance(gate, Mapping) else None
            ),
        }

    def projection(self) -> dict[str, Any]:
        state = self._state()
        decisions = [row for row in state.get("decisions") or () if isinstance(row, Mapping)]
        return {
            "schema_version": SCHEMA,
            "status": state.get("status"),
            "active_priority_bonus_seconds": self.active_bonus_seconds(),
            "decision_count": len(decisions),
            "settled_valid_count": sum(
                1 for row in decisions
                if isinstance(row.get("outcome"), Mapping) and row["outcome"].get("valid")
            ),
            "trial": (
                dict(state["trial"]) if isinstance(state.get("trial"), Mapping) else None
            ),
        }


__all__ = [
    "DEFAULT_PRIORITY_BONUS_SECONDS",
    "GoalSchedulerEvolution",
    "MAX_PRIORITY_BONUS_SECONDS",
    "MIN_PRIORITY_BONUS_SECONDS",
]
