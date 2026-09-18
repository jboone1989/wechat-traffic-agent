"""VirtualBrain-backed learning for habitat and capability placement."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from .habitat_placement import (
    AUTH_NONE,
    COST_FREE,
    HabitatPlacementController,
    PlacementContract,
    PlacementDecision,
)


ACTION_KEEP_LOCAL = "KEEP_LOCAL"
ACTION_OFFLOAD = "OFFLOAD_TO_WORKER"
ACTION_MOVE_COLD = "MOVE_COLD_STORAGE"
ACTION_REPLICATE = "REPLICATE_STANDBY"
ACTIONS = (ACTION_KEEP_LOCAL, ACTION_OFFLOAD, ACTION_MOVE_COLD, ACTION_REPLICATE)

PRESSURE_WEIGHT = {
    "NORMAL": 0.0,
    "WATCH": 0.35,
    "CONSERVE": 0.70,
    "CRITICAL": 1.0,
    "UNKNOWN": 0.20,
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("habitat timestamp requires timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _bounded(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class HabitatState:
    observed_at: str
    resource_pressure: str
    disk_free_gib: float | None
    disk_used_ratio: float | None
    mem_available_gib: float | None
    live_workers: int
    worker_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["worker_ids"] = list(self.worker_ids)
        row["evidence_refs"] = list(self.evidence_refs)
        return row


@dataclass(frozen=True)
class OrganCost:
    capability_id: str
    action: str
    sample_count: int
    success_rate: float
    median_latency_ms: float | None
    median_cpu_ms: float | None
    median_rss_delta_mib: float | None
    median_disk_delta_mib: float | None
    median_worker_age_seconds: float | None
    fallback_rate: float
    confidence: float
    provenance: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["provenance"] = list(self.provenance)
        return row


@dataclass(frozen=True)
class PlacementProposal:
    proposal_id: str
    capability_id: str
    action: str
    worker_id: str | None
    utility: float
    utility_components: Mapping[str, float]
    confidence: float
    evidence_refs: tuple[str, ...]
    eligible: bool
    reason: str

    def payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["utility_components"] = dict(self.utility_components)
        row["evidence_refs"] = list(self.evidence_refs)
        return row


@dataclass(frozen=True)
class PlacementOutcomeRecord:
    outcome_id: str
    episode_id: str
    task_id: str
    capability_id: str
    action: str
    worker_id: str | None
    started_at: str
    completed_at: str
    success: bool
    fallback_used: bool
    latency_ms: float
    process_cpu_ms: float
    rss_delta_mib: float
    disk_free_delta_mib: float
    worker_age_seconds: float | None
    error_type: str | None
    actual_utility: float
    evidence_refs: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["evidence_refs"] = list(self.evidence_refs)
        return row


class HabitatLearningEngine:
    """Let VirtualBrain choose placement using measured outcomes and explicit priors."""

    MAX_OUTCOMES = 512
    SCHEMA = "content-habitat-learning:v1"

    def __init__(
        self, *, state_root: Path, mind: Any, placement: HabitatPlacementController,
    ) -> None:
        self.state_root = Path(state_root)
        self.mind = mind
        self.placement = placement
        self.root = self.state_root / "application" / "habitat_learning"
        self.outcomes_path = self.root / "outcomes.json"
        self.latest_state_path = self.root / "habitat-state.json"
        self.latest_decision_path = self.root / "decision-latest.json"

    def outcomes(self) -> list[PlacementOutcomeRecord]:
        if not self.outcomes_path.exists():
            return []
        raw = json.loads(self.outcomes_path.read_text(encoding="utf-8"))
        if (
            type(raw) is not dict
            or raw.get("schema") != self.SCHEMA
            or type(raw.get("outcomes")) is not list
        ):
            raise ValueError("habitat learning outcome schema mismatch")
        rows = []
        for item in raw["outcomes"]:
            data = dict(item)
            data["evidence_refs"] = tuple(data.get("evidence_refs") or ())
            rows.append(PlacementOutcomeRecord(**data))
        return rows

    def _persist_outcome(self, outcome: PlacementOutcomeRecord) -> None:
        rows = self.outcomes()
        if any(row.outcome_id == outcome.outcome_id for row in rows):
            return
        rows.append(outcome)
        rows = rows[-self.MAX_OUTCOMES:]
        _atomic_json(self.outcomes_path, {
            "schema": self.SCHEMA,
            "outcomes": [row.payload() for row in rows],
        })

    def observe_habitat(
        self, *, now: datetime, current_resource: Mapping[str, Any],
        placement_review: Mapping[str, Any],
    ) -> HabitatState:
        workers = [
            row for row in self.placement.registry.workers()
            if row.is_live(now)
        ]
        state = HabitatState(
            observed_at=_iso(now),
            resource_pressure=str(current_resource.get("pressure") or "UNKNOWN"),
            disk_free_gib=(
                float(current_resource["disk_free_gib"])
                if current_resource.get("disk_free_gib") is not None else None
            ),
            disk_used_ratio=(
                float(current_resource["disk_used_ratio"])
                if current_resource.get("disk_used_ratio") is not None else None
            ),
            mem_available_gib=(
                float(current_resource["mem_available_gib"])
                if current_resource.get("mem_available_gib") is not None else None
            ),
            live_workers=len(workers),
            worker_ids=tuple(sorted(row.worker_id for row in workers)),
            evidence_refs=tuple(dict.fromkeys([
                f"habitat-review:{placement_review.get('status', 'UNKNOWN')}",
                *[
                    f"worker-lease:{row.worker_id}:{row.lease_expires_at or 'static'}"
                    for row in workers
                ],
            ])),
        )
        _atomic_json(self.latest_state_path, state.payload())
        return state

    def _history(self, capability_id: str, action: str) -> list[PlacementOutcomeRecord]:
        return [
            row for row in self.outcomes()
            if row.capability_id == capability_id and row.action == action
        ]

    def organ_cost(self, capability_id: str, action: str) -> OrganCost:
        rows = self._history(capability_id, action)
        if not rows:
            # Priors are explicit and low-confidence; they are not represented as observations.
            success = 0.90 if action == ACTION_KEEP_LOCAL else 0.65
            return OrganCost(
                capability_id=capability_id,
                action=action,
                sample_count=0,
                success_rate=success,
                median_latency_ms=None,
                median_cpu_ms=None,
                median_rss_delta_mib=None,
                median_disk_delta_mib=None,
                median_worker_age_seconds=None,
                fallback_rate=0.0,
                confidence=0.35 if action == ACTION_KEEP_LOCAL else 0.15,
                provenance=(f"bootstrap-prior:{capability_id}:{action}:v1",),
            )
        success_rate = sum(1 for row in rows if row.success) / len(rows)
        fallback_rate = sum(1 for row in rows if row.fallback_used) / len(rows)
        worker_ages = [row.worker_age_seconds for row in rows if row.worker_age_seconds is not None]
        confidence = min(1.0, 0.20 + len(rows) / 8.0)
        return OrganCost(
            capability_id=capability_id,
            action=action,
            sample_count=len(rows),
            success_rate=success_rate,
            median_latency_ms=median(row.latency_ms for row in rows),
            median_cpu_ms=median(row.process_cpu_ms for row in rows),
            median_rss_delta_mib=median(row.rss_delta_mib for row in rows),
            median_disk_delta_mib=median(row.disk_free_delta_mib for row in rows),
            median_worker_age_seconds=median(worker_ages) if worker_ages else None,
            fallback_rate=fallback_rate,
            confidence=confidence,
            provenance=tuple(row.outcome_id for row in rows[-8:]),
        )

    @staticmethod
    def _latency_value(latency_ms: float | None, *, prior: float) -> float:
        if latency_ms is None:
            return prior
        return 1.0 / (1.0 + max(0.0, latency_ms) / 1500.0)

    @staticmethod
    def _pressure_relief(
        *, pressure: str, local: OrganCost, action: str,
    ) -> tuple[float, tuple[str, ...]]:
        weight = PRESSURE_WEIGHT.get(pressure, PRESSURE_WEIGHT["UNKNOWN"])
        if action == ACTION_KEEP_LOCAL:
            return 0.0, (f"pressure:{pressure}",)
        if local.sample_count <= 0:
            return 0.10 * weight, (
                f"pressure:{pressure}",
                "bootstrap-prior:unknown-local-organ-cost:v1",
            )
        cpu = max(0.0, float(local.median_cpu_ms or 0.0))
        rss = max(0.0, float(local.median_rss_delta_mib or 0.0))
        # Measured process costs are normalized only to rank alternatives; units remain in OrganCost.
        observed_intensity = min(1.0, cpu / 500.0 + rss / 512.0)
        return weight * observed_intensity, (
            f"pressure:{pressure}",
            *local.provenance,
        )

    def _proposal(
        self, *, contract: PlacementContract, action: str, worker_id: str | None,
        pressure: str, eligible: bool, reason: str,
    ) -> PlacementProposal:
        local = self.organ_cost(contract.capability_id, ACTION_KEEP_LOCAL)
        cost = self.organ_cost(contract.capability_id, action)
        relief, relief_refs = self._pressure_relief(
            pressure=pressure, local=local, action=action,
        )
        reliability = cost.success_rate
        latency_value = self._latency_value(
            cost.median_latency_ms,
            prior=0.70 if action == ACTION_KEEP_LOCAL else 0.45,
        )
        # Public research has no observed token/LLM spend in this execution path.
        money_token_saving = 0.0
        migration_cost = 0.0 if action == ACTION_KEEP_LOCAL else min(
            1.0, 0.10 + cost.fallback_rate
        )
        dependency_risk = 0.0
        worker_refs: tuple[str, ...] = ()
        if worker_id:
            worker = self.placement.registry.by_id(worker_id)
            if worker is None:
                eligible = False
                reason = "selected worker disappeared from registry"
                dependency_risk = 1.0
            else:
                if cost.sample_count == 0:
                    reliability = worker.reliability
                dependency_risk = 1.0 - worker.reliability
                worker_refs = (
                    f"worker:{worker.worker_id}",
                    f"worker-reliability:{worker.reliability:.4f}",
                )
                if cost.median_worker_age_seconds is not None:
                    # Very short-lived workers increase dependency risk, but only from observed age.
                    dependency_risk = max(
                        dependency_risk,
                        1.0 - min(1.0, cost.median_worker_age_seconds / 86400.0),
                    )
        information_gain = 1.0 - cost.confidence
        utility = (
            0.28 * relief
            + 0.28 * reliability
            + 0.12 * latency_value
            + 0.08 * money_token_saving
            + 0.10 * information_gain
            - 0.07 * migration_cost
            - 0.07 * dependency_risk
        )
        if not eligible:
            utility = -1.0
        refs = tuple(dict.fromkeys((
            *relief_refs,
            *cost.provenance,
            *worker_refs,
            "utility-contract:habitat-v1",
            "cost-evidence:no-observed-llm-spend" if contract.capability_id == "content.research"
            else "cost-evidence:unknown",
        )))
        pid_seed = (
            contract.task_id + "\0" + action + "\0" + (worker_id or "core")
        ).encode()
        return PlacementProposal(
            proposal_id="habitat-option:" + sha256(pid_seed).hexdigest()[:24],
            capability_id=contract.capability_id,
            action=action,
            worker_id=worker_id,
            utility=utility,
            utility_components={
                "pressure_relief": relief,
                "reliability": reliability,
                "latency_value": latency_value,
                "money_token_saving": money_token_saving,
                "information_gain": information_gain,
                "migration_cost": migration_cost,
                "dependency_risk": dependency_risk,
            },
            confidence=cost.confidence,
            evidence_refs=refs,
            eligible=eligible,
            reason=reason,
        )

    def decide(
        self, *, contract: PlacementContract, now: datetime,
        resource_pressure: str,
    ) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import ActionProposal, DecisionEpisode

        hard = self.placement.choose(
            contract, now=now, resource_pressure=resource_pressure,
        )
        options = [
            self._proposal(
                contract=contract,
                action=ACTION_KEEP_LOCAL,
                worker_id=None,
                pressure=resource_pressure,
                eligible=True,
                reason="subject core is always an execution fallback",
            )
        ]
        if hard.mode == "REMOTE_WORKER" and hard.worker_id:
            options.append(self._proposal(
                contract=contract,
                action=ACTION_OFFLOAD,
                worker_id=hard.worker_id,
                pressure=resource_pressure,
                eligible=True,
                reason="worker passed PlacementController hard constraints",
            ))
        else:
            options.append(self._proposal(
                contract=contract,
                action=ACTION_OFFLOAD,
                worker_id=hard.worker_id,
                pressure=resource_pressure,
                eligible=False,
                reason=hard.reason,
            ))

        eligible = [row for row in options if row.eligible]
        selected = sorted(
            eligible,
            key=lambda row: (-row.utility, -row.confidence, row.action, row.worker_id or ""),
        )[0]
        awareness = self.mind.session.resource_awareness
        decision_seed = (
            contract.task_id + "\0" + contract.capability_id + "\0habitat-v1"
        ).encode()
        proposal_id = "habitat-proposal:" + sha256(decision_seed).hexdigest()[:24]
        episode_id = "habitat-decision:" + sha256(decision_seed).hexdigest()[:24]
        evidence = tuple(dict.fromkeys(
            ref for row in options for ref in row.evidence_refs
        ))
        if not any(row.proposal_id == proposal_id for row in awareness.proposals):
            awareness.register_proposal(ActionProposal(
                proposal_id=proposal_id,
                action="choose capability habitat placement",
                capability_id=contract.capability_id,
                estimated_cost=0.0,
                expected_external_value=_bounded(selected.utility_components["reliability"]),
                expected_learning_value=_bounded(selected.utility_components["information_gain"]),
                expected_reproduction_value=_bounded(selected.utility_components["pressure_relief"]),
                created_tick=int(self.mind.session.tick),
                evidence_refs=evidence,
            ))
        episode = next(
            (row for row in awareness.episodes if row.proposal_id == proposal_id),
            None,
        )
        decision_for_action = {
            ACTION_KEEP_LOCAL: "EXECUTE",
            ACTION_OFFLOAD: "EXECUTE_CHEAPER",
            ACTION_MOVE_COLD: "DEFER",
            ACTION_REPLICATE: "DEFER",
        }
        action_for_decision = {
            "EXECUTE": ACTION_KEEP_LOCAL,
            "EXECUTE_CHEAPER": ACTION_OFFLOAD,
        }
        vb_options = tuple(dict.fromkeys(decision_for_action[row.action] for row in eligible))
        if episode is None:
            episode = awareness.record_decision(DecisionEpisode(
                episode_id=episode_id,
                proposal_id=proposal_id,
                decision=decision_for_action[selected.action],
                options_considered=vb_options,
                predicted_total_value=_bounded(max(0.0, selected.utility)),
                estimated_cost=0.0,
                created_tick=int(self.mind.session.tick),
                evidence_refs=evidence,
            ))
            self.mind.persist_resource_awareness()
        else:
            selected_action = action_for_decision.get(episode.decision)
            selected = next(
                (row for row in options if row.action == selected_action and row.eligible),
                selected,
            )

        payload = {
            "status": "DECIDED",
            "task_id": contract.task_id,
            "episode_id": episode.episode_id,
            "selected": selected.payload(),
            "candidates": [row.payload() for row in options],
            "resource_pressure": resource_pressure,
            "vb_tick": int(self.mind.session.tick),
        }
        _atomic_json(self.latest_decision_path, payload)
        return payload

    def record_execution(
        self, *, task_id: str, episode_id: str, capability_id: str,
        action: str, worker_id: str | None, started_at: datetime, completed_at: datetime,
        success: bool, fallback_used: bool, latency_ms: float, process_cpu_ms: float,
        rss_delta_mib: float, disk_free_delta_mib: float,
        worker_age_seconds: float | None, error_type: str | None,
        evidence_refs: Sequence[str] = (),
    ) -> PlacementOutcomeRecord:
        from virtualbrain.research.resource_awareness import Outcome, Reflection

        seed = (
            episode_id + "\0" + task_id + "\0" + _iso(completed_at)
        ).encode()
        outcome_id = "habitat-outcome:" + sha256(seed).hexdigest()[:24]
        reliability_value = 1.0 if success else 0.0
        latency_value = self._latency_value(latency_ms, prior=0.0)
        fallback_penalty = 0.25 if fallback_used else 0.0
        actual_utility = _bounded(
            0.60 * reliability_value + 0.30 * latency_value - fallback_penalty
        )
        refs = tuple(dict.fromkeys((
            *[str(ref) for ref in evidence_refs if str(ref).strip()],
            f"measured-latency-ms:{latency_ms:.3f}",
            f"measured-process-cpu-ms:{process_cpu_ms:.3f}",
            f"measured-rss-delta-mib:{rss_delta_mib:.6f}",
            f"measured-disk-free-delta-mib:{disk_free_delta_mib:.6f}",
        )))
        record = PlacementOutcomeRecord(
            outcome_id=outcome_id,
            episode_id=episode_id,
            task_id=task_id,
            capability_id=capability_id,
            action=action,
            worker_id=worker_id,
            started_at=_iso(started_at),
            completed_at=_iso(completed_at),
            success=bool(success),
            fallback_used=bool(fallback_used),
            latency_ms=max(0.0, float(latency_ms)),
            process_cpu_ms=max(0.0, float(process_cpu_ms)),
            rss_delta_mib=float(rss_delta_mib),
            disk_free_delta_mib=float(disk_free_delta_mib),
            worker_age_seconds=(
                None if worker_age_seconds is None else max(0.0, float(worker_age_seconds))
            ),
            error_type=(None if error_type is None else str(error_type)[:120]),
            actual_utility=actual_utility,
            evidence_refs=refs,
        )
        self._persist_outcome(record)

        awareness = self.mind.session.resource_awareness
        if not any(row.episode_id == episode_id for row in awareness.outcomes):
            episode = next(row for row in awareness.episodes if row.episode_id == episode_id)
            vb_outcome = awareness.record_outcome(Outcome(
                outcome_id=outcome_id,
                episode_id=episode_id,
                result_summary=(
                    f"habitat placement {action} success={success} fallback={fallback_used}; "
                    f"latency={latency_ms:.1f}ms cpu={process_cpu_ms:.1f}ms"
                ),
                actual_cost=0.0,
                external_value=reliability_value,
                learning_value=1.0,
                reproduction_value=_bounded(1.0 - fallback_penalty),
                reputation_delta=0.0,
                observed_tick=int(self.mind.session.tick),
                evidence_refs=refs,
            ))
            prediction_error = _bounded(actual_utility - episode.predicted_total_value, -1.0, 1.0)
            waste = 0.0 if success and not fallback_used else (0.5 if success else 1.0)
            awareness.record_reflection(Reflection(
                reflection_id="habitat-reflection:" + sha256(outcome_id.encode()).hexdigest()[:24],
                outcome_id=vb_outcome.outcome_id,
                prediction_error=prediction_error,
                waste_score=waste,
                over_conservatism_score=(
                    0.25 if action == ACTION_KEEP_LOCAL and success and
                    PRESSURE_WEIGHT.get(self.current_pressure(), 0.0) >= 0.7 else 0.0
                ),
                lesson=(
                    f"Measured {action} for {capability_id}; future placement should use this "
                    "latency/reliability/resource evidence instead of the bootstrap prior."
                ),
                created_tick=int(self.mind.session.tick),
                evidence_refs=refs,
            ))
            self.mind.persist_resource_awareness()
        return record

    def current_pressure(self) -> str:
        try:
            projection = self.mind.public_projection().get("resource_awareness") or {}
            current = projection.get("current") or {}
            return str(current.get("pressure") or "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def candidate_catalog(self) -> tuple[dict[str, Any], ...]:
        return (
            {"action": ACTION_KEEP_LOCAL, "execution": "AVAILABLE"},
            {"action": ACTION_OFFLOAD, "execution": "AVAILABLE"},
            {"action": ACTION_MOVE_COLD, "execution": "PLANNED", "requires": "storage worker contract"},
            {"action": ACTION_REPLICATE, "execution": "PLANNED", "requires": "authoritative standby contract"},
        )


__all__ = [
    "ACTION_KEEP_LOCAL", "ACTION_OFFLOAD", "ACTION_MOVE_COLD", "ACTION_REPLICATE",
    "HabitatState", "OrganCost", "PlacementProposal", "PlacementOutcomeRecord",
    "HabitatLearningEngine",
]
