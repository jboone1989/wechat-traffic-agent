"""Unified, evidence-bound organism state for Ferro.

This module does not call an LLM.  It turns already-observed body, resource,
neural, and runtime evidence into one persistent interoceptive state.  Missing
or stale evidence is represented as UNKNOWN rather than silently becoming
healthy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-organism-state.v1"
MAX_HISTORY = 192
DEFAULT_MAX_AGE = timedelta(hours=2)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
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


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


class OrganismStateService:
    """Build one authoritative body/interoception projection from durable evidence."""

    def __init__(self, state_root: Path, *, max_age: timedelta = DEFAULT_MAX_AGE) -> None:
        self.state_root = Path(state_root)
        self.max_age = max_age
        self.path = self.state_root / "application" / "organism_state.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "updated_at": None,
            "signals": {},
            "summary": {},
            "history": [],
        }

    @staticmethod
    def _pressure_value(value: object) -> float | None:
        mapping = {"NORMAL": 0.10, "WATCH": 0.38, "CONSERVE": 0.68, "CRITICAL": 1.0}
        return mapping.get(str(value or "").upper())

    def _signal(
        self,
        *,
        name: str,
        value: float | None,
        observed_at: datetime | None,
        source: str,
        now: datetime,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        fresh = (
            value is not None
            and observed_at is not None
            and timedelta(0) <= now - observed_at <= self.max_age
        )
        prior = (self.state.get("signals") or {}).get(name)
        prior_value = prior.get("value") if isinstance(prior, Mapping) else None
        trend = None
        if value is not None and isinstance(prior_value, (int, float)):
            trend = round(float(value) - float(prior_value), 6)
        return {
            "value": None if value is None else round(_clamp(value), 6),
            "confidence": round(_clamp(confidence if fresh else 0.0), 6),
            "observed_at": observed_at.isoformat() if observed_at else None,
            "source": source,
            "freshness": "FRESH" if fresh else "UNKNOWN",
            "trend": trend,
        }

    def _persist(self, now: datetime, signals: dict, summary: dict) -> None:
        history = list(self.state.get("history") or ())
        history.append({
            "at": now.isoformat(),
            "vitality": summary["vitality"],
            "uncertainty": summary["uncertainty"],
            "worst_pressure": summary["worst_pressure"],
            "status": summary["status"],
        })
        self.state = {
            "schema_version": SCHEMA,
            "updated_at": now.isoformat(),
            "signals": signals,
            "summary": summary,
            "history": history[-MAX_HISTORY:],
        }
        _write(self.path, self.state)

    def refresh(
        self, *, now: datetime, neural_life: Mapping[str, Any],
        resource_pressure: str, pending_plan_count: int,
        last_cognition_at: datetime | None, last_action_at: datetime | None,
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("organism state refresh requires timezone-aware time")
        now = now.astimezone(timezone.utc)
        from .organism_observations import OrganismObservationReader
        from .organism_signal_calculator import OrganismInputs, OrganismSignalCalculator
        observations = OrganismObservationReader(
            self.state_root, read=_read, parse_time=_dt,
            clamp=_clamp, pressure=self._pressure_value,
        )
        resources, rewards = observations.read_observations(now)
        measured = OrganismInputs(
            now, neural_life, resource_pressure, pending_plan_count,
            last_cognition_at, last_action_at, resources, rewards,
        )
        calculator = OrganismSignalCalculator(
            signal=self._signal, clamp=_clamp,
            pressure=self._pressure_value, parse_time=_dt,
        )
        signals, factors = calculator.calculate(measured)
        summary = calculator.summarize(signals, measured, factors)
        self._persist(now, signals, summary)
        return self.projection()

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "signals": dict(self.state.get("signals") or {}),
            "summary": dict(self.state.get("summary") or {}),
            "history": [dict(row) for row in (self.state.get("history") or [])[-48:]],
            "unknown_is_healthy": False,
        }


__all__ = ["DEFAULT_MAX_AGE", "OrganismStateService", "SCHEMA"]
