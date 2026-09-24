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

    def refresh(
        self,
        *,
        now: datetime,
        neural_life: Mapping[str, Any],
        resource_pressure: str,
        pending_plan_count: int,
        last_cognition_at: datetime | None,
        last_action_at: datetime | None,
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("organism state refresh requires timezone-aware time")
        now = now.astimezone(timezone.utc)
        body = neural_life.get("body") if isinstance(neural_life.get("body"), Mapping) else {}
        affect = neural_life.get("affect") if isinstance(neural_life.get("affect"), Mapping) else {}
        features = (
            neural_life.get("neural_features")
            if isinstance(neural_life.get("neural_features"), Mapping)
            else {}
        )
        body_at = _dt(body.get("observed_at")) or _dt(neural_life.get("updated_at"))

        host_policy = _read(
            self.state_root / "application" / "survival_resources" / "policy.json"
        )
        host_at = _dt(host_policy.get("observed_at"))
        host_pressure = self._pressure_value(host_policy.get("pressure"))

        llm_policy = _read(
            self.state_root / "application" / "llm_resource_pool" / "policy.json"
        )
        llm_at = _dt(llm_policy.get("observed_at"))
        llm_pressure = self._pressure_value(llm_policy.get("pressure"))
        llm_resources = _read(
            self.state_root / "application" / "llm_resource_pool" / "resources.json"
        )
        llm_resources_at = _dt(llm_resources.get("observed_at"))
        llm_rows = [
            row for row in (llm_resources.get("resources") or ())
            if isinstance(row, Mapping)
        ]
        model_scores = [
            _clamp(float(row.get("quality_score") or 0.0))
            * _clamp(float(row.get("success_rate") or 0.0))
            for row in llm_rows
            if isinstance(row.get("quality_score"), (int, float))
            and isinstance(row.get("success_rate"), (int, float))
        ]
        model_availability = max(model_scores, default=None)
        free_model_scores = [
            _clamp(float(row.get("quality_score") or 0.0))
            * _clamp(float(row.get("success_rate") or 0.0))
            for row in llm_rows
            if str(row.get("pricing_class") or "").upper() == "FREE"
            and isinstance(row.get("quality_score"), (int, float))
            and isinstance(row.get("success_rate"), (int, float))
        ]
        free_model_availability = max(free_model_scores, default=None)

        paid_funds = llm_policy.get("paid_available_funds")
        reserve_floor = llm_policy.get("paid_reserve_floor")
        runway_days = llm_policy.get("runway_days")
        monetary_components: list[float] = []
        if (
            isinstance(paid_funds, (int, float))
            and isinstance(reserve_floor, (int, float))
            and float(reserve_floor) > 0
        ):
            monetary_components.append(
                _clamp(1.0 - float(paid_funds) / float(reserve_floor))
            )
        if isinstance(runway_days, (int, float)):
            monetary_components.append(_clamp(1.0 - float(runway_days) / 7.0))
        monetary_pressure = (
            max(monetary_components)
            if monetary_components
            else llm_pressure
        )
        if isinstance(free_model_availability, (int, float)):
            model_resource_pressure = min(
                1.0 if llm_pressure is None else float(llm_pressure),
                1.0 - float(free_model_availability),
            )
        else:
            model_resource_pressure = llm_pressure

        free_quota_pressures: list[float] = []
        for row in llm_rows:
            if str(row.get("pricing_class") or "").upper() != "FREE":
                continue
            limit = row.get("quota_limit")
            remaining = row.get("quota_remaining")
            if (
                isinstance(limit, (int, float))
                and not isinstance(limit, bool)
                and float(limit) > 0
                and isinstance(remaining, (int, float))
                and not isinstance(remaining, bool)
            ):
                free_quota_pressures.append(
                    _clamp(1.0 - float(remaining) / float(limit))
                )
        if free_quota_pressures:
            token_pressure = min(free_quota_pressures)
        elif isinstance(free_model_availability, (int, float)):
            # Unknown quota must not be interpreted as exhausted capacity when
            # a free route has recent empirical success.
            token_pressure = 1.0 - float(free_model_availability)
        else:
            token_pressure = llm_pressure

        survival_resources = _read(
            self.state_root / "application" / "survival_resources" / "resources.json"
        )
        survival_at = _dt(survival_resources.get("observed_at"))
        survival_rows = [
            row for row in (survival_resources.get("resources") or ())
            if isinstance(row, Mapping)
        ]
        service_rows = [
            row for row in survival_rows
            if str(row.get("domain") or "").upper() == "SERVICE"
        ]
        primary_service_rows = [
            row for row in service_rows
            if str(row.get("kind") or "").upper() == "PRIMARY_STATEFUL"
        ]
        health_rows = primary_service_rows or service_rows
        service_values = [
            1.0 if str(row.get("status") or "").upper() == "ACTIVE"
            else 0.45 if str(row.get("status") or "").upper() in {"WATCH", "DEGRADED"}
            else 0.0
            for row in health_rows
        ]
        service_health = (
            sum(service_values) / len(service_values)
            if service_values else None
        )
        remote_rows = [
            row for row in survival_rows
            if str(row.get("domain") or "").upper() == "REMOTE_MACHINE"
        ]
        network_values = [
            (
                1.0
                if str(row.get("status") or "").upper() == "ACTIVE"
                and str(row.get("receiver_status") or "").upper() == "VERIFIED"
                else 0.55
                if str(row.get("status") or "").upper() == "ACTIVE"
                else 0.0
            )
            for row in remote_rows
        ]
        network_health = max(network_values, default=None)

        world_learning = _read(
            self.state_root / "application" / "world_learning.json"
        )
        reward_rows: list[tuple[datetime, float]] = []
        world_actions = (
            world_learning.get("actions")
            if isinstance(world_learning.get("actions"), Mapping)
            else {}
        )
        for action in world_actions.values():
            if not isinstance(action, Mapping):
                continue
            settlements = (
                action.get("settlements")
                if isinstance(action.get("settlements"), Mapping)
                else {}
            )
            for settlement in settlements.values():
                if not isinstance(settlement, Mapping):
                    continue
                observed = _dt(settlement.get("observed_at"))
                signal = settlement.get("learning_signal")
                if observed is None or not isinstance(signal, (int, float)):
                    continue
                reward_rows.append((observed, _clamp((float(signal) + 1.0) / 2.0)))
        reward_rows.sort(key=lambda item: item[0])
        reward_at = reward_rows[-1][0] if reward_rows else now
        recent_reward = (
            sum(value for _, value in reward_rows[-8:]) / len(reward_rows[-8:])
            if reward_rows else 0.5
        )
        long_term_reward = (
            sum(value for _, value in reward_rows[-64:]) / len(reward_rows[-64:])
            if reward_rows else 0.5
        )
        reward_confidence = min(1.0, len(reward_rows) / 8.0) if reward_rows else 0.25

        runtime_pressure = self._pressure_value(resource_pressure)
        runtime_at = now

        idle_hours = (
            min(24.0, max(0.0, (now - last_cognition_at.astimezone(timezone.utc)).total_seconds() / 3600.0))
            if last_cognition_at is not None
            else 6.0
        )
        action_idle_hours = (
            min(24.0, max(0.0, (now - last_action_at.astimezone(timezone.utc)).total_seconds() / 3600.0))
            if last_action_at is not None
            else 6.0
        )

        workload = _clamp(max(0, int(pending_plan_count)) / 12.0)
        fatigue = _clamp(
            0.42 * float(affect.get("arousal") or 0.0)
            + 0.28 * float(affect.get("frustration") or 0.0)
            + 0.30 * workload
        )
        novelty = _clamp(
            0.55 * float(affect.get("curiosity") or 0.0)
            + 0.45 * float(affect.get("boredom") or 0.0)
        )
        opportunity = _clamp(float(features.get("opportunity") or 0.0))
        threat = _clamp(
            max(
                float(features.get("threat_risk") or 0.0),
                float(features.get("network_error_ratio") or 0.0),
            )
        )
        social_state = _clamp(float(affect.get("social_need") or 0.0))
        energy = _clamp(
            1.0
            - 0.62 * fatigue
            - 0.23 * workload
            - 0.15 * max(
                float(body.get("cpu_pressure") or 0.0),
                float(body.get("memory_pressure") or 0.0),
            )
        )

        signals = {
            "compute_pressure": self._signal(
                name="compute_pressure",
                value=float(body["cpu_pressure"]) if isinstance(body.get("cpu_pressure"), (int, float)) else None,
                observed_at=body_at,
                source="neural_life.body.cpu",
                now=now,
            ),
            "memory_pressure": self._signal(
                name="memory_pressure",
                value=float(body["memory_pressure"]) if isinstance(body.get("memory_pressure"), (int, float)) else None,
                observed_at=body_at,
                source="neural_life.body.memory",
                now=now,
            ),
            "storage_pressure": self._signal(
                name="storage_pressure",
                value=float(body["disk_pressure"]) if isinstance(body.get("disk_pressure"), (int, float)) else None,
                observed_at=body_at,
                source="neural_life.body.disk",
                now=now,
            ),
            "host_resource_pressure": self._signal(
                name="host_resource_pressure",
                value=host_pressure,
                observed_at=host_at,
                source="survival_resources.policy",
                now=now,
            ),
            "model_resource_pressure": self._signal(
                name="model_resource_pressure",
                value=model_resource_pressure,
                observed_at=max(filter(None, (llm_at, llm_resources_at)), default=None),
                source="llm_resource_pool.free_route_availability",
                now=now,
            ),
            "monetary_pressure": self._signal(
                name="monetary_pressure",
                value=monetary_pressure,
                observed_at=llm_at,
                source="llm_resource_pool.policy.funds+runway",
                now=now,
            ),
            "token_pressure": self._signal(
                name="token_pressure",
                value=token_pressure,
                observed_at=max(filter(None, (llm_at, llm_resources_at)), default=None),
                source="llm_resource_pool.free_quota_or_empirical_availability",
                now=now,
            ),
            "model_availability": self._signal(
                name="model_availability",
                value=model_availability,
                observed_at=llm_resources_at,
                source="llm_resource_pool.resources.quality*success",
                now=now,
            ),
            "service_health": self._signal(
                name="service_health",
                value=service_health,
                observed_at=survival_at,
                source="survival_resources.primary_service",
                now=now,
            ),
            "network_health": self._signal(
                name="network_health",
                value=network_health,
                observed_at=survival_at,
                source="survival_resources.remote_hosts",
                now=now,
            ),
            "runtime_resource_pressure": self._signal(
                name="runtime_resource_pressure",
                value=runtime_pressure,
                observed_at=runtime_at,
                source="wakeful.resource_pressure",
                now=now,
            ),
            "workload_pressure": self._signal(
                name="workload_pressure", value=workload, observed_at=now,
                source="wakeful.pending_plans", now=now,
            ),
            "fatigue": self._signal(
                name="fatigue", value=fatigue, observed_at=now,
                source="neural_life.affect+workload", now=now,
            ),
            "energy": self._signal(
                name="energy", value=energy, observed_at=now,
                source="organism_state.energy_balance", now=now,
            ),
            "recent_reward": self._signal(
                name="recent_reward", value=recent_reward, observed_at=reward_at,
                source=(
                    "world_learning.settled"
                    if reward_rows else "world_learning.neutral_prior_no_settlement"
                ),
                now=now, confidence=reward_confidence,
            ),
            "long_term_reward": self._signal(
                name="long_term_reward", value=long_term_reward, observed_at=reward_at,
                source=(
                    "world_learning.settled"
                    if reward_rows else "world_learning.neutral_prior_no_settlement"
                ),
                now=now, confidence=reward_confidence,
            ),
            "novelty_need": self._signal(
                name="novelty_need", value=novelty, observed_at=now,
                source="neural_life.affect", now=now,
            ),
            "social_need": self._signal(
                name="social_need", value=social_state, observed_at=now,
                source="neural_life.affect", now=now,
            ),
            "opportunity": self._signal(
                name="opportunity", value=opportunity, observed_at=now,
                source="neural_life.neural_features", now=now,
            ),
            "threat": self._signal(
                name="threat", value=threat, observed_at=now,
                source="neural_life.neural_features", now=now,
            ),
            "cognition_idle": self._signal(
                name="cognition_idle", value=_clamp(idle_hours / 12.0), observed_at=now,
                source="wakeful.last_cognition_at", now=now,
            ),
            "action_idle": self._signal(
                name="action_idle", value=_clamp(action_idle_hours / 12.0), observed_at=now,
                source="wakeful.last_action_at", now=now,
            ),
        }

        unknown = sorted(
            name for name, row in signals.items()
            if str(row.get("freshness")) != "FRESH"
        )
        hard_pressure_names = {
            "compute_pressure",
            "memory_pressure",
            "storage_pressure",
            "host_resource_pressure",
            "model_resource_pressure",
            "token_pressure",
            "runtime_resource_pressure",
            "workload_pressure",
        }
        pressure_values = [
            float(row["value"])
            for name, row in signals.items()
            if name in hard_pressure_names and row.get("freshness") == "FRESH"
            and isinstance(row.get("value"), (int, float))
        ]
        worst_pressure = max(pressure_values, default=1.0 if unknown else 0.0)
        uncertainty = _clamp(len(unknown) / max(1, len(signals)))
        availability_penalty = (
            1.0 - model_availability
            if isinstance(model_availability, (int, float))
            else 1.0
        )
        service_penalty = (
            1.0 - service_health
            if isinstance(service_health, (int, float))
            else 1.0
        )
        network_penalty = (
            1.0 - network_health
            if isinstance(network_health, (int, float))
            else 1.0
        )
        vitality = _clamp(
            1.0
            - 0.42 * worst_pressure
            - 0.14 * fatigue
            - 0.12 * threat
            - 0.10 * uncertainty
            - 0.10 * availability_penalty
            - 0.07 * service_penalty
            - 0.05 * network_penalty
        )
        summary = {
            "vitality": round(vitality, 6),
            "energy": round(energy, 6),
            "model_availability": (
                round(model_availability, 6)
                if isinstance(model_availability, (int, float)) else None
            ),
            "service_health": (
                round(service_health, 6)
                if isinstance(service_health, (int, float)) else None
            ),
            "network_health": (
                round(network_health, 6)
                if isinstance(network_health, (int, float)) else None
            ),
            "recent_reward": round(recent_reward, 6),
            "long_term_reward": round(long_term_reward, 6),
            "uncertainty": round(uncertainty, 6),
            "worst_pressure": round(worst_pressure, 6),
            "monetary_pressure": (
                round(float(monetary_pressure), 6)
                if isinstance(monetary_pressure, (int, float)) else None
            ),
            "free_model_availability": (
                round(float(free_model_availability), 6)
                if isinstance(free_model_availability, (int, float)) else None
            ),
            "unknown_signal_count": len(unknown),
            "unknown_signals": unknown,
            "status": (
                "UNKNOWN" if unknown
                else "CRITICAL" if worst_pressure >= 0.90
                else "CONSERVE" if worst_pressure >= 0.65
                else "WATCH" if worst_pressure >= 0.35
                else "NORMAL"
            ),
        }
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
