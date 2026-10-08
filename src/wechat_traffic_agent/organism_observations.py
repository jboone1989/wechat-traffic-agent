"""Read organism evidence from existing state stores, without lifecycle writes.

This module is the sole file-reading adapter for organism resources and
settled world learning in the refresh path. It does not infer health status.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ResourceObservations:
    host_at: Any
    host_pressure: float | None
    llm_at: Any
    llm_resources_at: Any
    model_availability: float | None
    free_model_availability: float | None
    monetary_pressure: float | None
    model_resource_pressure: float | None
    token_pressure: float | None
    survival_at: Any
    service_health: float | None
    network_health: float | None


@dataclass(frozen=True)
class RewardObservations:
    reward_rows: list
    reward_at: Any
    recent_reward: float
    long_term_reward: float
    reward_confidence: float


class OrganismObservationReader:
    def __init__(self, state_root: Path, *, read: Callable, parse_time: Callable,
                 clamp: Callable, pressure: Callable):
        self.state_root, self.read = Path(state_root), read
        self.parse_time, self.clamp, self.pressure = parse_time, clamp, pressure

    def _model_resources(self):
        host_policy = self.read(
            self.state_root / "application" / "survival_resources" / "policy.json"
        )
        host_at = self.parse_time(host_policy.get("observed_at"))
        host_pressure = self.pressure(host_policy.get("pressure"))

        llm_policy = self.read(
            self.state_root / "application" / "llm_resource_pool" / "policy.json"
        )
        llm_at = self.parse_time(llm_policy.get("observed_at"))
        llm_pressure = self.pressure(llm_policy.get("pressure"))
        llm_resources = self.read(
            self.state_root / "application" / "llm_resource_pool" / "resources.json"
        )
        llm_resources_at = self.parse_time(llm_resources.get("observed_at"))
        llm_rows = [
            row for row in (llm_resources.get("resources") or ())
            if isinstance(row, Mapping)
        ]
        model_scores = [
            self.clamp(float(row.get("quality_score") or 0.0))
            * self.clamp(float(row.get("success_rate") or 0.0))
            for row in llm_rows
            if isinstance(row.get("quality_score"), (int, float))
            and isinstance(row.get("success_rate"), (int, float))
        ]
        model_availability = max(model_scores, default=None)
        free_model_scores = [
            self.clamp(float(row.get("quality_score") or 0.0))
            * self.clamp(float(row.get("success_rate") or 0.0))
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
                self.clamp(1.0 - float(paid_funds) / float(reserve_floor))
            )
        if isinstance(runway_days, (int, float)):
            monetary_components.append(self.clamp(1.0 - float(runway_days) / 7.0))
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
                    self.clamp(1.0 - float(remaining) / float(limit))
                )
        if free_quota_pressures:
            token_pressure = min(free_quota_pressures)
        elif isinstance(free_model_availability, (int, float)):
            # Unknown quota must not be interpreted as exhausted capacity when
            # a free route has recent empirical success.
            token_pressure = 1.0 - float(free_model_availability)
        else:
            token_pressure = llm_pressure

        return {
            "host_at": host_at, "host_pressure": host_pressure,
            "llm_at": llm_at, "llm_resources_at": llm_resources_at,
            "model_availability": model_availability,
            "free_model_availability": free_model_availability,
            "monetary_pressure": monetary_pressure,
            "model_resource_pressure": model_resource_pressure,
            "token_pressure": token_pressure,
        }

    def _services(self):
        survival_resources = self.read(
            self.state_root / "application" / "survival_resources" / "resources.json"
        )
        survival_at = self.parse_time(survival_resources.get("observed_at"))
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

        return {
            "survival_at": survival_at, "service_health": service_health,
            "network_health": network_health,
        }

    def _rewards(self, now):
        world_learning = self.read(
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
                observed = self.parse_time(settlement.get("observed_at"))
                signal = settlement.get("learning_signal")
                if observed is None or not isinstance(signal, (int, float)):
                    continue
                reward_rows.append((observed, self.clamp((float(signal) + 1.0) / 2.0)))
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

        return RewardObservations(
            reward_rows, reward_at, recent_reward, long_term_reward, reward_confidence,
        )

    def read_observations(self, now):
        resources = ResourceObservations(
            **self._model_resources(), **self._services(),
        )
        return resources, self._rewards(now)
