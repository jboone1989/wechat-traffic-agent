"""Deterministic classification of task-result symptoms, independent of state IO.

No fault-episode writes, L1 calls, or repair decisions. Can be shared by
different runtimes without constructing InteroceptiveFaultSystem.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_HARD_FAILURE = frozenset({"ERROR", "FAILED", "FATAL"})
_IGNORE_STATUSES = frozenset({
    "", "OK", "HEALTHY", "SUCCESS", "SUCCEEDED", "COMPLETED", "OBSERVED",
    "SETTLED", "VERIFIED", "ADOPTED", "PUBLISHED", "NOOP",
    "SHUTDOWN_CHECKPOINT", "CANCELLED",
})
_TASK_SUBSYSTEMS = {
    "CONTENT_COGNITION": "cognition",
    "VERIFY_PREDICTION": "prediction",
    "CHECK_OUTCOMES": "prediction",
    "OBSERVE_WORLD": "world_model",
    "CHECK_X": "social_perception",
    "RESOURCE_REVIEW": "resources",
    "REFLECT": "self_reflection",
    "NEURAL_INTEROCEPTION": "interoception",
    "NEURAL_PLAY": "neural",
    "NEURAL_DREAM": "neural",
    "NEURAL_TRANSFER": "neural",
    "NEURAL_DEVELOPMENT": "neural",
    "NEURAL_DEVELOPMENT_TRANSFER": "neural",
    "NEURAL_EVOLUTION_CONSOLIDATION": "neural",
}


def _bounded(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _status_rows(value: Any, *, path: str = "result", depth: int = 0) -> list[tuple[str, str]]:
    if depth > 7:
        return []
    rows: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        status = str(value.get("status") or "").strip().upper()
        if status:
            rows.append((path, status))
        for key, child in value.items():
            if key in {"interoception", "maintenance_observation", "capability_contracts"}:
                continue
            if isinstance(child, (Mapping, list, tuple)):
                rows.extend(_status_rows(child, path=f"{path}.{key}", depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(list(value)[:64]):
            rows.extend(_status_rows(child, path=f"{path}[{index}]", depth=depth + 1))
    return rows


def _numeric_rows(
    value: Any,
    wanted: frozenset[str],
    *,
    path: str = "result",
    depth: int = 0,
) -> list[tuple[str, str, float]]:
    if depth > 7:
        return []
    rows: list[tuple[str, str, float]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"interoception", "maintenance_observation", "capability_contracts"}:
                continue
            if key in wanted and isinstance(child, (int, float)) and not isinstance(child, bool):
                rows.append((path, key, float(child)))
            if isinstance(child, (Mapping, list, tuple)):
                rows.extend(_numeric_rows(child, wanted, path=f"{path}.{key}", depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(list(value)[:64]):
            rows.extend(_numeric_rows(child, wanted, path=f"{path}[{index}]", depth=depth + 1))
    return rows


def _has_retryable(value: Any, *, depth: int = 0) -> bool:
    if depth > 7:
        return False
    if isinstance(value, Mapping):
        if value.get("retryable") is True:
            return True
        return any(
            _has_retryable(child, depth=depth + 1)
            for key, child in value.items()
            if key not in {"interoception", "maintenance_observation", "capability_contracts"}
            and isinstance(child, (Mapping, list, tuple))
        )
    if isinstance(value, (list, tuple)):
        return any(_has_retryable(child, depth=depth + 1) for child in list(value)[:64])
    return False


def _subsystem(task: str, status_rows: Sequence[tuple[str, str]]) -> str:
    blob = " ".join(path.lower() + " " + status.lower() for path, status in status_rows)
    for token, domain in (
        ("context", "context"),
        ("memory", "memory"),
        ("chatgpt", "cognition_provider"),
        ("provider", "cognition_provider"),
        ("browser", "network"),
        ("network", "network"),
        ("self_repair", "self_repair"),
        ("repair", "self_repair"),
        ("prediction", "prediction"),
        ("resource", "resources"),
    ):
        if token in blob:
            return domain
    return _TASK_SUBSYSTEMS.get(str(task or "").upper(), str(task or "unknown").lower())


def _scan_symptoms(task: str, result: Mapping[str, Any]):
    task = str(task or "UNKNOWN").upper()
    primary = str(result.get("status") or "").strip().upper()
    statuses = _status_rows(result)
    symptoms: list[dict[str, Any]] = []

    def add(kind: str, severity: float, path: str, detail: str) -> None:
        symptoms.append({
            "kind": kind,
            "severity": round(_bounded(severity), 6),
            "path": path,
            "detail": detail[:240],
        })

    for path, status in statuses:
        if status in _IGNORE_STATUSES:
            continue
        if status in _HARD_FAILURE:
            add("hard_error", 0.86, path, status)
        elif status == "DEGRADED":
            add("degraded", 0.58, path, status)
        elif status == "CHATGPT_UNAVAILABLE" or status == "UNAVAILABLE":
            add("unavailable", 0.50, path, status)
        elif "FALLBACK" in status:
            add("fallback_dependency", 0.34, path, status)
        elif "RETRY" in status:
            add("retry_dependency", 0.28, path, status)
        elif status in {"RESOURCE_DEFERRED", "DEFERRED"}:
            add("deferred", 0.18, path, status)
        elif status == "CAPABILITY_UNAVAILABLE" or status.endswith("_CAPABILITY_UNAVAILABLE"):
            add("capability_gap", 0.16, path, status)
        elif "STALLED" in status or "NO_MATERIAL_CHANGE" in status:
            add("goal_stall", 0.36, path, status)

    if _has_retryable(result):
        add("retry_dependency", 0.30, "result", "retryable=true")

    if task == "CONTENT_COGNITION" and primary in {
        "COGNITION_ONLY", "DEFERRED", "RESOURCE_DEFERRED",
    }:
        add("goal_stall", 0.30, "result.status", primary)

    contract_subsystem = ""
    contract_report = (
        result.get("capability_contracts")
        if isinstance(result.get("capability_contracts"), Mapping)
        else {}
    )
    contract_violations = [
        row for row in list(contract_report.get("violations") or [])
        if isinstance(row, Mapping)
    ]
    for index, row in enumerate(contract_violations[:16]):
        kind = str(row.get("kind") or "unknown")
        severity = _bounded(row.get("severity"), 0.45)
        subsystem_name = str(row.get("subsystem") or "capability")
        if not contract_subsystem or severity >= max(
            [
                _bounded(v.get("severity"), 0.0)
                for v in contract_violations[:index]
                if isinstance(v, Mapping)
            ]
            or [0.0]
        ):
            contract_subsystem = subsystem_name
        add(
            "contract_" + kind,
            severity,
            f"result.capability_contracts.violations[{index}]",
            str(row.get("observed") or row.get("contract") or kind),
        )

    prediction_error = 0.0
    for path, key, value in _numeric_rows(result, frozenset({"prediction_error"})):
        error = _bounded(value)
        prediction_error = max(prediction_error, error)
        if error >= 0.25:
            add(
                "prediction_error",
                min(0.72, 0.16 + 0.62 * error),
                f"{path}.{key}",
                f"{error:.6f}",
            )

    negative_world_utility = 0.0
    for path, key, value in _numeric_rows(result, frozenset({"world_utility"})):
        if value < -0.05:
            magnitude = _bounded(abs(value))
            negative_world_utility = max(negative_world_utility, magnitude)
            add(
                "negative_world_utility",
                min(0.82, 0.30 + 0.52 * magnitude),
                f"{path}.{key}",
                f"{value:.6f}",
            )
    return primary, statuses, symptoms, contract_subsystem, prediction_error, negative_world_utility


def classify_task_result(task: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Classify task failure, fallback dependence, contract gaps and outcomes."""
    task = str(task or "UNKNOWN").upper()
    (primary, statuses, symptoms, contract_subsystem,
     prediction_error, negative_world_utility) = _scan_symptoms(task, result)

    # Stable de-duplication keeps the highest severity for each symptom/path pair.
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in symptoms:
        key = (str(row["kind"]), str(row["path"]))
        prior = deduped.get(key)
        if prior is None or float(row["severity"]) > float(prior["severity"]):
            deduped[key] = row
    symptoms = sorted(
        deduped.values(),
        key=lambda row: (-float(row["severity"]), str(row["kind"]), str(row["path"])),
    )

    base_severity = float(symptoms[0]["severity"]) if symptoms else 0.0
    if len(symptoms) > 1:
        base_severity = min(1.0, base_severity + 0.06 * min(4, len(symptoms) - 1))
    subsystem = contract_subsystem or _subsystem(task, statuses)
    primary_symptom = str(symptoms[0]["kind"]) if symptoms else "healthy"
    return {
        "task": task,
        "primary_status": primary,
        "abnormal": bool(symptoms),
        "primary_symptom": primary_symptom,
        "subsystem": subsystem,
        "base_severity": round(base_severity, 6),
        "prediction_error": round(prediction_error, 6),
        "negative_world_utility": round(negative_world_utility, 6),
        "symptoms": symptoms[:16],
        "status_evidence": [
            {"path": path, "status": status}
            for path, status in statuses[:32]
        ],
    }
