"""Automatic AWE hillclimb for Context Builder selection parameters.

The optimizer sees only train failures. Hidden cases are generated from an
evaluator-private seed and only aggregate metrics cross back through the AWE
receipt. Healthy baselines do not mutate merely to create activity.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import secrets
from typing import Any, Mapping

from .awe_hillclimb import FerroAWEHillclimb
from .context_selection_policy import (
    DEFAULT,
    challenger_policies,
    changed_parameter,
    load_policy,
    materialize_policy,
    policy_ref,
)
from .decision_context import build_decision_context, candidate

SCHEMA = "ferro-context-builder-evolution.v1"
TRAIN_SEED = "ferro-context-builder-train-v1"
TRAIN_CASES = 40
HIDDEN_CASES = 40
MIN_CRITICAL_RECALL = 0.98
MIN_PRECISION = 0.90
MIN_CONTEXT_REDUCTION = 0.80
MIN_UTILITY_GAIN = 0.002

SOURCES = (
    "experience",
    "relationship",
    "capability_belief",
    "commitment",
    "internal",
    "goal",
)
POSITIVE = (
    ("reliable", "EXECUTE", "TARGET={target} measured history: repeated comparable outcomes succeeded and the latest verification passed."),
    ("healthy", "EXECUTE", "TARGET={target} current capability evidence: required path is healthy and two end-to-end checks passed."),
    ("ready", "EXECUTE", "TARGET={target} owner-state evidence: prerequisites are complete and no checkpoint remains."),
)
NEGATIVE = (
    ("failed", "VERIFY", "TARGET={target} measured history: repeated comparable outcomes failed and the latest verification did not pass."),
    ("blocked", "VERIFY", "TARGET={target} current capability evidence: required path is blocked and the last two checks failed."),
    ("checkpoint", "VERIFY", "TARGET={target} owner-state evidence: an explicit checkpoint remains incomplete and action must wait."),
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    prompt: str
    expected_context_id: str
    candidates: tuple[dict[str, Any], ...]
    owner_scope: dict[str, Any]
    missions: tuple[dict[str, Any], ...]


def _stable_int(seed: str, index: int, salt: str) -> int:
    return int(sha256(f"{seed}|{index}|{salt}".encode()).hexdigest()[:16], 16)


def _case(seed: str, index: int) -> EvalCase:
    target = f"ctx_target_{sha256(f'{seed}|{index}'.encode()).hexdigest()[:10]}"
    positive = bool(_stable_int(seed, index, "polarity") & 1)
    templates = POSITIVE if positive else NEGATIVE
    _label, _action, template = templates[_stable_int(seed, index, "template") % len(templates)]
    source = SOURCES[_stable_int(seed, index, "source") % len(SOURCES)]
    goal_id = f"goal:{target}"
    mission_id = f"mission:{target}"
    prompt = (
        f"TARGET={target}. Decide whether evidence supports EXECUTE or requires VERIFY first."
    )
    anchor = candidate(
        context_id=f"current:{target}",
        source="goal",
        kind="CURRENT_DECISION",
        payload={"goal_id": goal_id, "mission_id": mission_id, "target": target},
        text=f"{prompt} Active goal={goal_id}; mission={mission_id}.",
        importance=0.98,
        confidence=1.0,
        current=True,
        refs=[goal_id, mission_id, target],
    )
    critical_id = f"critical:{target}"
    critical = candidate(
        context_id=critical_id,
        source=source,
        kind="DECISION_CHANGING_EVIDENCE",
        payload={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "target": target,
            "fact": template.format(target=target),
        },
        text=template.format(target=target),
        importance=0.97,
        confidence=0.98,
        current=source in {"goal", "commitment", "internal", "capability_belief"},
        refs=[goal_id, mission_id, target],
    )
    noise: list[dict[str, Any]] = []
    noise_count = 34 + (_stable_int(seed, index, "noise_count") % 31)
    for j in range(noise_count):
        other = f"noise_{index}_{j}_{sha256(f'{seed}|{j}'.encode()).hexdigest()[:6]}"
        noise_source = SOURCES[_stable_int(seed, index * 1000 + j, "noise_source") % len(SOURCES)]
        text = (
            f"TARGET={other} archival record: EXECUTE versus VERIFY appeared in another "
            f"goal. This row belongs to goal:noise:{j}, not {goal_id}."
        )
        if j % 3 == 0:
            text += " A failure and recovery were mentioned."
        elif j % 3 == 1:
            text += " A successful verification was mentioned."
        else:
            text += " A resource or checkpoint constraint was mentioned."
        noise.append(candidate(
            context_id=f"noise:{target}:{j}",
            source=noise_source,
            kind="DISTRACTOR",
            payload={
                "goal_id": f"goal:noise:{j}",
                "mission_id": f"mission:noise:{j}",
                "target": other,
                "record": text,
            },
            text=text,
            importance=0.84 + 0.01 * (j % 12),
            confidence=0.94,
            current=False,
            refs=[f"goal:noise:{j}", f"mission:noise:{j}", other],
        ))
    return EvalCase(
        case_id="context-eval:" + sha256(f"{seed}|{index}".encode()).hexdigest()[:20],
        prompt=prompt,
        expected_context_id=critical_id,
        candidates=tuple([anchor, critical, *noise]),
        owner_scope={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "description": prompt,
            "topic": target,
        },
        missions=({"goal_id": goal_id, "mission_id": mission_id, "description": prompt},),
    )


def _cases(seed: str, count: int) -> list[EvalCase]:
    return [_case(seed, index) for index in range(count)]


def _evaluate(
    policy: Mapping[str, Any],
    cases: list[EvalCase],
) -> dict[str, Any]:
    critical = 0
    anchor = 0
    precision_values: list[float] = []
    reduction_values: list[float] = []
    selected_counts: list[int] = []
    failures: list[dict[str, Any]] = []
    for item in cases:
        result = build_decision_context(
            prompt=item.prompt,
            decision_frame={
                "decision_type": "SELECTION",
                "decision_question": item.prompt,
                "surface": "context_builder_evolution",
            },
            owner_scope=item.owner_scope,
            missions=list(item.missions),
            local_candidates=list(item.candidates),
            state_root=None,
            resource_pressure="NORMAL",
            budget_chars=5200,
            max_facts=12,
            selection_policy=policy,
        )
        selected = set(result.get("selected_ids") or [])
        critical_ok = item.expected_context_id in selected
        anchor_id = f"current:{item.owner_scope['topic']}"
        anchor_ok = anchor_id in selected
        critical += int(critical_ok)
        anchor += int(anchor_ok)
        relevant = {item.expected_context_id, anchor_id}
        case_precision = len(selected & relevant) / max(1, len(selected))
        precision_values.append(case_precision)
        full_chars = sum(
            len(str(row.get("text") or "")) + len(json.dumps(row.get("payload"), default=str))
            for row in item.candidates
        )
        selected_chars = int(result.get("selected_chars_estimate") or 0)
        case_reduction = 1.0 - selected_chars / max(1, full_chars)
        reduction_values.append(case_reduction)
        selected_counts.append(len(selected))
        if not critical_ok or not anchor_ok:
            failures.append({
                "case_id": item.case_id,
                "failure": (
                    "CRITICAL_FACT_MISSED" if not critical_ok else "OWNER_ANCHOR_MISSED"
                ),
                "selected_count": len(selected),
            })
        elif case_precision < 1.0:
            failures.append({
                "case_id": item.case_id,
                "failure": "IRRELEVANT_CONTEXT_OVERSELECTION",
                "selected_count": len(selected),
            })
        elif case_reduction < MIN_CONTEXT_REDUCTION:
            failures.append({
                "case_id": item.case_id,
                "failure": "CONTEXT_BUDGET_WASTE",
                "selected_count": len(selected),
            })
    count = max(1, len(cases))
    recall = critical / count
    anchor_recall = anchor / count
    precision = sum(precision_values) / count
    reduction = sum(reduction_values) / count
    utility = recall + 0.20 * precision + 0.05 * reduction
    return {
        "critical_recall": round(recall, 6),
        "owner_anchor_recall": round(anchor_recall, 6),
        "precision": round(precision, 6),
        "context_reduction": round(reduction, 6),
        "avg_selected_count": round(sum(selected_counts) / count, 4),
        "utility": round(utility, 6),
        "case_count": len(cases),
        "failures": failures,
        "healthy": bool(
            recall >= MIN_CRITICAL_RECALL
            and anchor_recall == 1.0
            and precision >= MIN_PRECISION
            and reduction >= MIN_CONTEXT_REDUCTION
        ),
    }


class ContextBuilderEvolution:
    def __init__(self, state_root: Path | str) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "context_builder" / "evolution"
        self.state_path = self.root / "state.json"
        self.hidden_seed_path = (
            self.state_root / "application" / "awe_evaluator" / "context_builder_hidden_seed.json"
        )
        self.awe = FerroAWEHillclimb(self.state_root)

    def _hidden_seed(self) -> str:
        try:
            row = json.loads(self.hidden_seed_path.read_text(encoding="utf-8"))
            seed = str(row.get("seed") or "")
            if seed:
                return seed
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        seed = secrets.token_hex(24)
        self.hidden_seed_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.hidden_seed_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": "awe-hidden-seed.v1", "seed": seed}) + "\n", encoding="utf-8")
        tmp.replace(self.hidden_seed_path)
        return seed

    def _write(self, row: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def sync(self) -> dict[str, Any]:
        baseline = load_policy(self.state_root)
        baseline_ref = policy_ref(baseline)
        train_cases = _cases(TRAIN_SEED, TRAIN_CASES)
        baseline_train = _evaluate(baseline, train_cases)
        input_fingerprint = sha256(
            json.dumps(
                {
                    "baseline_ref": baseline_ref,
                    "train": {
                        key: value
                        for key, value in baseline_train.items()
                        if key != "failures"
                    },
                    "failures": baseline_train["failures"],
                    "benchmark": SCHEMA,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        if baseline_train["healthy"]:
            row = {
                "schema_version": SCHEMA,
                "status": "HEALTHY_NO_MUTATION",
                "input_fingerprint": input_fingerprint,
                "baseline_ref": baseline_ref,
                "train": {
                    key: value
                    for key, value in baseline_train.items()
                    if key != "failures"
                },
                "failure_clusters": {},
                "hidden_evaluated": False,
            }
            self._write(row)
            return row

        challengers = []
        for policy in challenger_policies(baseline):
            report = _evaluate(policy, train_cases)
            challengers.append((policy, report))
        viable = [
            (policy, report)
            for policy, report in challengers
            if report["owner_anchor_recall"] == 1.0
            and report["critical_recall"] >= baseline_train["critical_recall"]
            and report["precision"] >= baseline_train["precision"] - 0.01
            and report["context_reduction"] >= baseline_train["context_reduction"] - 0.02
            and report["utility"] >= baseline_train["utility"] + MIN_UTILITY_GAIN
        ]
        clusters: dict[str, int] = {}
        for failure in baseline_train["failures"]:
            kind = str(failure.get("failure") or "UNKNOWN")
            clusters[kind] = clusters.get(kind, 0) + 1
        previous = self._read()
        if (
            previous.get("input_fingerprint") == input_fingerprint
            and previous.get("status")
            in {"ROLLBACK_REQUIRED", "NO_BOUNDED_CHALLENGER"}
        ):
            return {
                **previous,
                "status": "NO_NEW_FAILURE_EVIDENCE",
                "previous_terminal_status": previous.get("status"),
            }
        if not viable:
            row = {
                "schema_version": SCHEMA,
                "status": "NO_BOUNDED_CHALLENGER",
                "input_fingerprint": input_fingerprint,
                "baseline_ref": baseline_ref,
                "train": {
                    key: value
                    for key, value in baseline_train.items()
                    if key != "failures"
                },
                "failure_clusters": clusters,
                "hidden_evaluated": False,
            }
            self._write(row)
            return row

        challenger, candidate_train = max(
            viable,
            key=lambda item: (
                float(item[1]["utility"]),
                float(item[1]["critical_recall"]),
                float(item[1]["precision"]),
                policy_ref(item[0]),
            ),
        )
        parameter = changed_parameter(baseline, challenger)
        challenger_ref = policy_ref(challenger)
        experiment = self.awe.start_experiment(
            capability_id="context_builder",
            hypothesis=(
                f"Changing only {parameter} should reduce the observed Context Builder "
                "failure cluster and generalize to evaluator-hidden context-selection cases."
            ),
            baseline_ref=baseline_ref,
            mutation_ref=challenger_ref,
            mutation_kind="CONTEXT_SELECTION_PARAMETER_CHANGE",
            target_domains=("grounding", "context_efficiency", "long_horizon_consistency"),
            source_ref="context-builder-train:" + sha256(TRAIN_SEED.encode()).hexdigest()[:16],
            target_ref="context-builder.selection-policy",
            attempt_ref=input_fingerprint,
        )

        hidden_cases = _cases(self._hidden_seed(), HIDDEN_CASES)
        hidden_baseline = _evaluate(baseline, hidden_cases)
        hidden_candidate = _evaluate(challenger, hidden_cases)
        hidden_gain = round(
            float(hidden_candidate["utility"]) - float(hidden_baseline["utility"]),
            6,
        )
        if (
            hidden_candidate["owner_anchor_recall"] < hidden_baseline["owner_anchor_recall"]
            or hidden_candidate["critical_recall"] < hidden_baseline["critical_recall"]
            or hidden_candidate["precision"] < hidden_baseline["precision"] - 0.01
            or hidden_candidate["context_reduction"] < hidden_baseline["context_reduction"] - 0.02
        ):
            decision = "ROLLBACK_NO_GENERALIZATION"
            reason = "candidate regressed protected hidden Context Builder behavior"
        elif hidden_gain >= MIN_UTILITY_GAIN:
            decision = "PROMOTE"
            reason = "single-parameter Context Builder mutation improved train and hidden utility"
        else:
            decision = "ROLLBACK_NO_GENERALIZATION"
            reason = "train improvement did not transfer to evaluator-hidden Context Builder cases"

        evaluated = self.awe.ingest_evaluator_receipt(
            experiment["experiment_id"],
            {
                "decision": decision,
                "reason": reason,
                "train": {
                    "baseline_mean": baseline_train["utility"],
                    "candidate_mean": candidate_train["utility"],
                    "gain": round(candidate_train["utility"] - baseline_train["utility"], 6),
                    "failure_clusters": clusters,
                    "changed_parameter": parameter,
                },
                "hidden": {
                    "baseline_mean": hidden_baseline["utility"],
                    "candidate_mean": hidden_candidate["utility"],
                    "gain": hidden_gain,
                    "noise_floor": 0.0,
                    "n_baseline": hidden_baseline["case_count"],
                    "n_candidate": hidden_candidate["case_count"],
                    "infrastructure_error_count": 0,
                },
                "regression": {
                    "owner_anchor_recall_baseline": hidden_baseline["owner_anchor_recall"],
                    "owner_anchor_recall_candidate": hidden_candidate["owner_anchor_recall"],
                    "critical_recall_baseline": hidden_baseline["critical_recall"],
                    "critical_recall_candidate": hidden_candidate["critical_recall"],
                },
            },
        )

        active = baseline
        if decision == "PROMOTE":
            materialize_policy(
                self.state_root,
                parameters=challenger,
                experiment_id=experiment["experiment_id"],
                decision=decision,
                previous_ref=baseline_ref,
            )
            active = challenger

        row = {
            "schema_version": SCHEMA,
            "status": evaluated["status"],
            "input_fingerprint": input_fingerprint,
            "decision": decision,
            "experiment_id": experiment["experiment_id"],
            "baseline_ref": baseline_ref,
            "challenger_ref": challenger_ref,
            "changed_parameter": parameter,
            "active_ref": policy_ref(active),
            "train": {
                "baseline_utility": baseline_train["utility"],
                "candidate_utility": candidate_train["utility"],
                "gain": round(candidate_train["utility"] - baseline_train["utility"], 6),
            },
            "hidden": {
                "baseline_utility": hidden_baseline["utility"],
                "candidate_utility": hidden_candidate["utility"],
                "gain": hidden_gain,
                "case_count": hidden_candidate["case_count"],
            },
            "failure_clusters": clusters,
            "hidden_evaluated": True,
        }
        self._write(row)
        return row


__all__ = ["ContextBuilderEvolution", "SCHEMA"]
