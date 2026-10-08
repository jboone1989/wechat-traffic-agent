"""Pure organism signal computation from observed evidence.

No filesystem access, memory lookups, publishing or action decisions live
here. Unknown/stale input remains UNKNOWN rather than silently healthy.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class OrganismInputs:
    now: datetime
    neural_life: Mapping[str, Any]
    resource_pressure: str
    pending_plan_count: int
    last_cognition_at: datetime | None
    last_action_at: datetime | None
    resources: Any
    rewards: Any


@dataclass(frozen=True)
class NeuralFactors:
    workload: float
    fatigue: float
    novelty: float
    opportunity: float
    threat: float
    social_state: float
    energy: float
    idle_hours: float
    action_idle_hours: float
    runtime_pressure: float | None


class OrganismSignalCalculator:
    def __init__(self, *, signal: Callable, clamp: Callable, pressure: Callable,
                 parse_time: Callable):
        self.signal, self.clamp = signal, clamp
        self.pressure, self.parse_time = pressure, parse_time

    def _factors(self, request, body, affect, features):
        now = request.now
        resource_pressure = request.resource_pressure
        last_cognition_at, last_action_at = request.last_cognition_at, request.last_action_at
        pending_plan_count = request.pending_plan_count
        runtime_pressure = self.pressure(resource_pressure)
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

        workload = self.clamp(max(0, int(pending_plan_count)) / 12.0)
        fatigue = self.clamp(
            0.42 * float(affect.get("arousal") or 0.0)
            + 0.28 * float(affect.get("frustration") or 0.0)
            + 0.30 * workload
        )
        novelty = self.clamp(
            0.55 * float(affect.get("curiosity") or 0.0)
            + 0.45 * float(affect.get("boredom") or 0.0)
        )
        opportunity = self.clamp(float(features.get("opportunity") or 0.0))
        threat = self.clamp(
            max(
                float(features.get("threat_risk") or 0.0),
                float(features.get("network_error_ratio") or 0.0),
            )
        )
        social_state = self.clamp(float(affect.get("social_need") or 0.0))
        energy = self.clamp(
            1.0
            - 0.62 * fatigue
            - 0.23 * workload
            - 0.15 * max(
                float(body.get("cpu_pressure") or 0.0),
                float(body.get("memory_pressure") or 0.0),
            )
        )

        return NeuralFactors(
            workload, fatigue, novelty, opportunity, threat, social_state,
            energy, idle_hours, action_idle_hours, runtime_pressure,
        )

    def _capacity_signals(self, request, body, body_at, factors):
        now, r = request.now, request.resources
        (host_at, host_pressure, llm_at, llm_resources_at, model_availability,
         monetary_pressure, model_resource_pressure, token_pressure,
         survival_at, service_health, network_health) = (
            r.host_at, r.host_pressure, r.llm_at, r.llm_resources_at,
            r.model_availability, r.monetary_pressure, r.model_resource_pressure,
            r.token_pressure, r.survival_at, r.service_health, r.network_health,
        )
        runtime_pressure, workload = factors.runtime_pressure, factors.workload
        runtime_at = now
        return {
            "compute_pressure": self.signal(
                name="compute_pressure",
                value=float(body["cpu_pressure"]) if isinstance(body.get("cpu_pressure"), (int, float)) else None,
                observed_at=body_at,
                source="neural_life.body.cpu",
                now=now,
            ),
            "memory_pressure": self.signal(
                name="memory_pressure",
                value=float(body["memory_pressure"]) if isinstance(body.get("memory_pressure"), (int, float)) else None,
                observed_at=body_at,
                source="neural_life.body.memory",
                now=now,
            ),
            "storage_pressure": self.signal(
                name="storage_pressure",
                value=float(body["disk_pressure"]) if isinstance(body.get("disk_pressure"), (int, float)) else None,
                observed_at=body_at,
                source="neural_life.body.disk",
                now=now,
            ),
            "host_resource_pressure": self.signal(
                name="host_resource_pressure",
                value=host_pressure,
                observed_at=host_at,
                source="survival_resources.policy",
                now=now,
            ),
            "model_resource_pressure": self.signal(
                name="model_resource_pressure",
                value=model_resource_pressure,
                observed_at=max(filter(None, (llm_at, llm_resources_at)), default=None),
                source="llm_resource_pool.free_route_availability",
                now=now,
            ),
            "monetary_pressure": self.signal(
                name="monetary_pressure",
                value=monetary_pressure,
                observed_at=llm_at,
                source="llm_resource_pool.policy.funds+runway",
                now=now,
            ),
            "token_pressure": self.signal(
                name="token_pressure",
                value=token_pressure,
                observed_at=max(filter(None, (llm_at, llm_resources_at)), default=None),
                source="llm_resource_pool.free_quota_or_empirical_availability",
                now=now,
            ),
            "model_availability": self.signal(
                name="model_availability",
                value=model_availability,
                observed_at=llm_resources_at,
                source="llm_resource_pool.resources.quality*success",
                now=now,
            ),
            "service_health": self.signal(
                name="service_health",
                value=service_health,
                observed_at=survival_at,
                source="survival_resources.primary_service",
                now=now,
            ),
            "network_health": self.signal(
                name="network_health",
                value=network_health,
                observed_at=survival_at,
                source="survival_resources.remote_hosts",
                now=now,
            ),
            "runtime_resource_pressure": self.signal(
                name="runtime_resource_pressure",
                value=runtime_pressure,
                observed_at=runtime_at,
                source="wakeful.resource_pressure",
                now=now,
            ),
        }


    def _drive_signals(self, request, factors):
        now, rewards = request.now, request.rewards
        (reward_at, recent_reward, long_term_reward, reward_rows,
         reward_confidence) = (
            rewards.reward_at, rewards.recent_reward, rewards.long_term_reward,
            rewards.reward_rows, rewards.reward_confidence,
        )
        (workload, fatigue, energy, novelty, social_state,
         opportunity, threat, idle_hours, action_idle_hours) = (
            factors.workload, factors.fatigue, factors.energy,
            factors.novelty, factors.social_state, factors.opportunity,
            factors.threat, factors.idle_hours, factors.action_idle_hours,
        )
        return {
            "workload_pressure": self.signal(
                name="workload_pressure", value=workload, observed_at=now,
                source="wakeful.pending_plans", now=now,
            ),
            "fatigue": self.signal(
                name="fatigue", value=fatigue, observed_at=now,
                source="neural_life.affect+workload", now=now,
            ),
            "energy": self.signal(
                name="energy", value=energy, observed_at=now,
                source="organism_state.energy_balance", now=now,
            ),
            "recent_reward": self.signal(
                name="recent_reward", value=recent_reward, observed_at=reward_at,
                source=(
                    "world_learning.settled"
                    if reward_rows else "world_learning.neutral_prior_no_settlement"
                ),
                now=now, confidence=reward_confidence,
            ),
            "long_term_reward": self.signal(
                name="long_term_reward", value=long_term_reward, observed_at=reward_at,
                source=(
                    "world_learning.settled"
                    if reward_rows else "world_learning.neutral_prior_no_settlement"
                ),
                now=now, confidence=reward_confidence,
            ),
            "novelty_need": self.signal(
                name="novelty_need", value=novelty, observed_at=now,
                source="neural_life.affect", now=now,
            ),
            "social_need": self.signal(
                name="social_need", value=social_state, observed_at=now,
                source="neural_life.affect", now=now,
            ),
            "opportunity": self.signal(
                name="opportunity", value=opportunity, observed_at=now,
                source="neural_life.neural_features", now=now,
            ),
            "threat": self.signal(
                name="threat", value=threat, observed_at=now,
                source="neural_life.neural_features", now=now,
            ),
            "cognition_idle": self.signal(
                name="cognition_idle", value=self.clamp(idle_hours / 12.0), observed_at=now,
                source="wakeful.last_cognition_at", now=now,
            ),
            "action_idle": self.signal(
                name="action_idle", value=self.clamp(action_idle_hours / 12.0), observed_at=now,
                source="wakeful.last_action_at", now=now,
            ),
        }



    def calculate(self, request: OrganismInputs):
        neural = request.neural_life
        body = neural.get("body") if isinstance(neural.get("body"), Mapping) else {}
        affect = neural.get("affect") if isinstance(neural.get("affect"), Mapping) else {}
        features = (
            neural.get("neural_features")
            if isinstance(neural.get("neural_features"), Mapping) else {}
        )
        body_at = self.parse_time(body.get("observed_at")) or self.parse_time(neural.get("updated_at"))
        factors = self._factors(request, body, affect, features)
        signals = {
            **self._capacity_signals(request, body, body_at, factors),
            **self._drive_signals(request, factors),
        }
        return signals, factors

    def summarize(self, signals, request: OrganismInputs, factors: NeuralFactors):
        (model_availability, service_health, network_health, monetary_pressure,
         free_model_availability) = (
            request.resources.model_availability, request.resources.service_health,
            request.resources.network_health, request.resources.monetary_pressure,
            request.resources.free_model_availability,
        )
        recent_reward, long_term_reward = (
            request.rewards.recent_reward, request.rewards.long_term_reward,
        )
        fatigue, threat, energy = factors.fatigue, factors.threat, factors.energy
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
        uncertainty = self.clamp(len(unknown) / max(1, len(signals)))
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
        vitality = self.clamp(
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
        return summary
