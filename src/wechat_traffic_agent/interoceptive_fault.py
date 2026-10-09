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


from .interoception_task_classifier import _bounded, classify_task_result


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

    def __init__(
        self,
        state_root: Path,
        *,
        l1_decisions: Any | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "interoception"
        self.state_path = self.root / "state.json"
        self.episodes_root = self.root / "episodes"
        self._l1_decisions = l1_decisions

    def _l1(self):
        if self._l1_decisions is None:
            from .l1_decisions import FerroL1Decisions

            self._l1_decisions = FerroL1Decisions(state_root=self.state_root)
        return self._l1_decisions

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

        from .interoception_health_assessment import enrich_l1_health
        classification, l1_health = enrich_l1_health(
            task, classification, recent_same_task, l1=self._l1(),
        )

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

        from .interoception_episode_ledger import InteroceptionEpisodeLedger
        episode_id, episode_payload, should_diagnose = InteroceptionEpisodeLedger(
            open_episodes=self._open_episodes, episode_path=self._episode_path,
            read_json=_read_json, write_json=_atomic_json,
            episode_schema=EPISODE_SCHEMA, recovery_streak=RECOVERY_HEALTHY_STREAK,
        ).update(
            task=task, now=now, subsystem=subsystem, primary_symptom=primary_symptom,
            severity=severity, classification=classification,
            l1_health=l1_health, observation_id=observation_id, cycle=cycle,
        )

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
            "l1_health": dict(l1_health),
        }

        from .interoception_state_projection import compose_interoception_state
        new_state = compose_interoception_state(
            previous_state=state, observation=observation,
            active=[payload for _path, payload in self._open_episodes()],
            distress=distress, l1_health=l1_health, now=now,
            bounded=_bounded, schema=STATE_SCHEMA,
            recent_limit=MAX_RECENT_OBSERVATIONS,
        )
        _atomic_json(self.state_path, new_state)
        _atomic_json(self.root / "latest_observation.json", observation)

        from .interoception_diagnosis_receipt import diagnostic_failure_receipt
        diagnosis_active, diagnostic_failure = diagnostic_failure_receipt(
            task=task, classification=classification, episode_payload=episode_payload,
            episode_id=episode_id, observation_id=observation_id,
            primary_symptom=primary_symptom, subsystem=subsystem,
            severity=severity, should_diagnose=should_diagnose,
        )
        return {
            "status": "SYMPTOM_OBSERVED" if classification["abnormal"] else "HEALTHY_OBSERVED",
            "observation": observation,
            "projection": new_state,
            "episode": episode_payload,
            "should_diagnose": should_diagnose,
            "diagnosis_active": diagnosis_active,
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
