"""Low-semantic interoception for Ferro runtime faults.

This module is deliberately deterministic and non-LLM.  It converts runtime
outcomes into persistent symptoms, distress, fault episodes, and recovery
evidence so service continuity cannot erase evidence that a capability is
operating abnormally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


STATE_SCHEMA = "ferro-interoception.v1"
EPISODE_SCHEMA = "ferro-fault-episode.v1"
OBSERVATION_SCHEMA = "ferro-interoceptive-observation.v1"
RECOVERY_HEALTHY_STREAK = 3
MAX_RECENT_OBSERVATIONS = 48

_HARD_FAILURE = frozenset({"ERROR", "FAILED", "FATAL"})
_IGNORE_STATUSES = frozenset({
    "", "OK", "HEALTHY", "SUCCESS", "SUCCEEDED", "COMPLETED", "OBSERVED",
    "SETTLED", "VERIFIED", "ADOPTED", "PUBLISHED", "NOOP",
    "WAITING_FOR_CHATGPT", "SHUTDOWN_CHECKPOINT", "CANCELLED",
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


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "schema_version": STATE_SCHEMA,
            "status": "SENSOR_FAILURE",
            "distress": 1.0,
            "confidence_in_self": 0.2,
            "active_episode_ids": [],
            "suspected_faults": [
                {
                    "subsystem": "interoception",
                    "confidence": 1.0,
                    "severity": 1.0,
                    "reason": "interoception state unreadable",
                }
            ],
            "symptoms": ["interoception_state_unreadable"],
        }
    return payload if isinstance(payload, dict) else None


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
            if key in {"interoception", "maintenance_observation"}:
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
            if key in {"interoception", "maintenance_observation"}:
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
            if key not in {"interoception", "maintenance_observation"}
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


def classify_task_result(task: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a task result without deciding whether the process survived.

    A task may be operationally successful yet pathologically dependent on a
    fallback or retry.  Those conditions remain visible here.
    """
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
    subsystem = _subsystem(task, statuses)
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


def _empty_projection(now: datetime | None = None) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": STATE_SCHEMA,
        "status": "UNINITIALIZED",
        "updated_at": observed_at.isoformat(),
        "distress": 0.0,
        "confidence_in_self": 1.0,
        "active_episode_ids": [],
        "suspected_faults": [],
        "symptoms": [],
        "recent_observations": [],
    }


def read_interoceptive_projection(state_root: Path | str | None) -> dict[str, Any]:
    if state_root is None:
        return _empty_projection()
    path = Path(state_root) / "application" / "interoception" / "state.json"
    payload = _read_json(path)
    if payload is None:
        return _empty_projection()
    return payload


class InteroceptiveFaultSystem:
    """Persistent body-sense layer for capability pathology."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "interoception"
        self.state_path = self.root / "state.json"
        self.episodes_root = self.root / "episodes"

    def projection(self, *, now: datetime | None = None) -> dict[str, Any]:
        payload = _read_json(self.state_path)
        return payload if payload is not None else _empty_projection(now)

    def _episode_path(self, task: str, subsystem: str, symptom: str) -> tuple[str, Path]:
        signature = sha256(
            f"{task}\0{subsystem}\0{symptom}".encode("utf-8")
        ).hexdigest()[:24]
        episode_id = f"fault-episode:{signature}"
        return episode_id, self.episodes_root / f"{signature}.json"

    def _open_episodes(self) -> list[tuple[Path, dict[str, Any]]]:
        rows: list[tuple[Path, dict[str, Any]]] = []
        if not self.episodes_root.exists():
            return rows
        for path in sorted(self.episodes_root.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "") not in {
                "OPEN", "DIAGNOSING", "REPAIR_PROPOSED", "REPAIR_REQUESTED",
                "RECOVERING",
            }:
                continue
            rows.append((path, payload))
        return rows

    @staticmethod
    def _same_task_recent(state: Mapping[str, Any], task: str) -> list[Mapping[str, Any]]:
        return [
            row for row in list(state.get("recent_observations") or [])[-12:]
            if isinstance(row, Mapping) and str(row.get("task") or "") == task
        ]

    def observe(
        self,
        task: str,
        result: Mapping[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("interoception observation time requires timezone awareness")
        now = now.astimezone(timezone.utc)
        task = str(task or "UNKNOWN").upper()
        classification = classify_task_result(task, result)
        state = self.projection(now=now)
        recent_same_task = self._same_task_recent(state, task)
        consecutive_abnormal = 0
        for row in reversed(recent_same_task):
            if not bool(row.get("abnormal")):
                break
            consecutive_abnormal += 1

        severity = float(classification["base_severity"])
        if classification["abnormal"]:
            severity = min(1.0, severity + 0.07 * min(4, consecutive_abnormal))
        primary_symptom = str(classification["primary_symptom"])
        subsystem = str(classification["subsystem"])
        cycle = str(
            result.get("cycle")
            or result.get("request_id")
            or f"{task.lower()}-{int(now.timestamp())}"
        )
        observation_id = "interoception:" + sha256(
            f"{task}\0{cycle}\0{now.isoformat()}\0{primary_symptom}".encode("utf-8")
        ).hexdigest()[:24]

        old_distress = _bounded(state.get("distress"))
        if classification["abnormal"]:
            distress = min(1.0, max(severity, old_distress * 0.72 + severity * 0.45))
        else:
            distress = max(0.0, old_distress * 0.68)

        episode_id: str | None = None
        episode_payload: dict[str, Any] | None = None
        should_diagnose = False
        if classification["abnormal"]:
            # Any new pathology means the capability has not yet recovered.
            # Reset recovery evidence for every still-open episode of this task,
            # not only the newly observed symptom signature.
            for open_path, open_episode in self._open_episodes():
                if str(open_episode.get("task") or "") != task:
                    continue
                open_episode["healthy_streak"] = 0
                open_episode["last_recurrence_at"] = now.isoformat()
                if str(open_episode.get("status") or "") == "RECOVERING":
                    open_episode["status"] = "OPEN"
                _atomic_json(open_path, open_episode)

            episode_id, episode_path = self._episode_path(task, subsystem, primary_symptom)
            previous = _read_json(episode_path) or {}
            previous_status = str(previous.get("status") or "")
            occurrences = int(previous.get("occurrences") or 0) + 1
            should_diagnose = (
                not previous.get("diagnosis_requested_at")
                and (
                    severity >= 0.45
                    or (occurrences >= 3 and severity >= 0.30)
                )
            )
            episode_payload = {
                "schema_version": EPISODE_SCHEMA,
                "episode_id": episode_id,
                "status": (
                    previous_status
                    if previous_status in {"DIAGNOSING", "REPAIR_PROPOSED", "REPAIR_REQUESTED"}
                    else "OPEN"
                ),
                "task": task,
                "subsystem": subsystem,
                "primary_symptom": primary_symptom,
                "first_seen_at": previous.get("first_seen_at") or now.isoformat(),
                "last_seen_at": now.isoformat(),
                "occurrences": occurrences,
                "max_severity": round(max(float(previous.get("max_severity") or 0.0), severity), 6),
                "current_severity": round(severity, 6),
                "healthy_streak": 0,
                "symptoms": classification["symptoms"],
                "latest_observation_id": observation_id,
                "evidence_refs": list(dict.fromkeys([
                    *[str(x) for x in previous.get("evidence_refs") or () if str(x)],
                    observation_id,
                    f"wakeful-task:{task}:{cycle}:result",
                ]))[-24:],
                "diagnosis_requested_at": (
                    now.isoformat()
                    if should_diagnose
                    else previous.get("diagnosis_requested_at")
                ),
                "recovered_at": None,
            }
            _atomic_json(episode_path, episode_payload)
        else:
            for episode_path, previous in self._open_episodes():
                if str(previous.get("task") or "") != task:
                    continue
                healthy_streak = int(previous.get("healthy_streak") or 0) + 1
                previous["healthy_streak"] = healthy_streak
                previous["last_healthy_at"] = now.isoformat()
                previous["recovery_evidence_refs"] = list(dict.fromkeys([
                    *[str(x) for x in previous.get("recovery_evidence_refs") or () if str(x)],
                    observation_id,
                    f"wakeful-task:{task}:{cycle}:result",
                ]))[-16:]
                if healthy_streak >= RECOVERY_HEALTHY_STREAK:
                    previous["status"] = "RECOVERED_VERIFIED"
                    previous["recovered_at"] = now.isoformat()
                    previous["recovery_verification"] = {
                        "required_consecutive_healthy": RECOVERY_HEALTHY_STREAK,
                        "observed_consecutive_healthy": healthy_streak,
                        "latest_result_status": classification["primary_status"],
                    }
                else:
                    previous["status"] = "RECOVERING"
                _atomic_json(episode_path, previous)

        observation = {
            "schema_version": OBSERVATION_SCHEMA,
            "observation_id": observation_id,
            "observed_at": now.isoformat(),
            "task": task,
            "cycle": cycle,
            "abnormal": bool(classification["abnormal"]),
            "subsystem": subsystem,
            "primary_symptom": primary_symptom,
            "severity": round(severity, 6),
            "episode_id": episode_id,
            "symptoms": classification["symptoms"],
            "primary_status": classification["primary_status"],
        }

        recent = [
            dict(row)
            for row in list(state.get("recent_observations") or [])
            if isinstance(row, Mapping)
        ]
        recent.append(observation)
        recent = recent[-MAX_RECENT_OBSERVATIONS:]

        active = [payload for _path, payload in self._open_episodes()]
        fault_scores: dict[str, dict[str, Any]] = {}
        for payload in active:
            domain = str(payload.get("subsystem") or "unknown")
            severity_value = _bounded(
                payload.get("current_severity"),
                _bounded(payload.get("max_severity")),
            )
            occurrences = max(1, int(payload.get("occurrences") or 1))
            confidence = min(0.98, 0.45 + 0.09 * min(5, occurrences) + 0.30 * severity_value)
            prior = fault_scores.get(domain)
            row = {
                "subsystem": domain,
                "confidence": round(confidence, 6),
                "severity": round(severity_value, 6),
                "episode_ids": [str(payload.get("episode_id") or "")],
                "reason": str(payload.get("primary_symptom") or "unlocalized"),
            }
            if prior is None:
                fault_scores[domain] = row
            else:
                prior["confidence"] = round(max(float(prior["confidence"]), confidence), 6)
                prior["severity"] = round(max(float(prior["severity"]), severity_value), 6)
                prior["episode_ids"] = list(dict.fromkeys([
                    *list(prior.get("episode_ids") or []),
                    str(payload.get("episode_id") or ""),
                ]))[:8]

        suspected = sorted(
            fault_scores.values(),
            key=lambda row: (-float(row["confidence"]), -float(row["severity"]), str(row["subsystem"])),
        )[:8]
        active_ids = [
            str(payload.get("episode_id") or "")
            for payload in active
            if str(payload.get("episode_id") or "")
        ]
        symptom_names = list(dict.fromkeys(
            str(payload.get("primary_symptom") or "")
            for payload in active
            if str(payload.get("primary_symptom") or "")
        ))[:12]
        if active and distress < max(_bounded(row.get("current_severity")) for row in active):
            distress = max(_bounded(row.get("current_severity")) for row in active)
        status = (
            "ILL" if distress >= 0.55 or any(float(row["severity"]) >= 0.60 for row in suspected)
            else "WATCH" if active or distress >= 0.20
            else "HEALTHY"
        )
        new_state = {
            "schema_version": STATE_SCHEMA,
            "status": status,
            "updated_at": now.isoformat(),
            "distress": round(distress, 6),
            "confidence_in_self": round(max(0.0, 1.0 - 0.80 * distress), 6),
            "active_episode_ids": active_ids,
            "suspected_faults": suspected,
            "symptoms": symptom_names,
            "latest_observation_id": observation_id,
            "recent_observations": recent,
        }
        _atomic_json(self.state_path, new_state)
        _atomic_json(self.root / "latest_observation.json", observation)

        diagnostic_failure = None
        if should_diagnose and episode_id:
            diagnostic_failure = {
                "status": "ERROR",
                "task": task,
                "cycle": observation_id,
                "error_type": "INTEROCEPTIVE_FAULT",
                "message": f"interoceptive {primary_symptom} in {subsystem}; task={task}",
                "failure_site": f"interoception:{subsystem}",
                "interoceptive_episode_id": episode_id,
                "severity": round(severity, 6),
            }
        return {
            "status": "SYMPTOM_OBSERVED" if classification["abnormal"] else "HEALTHY_OBSERVED",
            "observation": observation,
            "projection": new_state,
            "episode": episode_payload,
            "should_diagnose": should_diagnose,
            "diagnostic_failure": diagnostic_failure,
        }


__all__ = [
    "EPISODE_SCHEMA",
    "InteroceptiveFaultSystem",
    "MAX_RECENT_OBSERVATIONS",
    "OBSERVATION_SCHEMA",
    "RECOVERY_HEALTHY_STREAK",
    "STATE_SCHEMA",
    "classify_task_result",
    "read_interoceptive_projection",
]
