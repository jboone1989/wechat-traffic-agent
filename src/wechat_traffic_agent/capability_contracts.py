"""Capability contracts for detecting silent functional breakpoints in Ferro.

Runtime errors are only one kind of pathology.  These contracts compare what a
capability is supposed to make observably true with what actually happened.
Violations are deterministic evidence that can feed the existing interoceptive
fault system even when every function returned "OK".
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "ferro-capability-contracts.v1"
MAX_HISTORY = 48


def _bounded(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _walk(value: Any, *, path: str = "result", depth: int = 0):
    if depth > 8:
        return
    if isinstance(value, Mapping):
        yield path, value
        for key, child in value.items():
            if key in {"interoception", "maintenance_observation", "capability_contracts"}:
                continue
            if isinstance(child, (Mapping, list, tuple)):
                yield from _walk(child, path=f"{path}.{key}", depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(list(value)[:64]):
            if isinstance(child, (Mapping, list, tuple)):
                yield from _walk(child, path=f"{path}[{index}]", depth=depth + 1)


def _violation(
    kind: str,
    *,
    subsystem: str,
    severity: float,
    contract: str,
    expected: str,
    observed: str,
    evidence_refs: list[str] | None = None,
    failure_site: str | None = None,
) -> dict[str, Any]:
    row = {
        "kind": kind,
        "subsystem": subsystem,
        "severity": round(_bounded(severity), 6),
        "contract": contract,
        "expected": expected[:500],
        "observed": observed[:800],
        "evidence_refs": list(dict.fromkeys(
            str(x) for x in (evidence_refs or []) if str(x).strip()
        ))[:16],
    }
    if failure_site:
        row["failure_site"] = str(failure_site)[:500]
    return row


def evaluate_context_contract(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Check whether the Context Builder fulfilled its observable contract."""
    decision = _mapping(snapshot.get("decision_context"))
    receipt = _mapping(snapshot.get("context_receipt"))
    summary = _mapping(receipt.get("selection_summary"))
    counts = _mapping(receipt.get("candidate_counts"))
    decision_frame = _mapping(snapshot.get("decision_frame"))
    behavioral = _mapping(snapshot.get("behavioral_state"))
    phenotype = _mapping(snapshot.get("phenotype_prior"))
    violations: list[dict[str, Any]] = []

    candidate_count = int(decision.get("candidate_count") or 0)
    selected_count = int(decision.get("selected_count") or 0)
    needs = [
        row for row in list(decision.get("information_needs") or [])
        if isinstance(row, Mapping)
    ]
    if candidate_count >= 4 and needs and selected_count == 0:
        violations.append(_violation(
            "context_selection_empty",
            subsystem="context",
            severity=0.62,
            contract="relevant_context_must_reach_cognition",
            expected="at least one selected fact when multiple candidates and explicit information needs exist",
            observed=f"candidate_count={candidate_count}, selected_count=0, information_needs={len(needs)}",
            evidence_refs=["context-receipt:selection"],
        ))

    decision_type = str(decision_frame.get("decision_type") or "").upper()
    owner_mode = decision_type.startswith("OWNER_")
    full_mission_count = int(counts.get("missions") or 0)
    selected_missions = [
        row for row in list(_mapping(snapshot.get("missions")).get("active") or [])
        if isinstance(row, Mapping)
    ]
    if owner_mode and full_mission_count > 0 and not selected_missions:
        violations.append(_violation(
            "owner_goal_context_omitted",
            subsystem="context",
            severity=0.82,
            contract="owner_goal_cognition_must_retain_active_mission",
            expected="an active owner mission must survive retrieval into the model-visible context",
            observed=f"candidate_missions={full_mission_count}, selected_missions=0",
            evidence_refs=["context-receipt:missions"],
        ))

    required_always_on = {"phenotype_prior", "behavioral_state", "interoception"}
    actual_always_on = {
        str(x) for x in list(decision.get("always_on_sources") or []) if str(x)
    }
    missing = sorted(required_always_on - actual_always_on)
    if missing:
        violations.append(_violation(
            "always_on_internal_state_missing",
            subsystem="context",
            severity=0.76,
            contract="identity_and_body_state_are_always_on",
            expected="phenotype_prior, behavioral_state, and interoception are always model-visible",
            observed="missing=" + ",".join(missing),
            evidence_refs=["context-receipt:always-on"],
        ))

    dynamic = _mapping(phenotype.get("dynamic_modulation"))
    stable = _mapping(phenotype.get("stable_phenotype"))
    salient = [
        row for row in list(behavioral.get("salient_expression_profile") or [])
        if isinstance(row, Mapping)
    ]
    directives = [str(x) for x in behavioral.get("behavioral_directives") or [] if str(x)]
    targets = [
        row for row in list(behavioral.get("realization_targets") or [])
        if isinstance(row, Mapping)
    ]
    has_neural_identity = bool(dynamic or stable)
    if has_neural_identity and not behavioral:
        violations.append(_violation(
            "neural_behavior_projection_missing",
            subsystem="neural",
            severity=0.78,
            contract="neural_and_personality_state_must_project_into_behavior",
            expected="persisted neural/personality state produces a behavioral-state envelope",
            observed="phenotype state exists but behavioral_state is empty",
            evidence_refs=["phenotype-prior:current"],
        ))
    elif salient and not directives and not targets:
        violations.append(_violation(
            "neural_behavior_realization_missing",
            subsystem="neural",
            severity=0.58,
            contract="salient_internal_differences_must_create_behavioral_pressure",
            expected="salient phenotype deviations produce directives or realization targets",
            observed=f"salient_dimensions={len(salient)}, directives=0, realization_targets=0",
            evidence_refs=["behavioral-state:current"],
        ))

    # A context receipt that claims sources were selected but exposes no fact index
    # is an integrity failure, even though the builder technically returned a dict.
    selected_sources = [str(x) for x in summary.get("selected_sources") or [] if str(x)]
    fact_index = [
        row for row in list(decision.get("fact_index") or [])
        if isinstance(row, Mapping)
    ]
    if selected_count > 0 and selected_sources and not fact_index:
        violations.append(_violation(
            "context_receipt_inconsistent",
            subsystem="context",
            severity=0.70,
            contract="context_receipt_must_match_model_visible_selection",
            expected="non-empty selected facts when selected_count and selected_sources are non-empty",
            observed=f"selected_count={selected_count}, selected_sources={len(selected_sources)}, fact_index=0",
            evidence_refs=["context-receipt:integrity"],
        ))

    violations.sort(key=lambda row: (-float(row["severity"]), str(row["kind"])))
    return {
        "schema_version": SCHEMA,
        "surface": "cognition_context",
        "status": "VIOLATED" if violations else "PASS",
        "violations": violations,
        "checked_contracts": [
            "relevant_context_must_reach_cognition",
            "owner_goal_cognition_must_retain_active_mission",
            "identity_and_body_state_are_always_on",
            "neural_and_personality_state_must_project_into_behavior",
            "salient_internal_differences_must_create_behavioral_pressure",
            "context_receipt_must_match_model_visible_selection",
        ],
    }


def evaluate_durable_state_contract(
    state_root: Path | str,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Detect chronic capability failure that persists even when no task is running."""
    root = Path(state_root)
    drive_state = _read_json(root / "application" / "drive_system.json")
    drives = _mapping(drive_state.get("drives"))
    violations: list[dict[str, Any]] = []

    for drive_name, raw in sorted(drives.items(), key=lambda item: str(item[0])):
        row = _mapping(raw)
        try:
            no_effect = int(row.get("no_effect_streak") or 0)
        except (TypeError, ValueError):
            no_effect = 0
        if no_effect < 3:
            continue
        severity = min(0.92, 0.42 + 0.025 * no_effect)
        last_attempted = str(row.get("last_attempted_at") or "")
        evidence_ref = str(row.get("last_outcome_evidence_ref") or "")
        observed = (
            f"drive={drive_name}, no_effect_streak={no_effect}, "
            f"last_outcome_status={row.get('last_outcome_status')}, "
            f"last_outcome_utility={row.get('last_outcome_utility')}, "
            f"last_attempted_at={last_attempted or 'unknown'}"
        )
        inferred_task = {
            "resource": "RESOURCE_REVIEW",
            "survival": "RESOURCE_REVIEW",
            "curiosity": "OBSERVE_WORLD",
            "social": "CHECK_X",
            "creation": "CONTENT_COGNITION",
            "mastery": "REFLECT",
            "propagation": "NEURAL_DEVELOPMENT",
        }.get(str(drive_name).lower(), "")
        last_task = str(row.get("last_outcome_task") or inferred_task).upper()
        failure_site = {
            "RESOURCE_REVIEW": "resource_review:survival_resources",
            "CONTENT_COGNITION": "content_cognition:content_goal_loop",
            "CHECK_X": "check_x:x_growth",
            "OBSERVE_WORLD": "observe_world:world_feed",
        }.get(last_task, "wakeful_tasks.py:goal_effect_contract")
        violations.append(_violation(
            "goal_effect_absent",
            subsystem="goal_execution",
            severity=severity,
            contract="persistent_drive_attempts_must_eventually_change_goal_relevant_state",
            expected="a repeatedly activated drive eventually produces measurable effect or a concrete blocker",
            observed=observed,
            evidence_refs=[
                ref for ref in (
                    evidence_ref,
                    f"drive-state:{drive_name}",
                ) if ref
            ],
            failure_site=failure_site,
        ))

    violations.sort(key=lambda row: (-float(row["severity"]), str(row["kind"]), str(row.get("observed") or "")))
    return {
        "schema_version": SCHEMA,
        "surface": "durable_state",
        "task": "NEURAL_INTEROCEPTION",
        "status": "VIOLATED" if violations else "PASS",
        "violations": violations,
        "checked_contracts": [
            "persistent_drive_attempts_must_eventually_change_goal_relevant_state",
        ],
        "observed_at": now.astimezone(timezone.utc).isoformat(),
    }


def _result_fingerprint(value: Mapping[str, Any]) -> str:
    compact = {
        key: value.get(key)
        for key in ("status", "count", "eligible", "current", "trajectory", "pressure", "world_utility")
        if key in value
    }
    if not compact:
        compact = {
            key: value.get(key)
            for key in sorted(value)[:8]
            if key not in {"observed_at", "updated_at", "completed_at"}
        }
    return sha256(
        json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:20]


def evaluate_task_contract(task: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Check task-level capability effects, including silent no-effect execution."""
    task = str(task or "UNKNOWN").upper()
    violations: list[dict[str, Any]] = []

    max_no_effect = 0
    max_no_effect_path = ""
    for path, row in _walk(result):
        try:
            no_effect = int(row.get("no_effect_streak") or 0)
        except (TypeError, ValueError):
            no_effect = 0
        if no_effect > max_no_effect:
            max_no_effect = no_effect
            max_no_effect_path = path
    if max_no_effect >= 3:
        severity = min(0.88, 0.42 + 0.025 * max_no_effect)
        violations.append(_violation(
            "goal_effect_absent",
            subsystem="goal_execution",
            severity=severity,
            contract="repeated_executed_actions_must_eventually_change_goal_relevant_state",
            expected="repeated executions produce measurable progress or an explicit blocker",
            observed=f"no_effect_streak={max_no_effect} at {max_no_effect_path}",
            evidence_refs=[f"task-result:{task}:no-effect"],
        ))

    primary_status = str(result.get("status") or "").upper()
    unresolved = primary_status == "OWNER_ACTION_REQUIRED_BUT_UNRESOLVED"
    if not unresolved:
        unresolved = any(
            str(row.get("status") or "").upper() == "UNRESOLVED_ACTION_REQUIRED"
            for _path, row in _walk(result)
        )
    if unresolved:
        violations.append(_violation(
            "owner_action_exhausted",
            subsystem="goal_execution",
            severity=0.76,
            contract="active_owner_goal_must_resolve_to_action_or_concrete_blocker",
            expected="bounded owner cognition chooses a permitted action or names a concrete external blocker",
            observed="inspection budget exhausted without a permitted world-changing action",
            evidence_refs=[f"task-result:{task}:owner-action"],
        ))

    owner = _mapping(result.get("owner_agent_episode"))
    steps = [
        row for row in list(owner.get("steps") or [])
        if isinstance(row, Mapping)
    ]
    if len(steps) >= 2:
        for first, second in zip(steps, steps[1:]):
            d1 = _mapping(first.get("decision"))
            d2 = _mapping(second.get("decision"))
            r1 = _mapping(first.get("result"))
            r2 = _mapping(second.get("result"))
            action1 = str(d1.get("action") or "")
            action2 = str(d2.get("action") or "")
            if (
                action1
                and action1 == action2
                and action1 not in {"WAIT", "RUN_CONTENT_PIPELINE"}
                and r1
                and r2
                and _result_fingerprint(r1) == _result_fingerprint(r2)
            ):
                violations.append(_violation(
                    "repeated_tool_without_new_evidence",
                    subsystem="tool_use",
                    severity=0.60,
                    contract="repeated_tool_calls_must_have_new_information_value",
                    expected="do not repeat the same read-only tool when the previous result already answers the need",
                    observed=f"repeated action={action1} with materially identical result",
                    evidence_refs=[f"owner-agent:{owner.get('episode_id') or 'current'}"],
                ))
                break

    for path, row in _walk(result):
        decision = _mapping(row.get("decision"))
        tool_result = _mapping(row.get("result"))
        action = str(decision.get("action") or "")
        if not action or action in {"WAIT", "RUN_CONTENT_PIPELINE"}:
            continue
        if not tool_result:
            violations.append(_violation(
                "tool_result_missing",
                subsystem="tool_use",
                severity=0.66,
                contract="tool_invocation_must_produce_observable_result",
                expected="every executed tool step has a non-empty result receipt",
                observed=f"action={action} has no result at {path}",
                evidence_refs=[f"task-result:{task}:tool"],
            ))
            break

    violations.sort(key=lambda row: (-float(row["severity"]), str(row["kind"])))
    return {
        "schema_version": SCHEMA,
        "surface": "task_result",
        "task": task,
        "status": "VIOLATED" if violations else "PASS",
        "violations": violations,
        "checked_contracts": [
            "repeated_executed_actions_must_eventually_change_goal_relevant_state",
            "active_owner_goal_must_resolve_to_action_or_concrete_blocker",
            "repeated_tool_calls_must_have_new_information_value",
            "tool_invocation_must_produce_observable_result",
        ],
    }


class CapabilityContractMonitor:
    """Persist capability-contract evidence without granting any repair authority."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "capability_contracts"
        self.state_path = self.root / "state.json"

    def _persist(self, report: Mapping[str, Any], *, now: datetime, identity: str) -> dict[str, Any]:
        now = now.astimezone(timezone.utc)
        state = _read_json(self.state_path)
        history = [
            dict(row)
            for row in list(state.get("history") or [])
            if isinstance(row, Mapping)
        ]
        observation_id = "capability-contract:" + sha256(
            (identity + "\0" + json.dumps(
                list(report.get("violations") or []),
                sort_keys=True,
                ensure_ascii=False,
            )).encode("utf-8")
        ).hexdigest()[:24]
        duplicate = observation_id == str(state.get("latest_observation_id") or "")
        enriched = {
            **dict(report),
            "observation_id": observation_id,
            "observed_at": now.isoformat(),
            "duplicate": duplicate,
        }
        if not duplicate:
            history.append({
                "observation_id": observation_id,
                "observed_at": now.isoformat(),
                "surface": report.get("surface"),
                "task": report.get("task"),
                "status": report.get("status"),
                "violations": list(report.get("violations") or [])[:8],
            })
            history = history[-MAX_HISTORY:]
        payload = {
            "schema_version": SCHEMA,
            "updated_at": now.isoformat(),
            "latest_observation_id": observation_id,
            "latest": enriched,
            "history": history,
        }
        _atomic_json(self.state_path, payload)
        _atomic_json(self.root / "latest.json", enriched)
        return enriched

    def observe_context(self, snapshot: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
        report = evaluate_context_contract(snapshot)
        continuity = _mapping(snapshot.get("continuity"))
        frame = _mapping(snapshot.get("decision_frame"))
        identity = "context|" + "|".join([
            str(continuity.get("application_cycle") or ""),
            str(continuity.get("mind_tick") or ""),
            str(frame.get("decision_type") or ""),
            str(_mapping(snapshot.get("decision_context")).get("selected_count") or 0),
        ])
        return self._persist(report, now=now, identity=identity)

    def observe_durable_state(self, *, now: datetime) -> dict[str, Any]:
        report = evaluate_durable_state_contract(self.state_root, now=now)
        fingerprint = [
            (
                str(row.get("kind") or ""),
                str(row.get("subsystem") or ""),
                str(row.get("observed") or ""),
            )
            for row in list(report.get("violations") or [])
            if isinstance(row, Mapping)
        ]
        identity = "durable|" + sha256(
            json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        return self._persist(report, now=now, identity=identity)

    def observe_task(self, task: str, result: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
        report = evaluate_task_contract(task, result)
        cycle = str(
            result.get("cycle")
            or result.get("request_id")
            or result.get("task_result_id")
            or int(now.timestamp())
        )
        identity = f"task|{str(task or '').upper()}|{cycle}"
        return self._persist(report, now=now, identity=identity)


__all__ = [
    "CapabilityContractMonitor",
    "MAX_HISTORY",
    "SCHEMA",
    "evaluate_context_contract",
    "evaluate_durable_state_contract",
    "evaluate_task_contract",
]
