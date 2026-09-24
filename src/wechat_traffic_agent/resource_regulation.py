"""Host-side admission for Ferro's scheduled and neural work.

The regulator reads persisted resource evidence, never accepts a model's assertion
that resources are healthy, and preserves a small recovery lane under pressure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

ESSENTIAL_TASKS = frozenset({
    "RESOURCE_REVIEW", "NEURAL_INTEROCEPTION", "CHECK_OUTCOMES",
    "VERIFY_PREDICTION", "EVENT_OBSERVE",
})
EXPENSIVE_TASKS = frozenset({
    "CONTENT_COGNITION", "NEURAL_PLAY", "NEURAL_TRANSFER",
    "NEURAL_DEVELOPMENT", "NEURAL_DEVELOPMENT_TRANSFER",
    "NEURAL_EVOLUTION_CONSOLIDATION",
})
PRESSURE_ORDER = {"NORMAL": 0, "WATCH": 1, "CONSERVE": 2, "CRITICAL": 3, "UNKNOWN": 3}
POLICY_MAX_AGE = timedelta(hours=2)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _observed_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


class ResourceRegulator:
    """One deterministic admission rule for every wakeful task entry point."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "resource_regulation.json"

    def pressure(self, now: datetime) -> str:
        now = now.astimezone(timezone.utc)
        policy = _read(self.state_root / "application" / "survival_resources" / "policy.json")
        routing = _read(self.state_root / "application" / "llm_resource_pool" / "policy.json")
        from .llm_resources import eligible_free_resources
        free_available = bool(eligible_free_resources(self.state_root, now))
        states = []
        for domain, row in (("host", policy), ("llm", routing)):
            observed = _observed_time(row.get("observed_at"))
            if observed is None or not timedelta(0) <= now - observed <= POLICY_MAX_AGE:
                value = "UNKNOWN"
            else:
                value = str(row.get("pressure") or "UNKNOWN").upper()
                if value not in PRESSURE_ORDER:
                    value = "UNKNOWN"
            # A verified free route keeps ordinary cognition alive when only
            # the paid account is exhausted; host pressure still wins.
            if domain == "llm" and free_available and value in {"CONSERVE", "CRITICAL", "UNKNOWN"}:
                value = "WATCH"
            states.append(value)
        return max(states, key=lambda value: PRESSURE_ORDER[value])

    def assess(
        self,
        task: str,
        now: datetime,
        *,
        owner_authorized: bool = False,
    ) -> dict[str, Any]:
        task = str(task or "")
        pressure = self.pressure(now)
        neural = _read(self.state_root / "application" / "neural_life.json")
        neural_at = _observed_time(neural.get("updated_at"))
        neural_fresh = neural_at is not None and timedelta(0) <= now.astimezone(timezone.utc) - neural_at <= POLICY_MAX_AGE
        if neural_fresh:
            quality = (neural.get("body") or {}).get("sensor_quality") or {}
            if any(value == "UNKNOWN" for value in quality.values()):
                pressure = "UNKNOWN"
        if task in ESSENTIAL_TASKS:
            return {"allowed": True, "pressure": pressure, "reason": "RECOVERY_LANE"}
        if owner_authorized and task == "CONTENT_COGNITION":
            from .llm_resources import eligible_free_resources
            if eligible_free_resources(self.state_root, now):
                return {
                    "allowed": True,
                    "pressure": pressure,
                    "reason": "OWNER_GOAL_FREE_COGNITION_LANE",
                }
        if neural_fresh and str((neural.get("intention") or {}).get("name") or "") == "REST":
            return {"allowed": False, "pressure": pressure, "reason": "NEURAL_REST",
                    "retry_after_seconds": 300}
        if pressure in {"CONSERVE", "CRITICAL", "UNKNOWN"}:
            return {"allowed": False, "pressure": pressure, "reason": "RESOURCE_PRESSURE",
                    "retry_after_seconds": 300 if pressure != "CRITICAL" else 900}
        state = _read(self.path)
        recent = _observed_time((state.get("last_started") or {}).get(task))
        cooldown = 300 if task in EXPENSIVE_TASKS else 30
        if recent is not None:
            remaining = cooldown - (now.astimezone(timezone.utc) - recent).total_seconds()
            if remaining > 0:
                return {"allowed": False, "pressure": pressure, "reason": "TASK_COOLDOWN",
                        "retry_after_seconds": max(1, int(remaining + .999))}
        return {"allowed": True, "pressure": pressure, "reason": "WITHIN_BUDGET"}

    def defer_seconds(self, task: str, now: datetime) -> float | None:
        result = self.assess(task, now)
        return None if result["allowed"] else float(result["retry_after_seconds"])

    def claim(
        self,
        task: str,
        now: datetime,
        *,
        owner_authorized: bool = False,
    ) -> dict[str, Any]:
        result = self.assess(task, now, owner_authorized=owner_authorized)
        if not result["allowed"]:
            return result
        if task in ESSENTIAL_TASKS:
            return result
        state = _read(self.path)
        started = dict(state.get("last_started") or {})
        started[str(task)] = now.astimezone(timezone.utc).isoformat()
        payload = {"schema_version": "ferro-resource-regulation.v1", "last_started": started}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.path)
        return result
