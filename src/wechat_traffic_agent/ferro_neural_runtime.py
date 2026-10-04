"""Minimal owner of Ferro's live neural state.

This process-level runtime deliberately excludes content, dream, transfer,
development, evolution and repair modules. Those are task workers, not
dependencies of the live neural/state boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from agent_runtime.wakeful import NeuralFeedbackEvent, StimulusEvent, WakefulCore
from agent_runtime.wakeful.plan_store import FilePlanStore
from virtualbrain.research.neural_graph import LIFNeuralGraph

from .wakeful_runtime import (
    _CONNECTOME_AUX_CHANNELS,
    _connectome_config,
    _persist_neural_topology_manifest,
)


class FerroNeuralRuntime:
    """The smallest runtime that owns the production nervous system."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)

        from .neural_life import FerroNeuralLife

        self.neural_life_enabled = os.environ.get(
            "CONTENT_NEURAL_LIFE_ENABLED", "1"
        ).lower() in {"1", "true", "yes", "on"}
        self.neural_life = FerroNeuralLife(self.state_root)

        semantic = LIFNeuralGraph()
        config = _connectome_config(self.state_root)
        self.connectome_enabled = bool(config["enabled"])
        self.connectome_error: dict[str, str] | None = None
        reflex = semantic
        if self.connectome_enabled:
            try:
                from virtualbrain.research.fly_connectome import (
                    FlyDronesBurstEngine,
                    HybridConnectomeNervousSystem,
                )

                base = FlyDronesBurstEngine(
                    source=str(config["source"]),
                    state_path=self.state_root / "application" / "fly_connectome_state.npz",
                    channels=semantic.neuron_names,
                    aux_channels=_CONNECTOME_AUX_CHANNELS,
                    upstream_commit=str(config["upstream_commit"]),
                    electrode_size=int(config["electrode_size"]),
                    input_hz=float(config["input_hz"]),
                    stimulus_ms=float(config["stimulus_ms"]),
                    settle_ms=float(config["settle_ms"]),
                    baseline_ms=float(config["baseline_ms"]),
                )
                if bool(config["neurogenesis_enabled"]):
                    from virtualbrain.research.developmental_connectome import (
                        DevelopmentalConnectomeSubstrate,
                    )

                    substrate = DevelopmentalConnectomeSubstrate(
                        base=base,
                        state_path=self.state_root
                        / "application"
                        / "synthetic_neural_development.json",
                    )
                else:
                    substrate = base
                reflex = HybridConnectomeNervousSystem(
                    semantic=semantic,
                    substrate=substrate,
                )
                _persist_neural_topology_manifest(self.state_root, config)
            except Exception as exc:
                self.connectome_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                }

        self.core = WakefulCore(
            state_path=self.state_root / "application" / "wakeful.json",
            reflex=reflex,
            plan_store=FilePlanStore(self.state_root / "application" / "task_plans"),
        )

        from .neural_identity import (
            BrainCortexLedger,
            NeuralPersonality,
            NeuralSocialSelf,
            NeuralTasteEngine,
            PsychodynamicState,
        )

        substrate = getattr(self.core.reflex, "substrate", None)
        self.neural_taste = NeuralTasteEngine(self.state_root, substrate=substrate)
        self.neural_social = NeuralSocialSelf(self.state_root, substrate=substrate)
        self.neural_personality = NeuralPersonality(self.state_root)
        self.brain_cortex = BrainCortexLedger(self.state_root)
        self.psychodynamics = PsychodynamicState(self.state_root)

    def _brain_lab_projection(self) -> dict[str, Any]:
        path = self.state_root / "application" / "brain_lab.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        environments = row.get("environments")
        return {
            "total_sessions": int(row.get("total_sessions") or 0),
            "environments": dict(environments) if isinstance(environments, dict) else {},
        }

    def rank_neural_taste(self, topics, now: datetime | None = None) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = self.neural_taste.rank_topics(topics, now=now)
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return result

    def observe_social_interaction(
        self,
        *,
        actor: str,
        category: str,
        signal: str,
        now: datetime | None = None,
    ) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        relation = self.neural_social.observe(
            actor=actor,
            category=category,
            signal=signal,
            now=now,
        )
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        self.neural_personality.refresh(
            now=now,
            neural_life=self.neural_life.projection(),
            brain_lab=self._brain_lab_projection(),
            social=self.neural_social.projection(),
        )
        return relation

    def choose_brain_or_cortex(
        self,
        *,
        cycle: str,
        neural_choice: str | None,
        ego_choice: str | None,
        now: datetime | None = None,
    ) -> dict:
        return self.brain_cortex.choose(
            cycle=cycle,
            neural_choice=neural_choice,
            ego_choice=ego_choice,
            now=now,
        )

    def settle_brain_cortex(
        self,
        *,
        cycle: str,
        utility: float,
        evidence_refs=(),
        observed_at: datetime | None = None,
    ) -> dict:
        return self.brain_cortex.settle(
            cycle=cycle,
            utility=utility,
            evidence_refs=evidence_refs,
            observed_at=observed_at,
        )

    def emit_network_error(
        self, *, code: str, observed_at: datetime | None = None
    ) -> None:
        now = observed_at or datetime.now(timezone.utc)
        self.core.emit(
            StimulusEvent(
                event_id=f"network-error:{int(now.timestamp() * 1000)}",
                source="agent-network",
                kind="NETWORK_ERROR",
                observed_at=now,
                salience=0.75,
                novelty=0.4,
                urgency=0.65,
                persistence=0.7,
                payload_ref=f"network-error:{code or 'unknown'}",
            ),
            enqueue=False,
        )

    def emit_network_evidence(
        self, *, evidence_ref: str, observed_at: datetime | None = None
    ) -> None:
        now = observed_at or datetime.now(timezone.utc)
        digest = hashlib.sha256(str(evidence_ref).encode("utf-8")).hexdigest()[:20]
        self.core.emit(
            StimulusEvent(
                event_id=f"network-evidence:{digest}:{int(now.timestamp())}",
                source="agent-network",
                kind="NETWORK_EVIDENCE",
                observed_at=now,
                salience=0.65,
                novelty=0.55,
                urgency=0.35,
                persistence=0.55,
                payload_ref=f"network-evidence:{digest}",
            ),
            enqueue=False,
        )

    def emit_x_mentions(
        self, *, count: int, observed_at: datetime | None = None
    ) -> None:
        if int(count) <= 0:
            return
        now = observed_at or datetime.now(timezone.utc)
        strength = min(1.0, 0.35 + 0.10 * int(count))
        self.core.emit(
            StimulusEvent(
                event_id=f"x-mentions:{int(now.timestamp())}:{int(count)}",
                source="x",
                kind="X_MENTION",
                observed_at=now,
                salience=strength,
                novelty=min(1.0, 0.45 + 0.05 * int(count)),
                urgency=0.35,
                persistence=0.45,
                payload_ref=f"x-mentions-count:{int(count)}",
            ),
            enqueue=False,
        )

    def emit_action_commitment(
        self, *, prediction_id: str, observed_at: datetime | None = None
    ) -> dict:
        prediction_id = str(prediction_id or "").strip()
        if not prediction_id:
            raise ValueError("action commitment requires prediction_id")
        now = observed_at or datetime.now(timezone.utc)
        event_id = f"action:{prediction_id}"
        signal = self.core.emit(
            StimulusEvent(
                event_id=event_id,
                source="content-cognition",
                kind="ACTION_COMMITMENT",
                observed_at=now,
                salience=0.80,
                novelty=0.35,
                urgency=0.65,
                persistence=0.85,
                payload_ref=prediction_id,
            ),
            enqueue=False,
        )
        return {
            "status": "OBSERVED",
            "event_id": event_id,
            "prediction_id": prediction_id,
            "attention_channel": signal.channel,
            "threshold_crossed": signal.threshold_crossed,
        }

    def emit_prediction_due(
        self, *, prediction_ref: str, observed_at: datetime | None = None
    ) -> None:
        now = observed_at or datetime.now(timezone.utc)
        digest = hashlib.sha256(prediction_ref.encode()).hexdigest()[:20]
        self.core.emit(
            StimulusEvent(
                event_id=f"prediction-due:{digest}:{int(now.timestamp())}",
                source="planner",
                kind="PREDICTION_DUE",
                observed_at=now,
                salience=0.85,
                novelty=0.20,
                urgency=0.90,
                persistence=0.85,
                payload_ref=prediction_ref,
            ),
            enqueue=False,
        )

    def emit_interoceptive_fault(
        self,
        fault: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        episode_id = str(fault.get("episode_id") or "interoception:unlocalized")
        subsystem = str(fault.get("subsystem") or "unknown")
        symptom = str(fault.get("primary_symptom") or "unknown")
        projection = (
            fault.get("projection")
            if isinstance(fault.get("projection"), Mapping)
            else {}
        )
        severity = max(
            0.0,
            min(
                1.0,
                max(
                    float(fault.get("severity") or 0.0),
                    float(projection.get("distress") or 0.0),
                ),
            ),
        )
        attention_channel = (
            "resource_pressure" if subsystem == "resources" else "task_urgency"
        )
        event_hash = hashlib.sha256(
            (episode_id + "\0" + subsystem + "\0" + symptom).encode("utf-8")
        ).hexdigest()[:20]
        event_id = f"interoceptive-fault:{event_hash}:{int(now.timestamp() // 300)}"
        signal = self.core.emit(
            StimulusEvent(
                event_id=event_id,
                source="ferro-interoceptive-fault-system",
                kind="INTEROCEPTION_FAULT",
                observed_at=now,
                salience=max(0.08, severity),
                novelty=0.48,
                urgency=max(0.10, severity),
                persistence=0.97,
                payload_ref=episode_id,
                attention_channel=attention_channel,
            ),
            enqueue=False,
        )
        return {
            "status": "OBSERVED",
            "event_id": event_id,
            "episode_id": episode_id,
            "subsystem": subsystem,
            "symptom": symptom,
            "magnitude": round(severity, 6),
            "attention_channel": signal.channel,
            "threshold_crossed": signal.threshold_crossed,
        }

    def set_resource_pressure(self, pressure: str) -> None:
        normalized = str(pressure or "UNKNOWN").upper()
        previous = self.core.snapshot().resource_pressure
        self.core.set_resource_pressure(normalized)
        if normalized == previous:
            return
        now = datetime.now(timezone.utc)
        urgency = {
            "NORMAL": 0.1,
            "WATCH": 0.35,
            "CONSERVE": 0.65,
            "CRITICAL": 1.0,
        }.get(normalized, 0.3)
        self.core.emit(
            StimulusEvent(
                event_id=f"resource:{normalized}:{int(now.timestamp())}",
                source="resource-state",
                kind="RESOURCE_CHANGE",
                observed_at=now,
                salience=urgency,
                novelty=0.35,
                urgency=urgency,
                persistence=0.8,
                payload_ref=f"resource-pressure:{normalized}",
            )
        )

    def apply_prediction_outcome_feedback(self, outcome: dict) -> dict:
        prediction_id = str(outcome.get("prediction_id") or "")
        window = str(outcome.get("window") or "")
        evidence_refs = tuple(
            str(x) for x in outcome.get("evidence_refs") or () if x
        )
        if (
            not prediction_id
            or window not in {"1h", "4h", "24h"}
            or not evidence_refs
        ):
            raise ValueError(
                "prediction outcome feedback requires identity, canonical window, and evidence"
            )
        observed_raw = outcome.get("observed_at")
        if isinstance(observed_raw, datetime):
            observed_at = observed_raw
        else:
            observed_at = datetime.fromisoformat(
                str(observed_raw).replace("Z", "+00:00")
            )
        expected = max(0.0, float(outcome.get("expected_pageviews") or 0.0))
        actual = max(0.0, float(outcome.get("actual_pageviews") or 0.0))
        reliability = max(
            0.0, min(1.0, float(outcome.get("reliability") or 0.0))
        )
        prediction_error = max(
            0.0, min(1.0, float(outcome.get("prediction_error") or 0.0))
        )
        signed_progress = (actual - expected) / (actual + expected + 1.0)
        feedback = NeuralFeedbackEvent(
            outcome_id=f"content-action:{prediction_id}:{window}",
            observed_at=observed_at,
            pathway=("prediction_due", "task_urgency"),
            evidence_refs=evidence_refs,
            source_event_id=f"action:{prediction_id}",
            information_gain=0.25 * reliability,
            prediction_error=prediction_error * reliability,
            goal_progress=max(
                -1.0, min(1.0, signed_progress * reliability)
            ),
        )
        return self.core.apply_neural_feedback(feedback)

    def mark_cognition(self, now: datetime | None = None) -> None:
        self.core.mark_cognition(now)

    def behavioral_projection(self) -> dict:
        snap = self.core.snapshot()
        neural_life = (
            self.neural_life.projection()
            if self.neural_life_enabled
            else {"status": "DISABLED"}
        )
        personality = self.neural_personality.projection()
        return {
            "neural_life": neural_life,
            "personality": personality,
            "psychodynamics": self.psychodynamics.projection(
                neural_life=neural_life,
                personality=personality,
                active_goal_count=len(snap.active_goal_refs),
            ),
        }

    def public_snapshot(self) -> dict:
        snap = self.core.snapshot()
        return {
            "presence": "AWAKE",
            "state": snap.state.value,
            "next_wake_at": (
                snap.next_wake_at.isoformat() if snap.next_wake_at else None
            ),
            "next_wake_reason": snap.next_wake_reason,
            "attention": dict(snap.attention),
            "neural_graph": dict(snap.neural_state),
            "resource_pressure": snap.resource_pressure,
            "last_cognition_at": (
                snap.last_cognition_at.isoformat()
                if snap.last_cognition_at
                else None
            ),
            "last_action_at": (
                snap.last_action_at.isoformat() if snap.last_action_at else None
            ),
            "pending_plan_count": len(snap.pending_plans),
            "cognition_threshold_crossed": bool(snap.cognition_triggers),
            "recent_stimulus_kinds": [
                event.kind for event in snap.recent_stimuli[-8:]
            ],
            "neural_life": (
                self.neural_life.projection()
                if self.neural_life_enabled
                else {"status": "DISABLED"}
            ),
            "neural_taste": self.neural_taste.projection(),
            "neural_social": self.neural_social.projection(),
            "personality": self.neural_personality.projection(),
            "brain_vs_cortex": self.brain_cortex.projection(),
            "connectome": {
                "enabled": self.connectome_enabled,
                "status": (
                    "SHADOW_ACTIVE"
                    if isinstance(snap.neural_state.get("substrate"), dict)
                    else "DEGRADED"
                    if self.connectome_error
                    else "DISABLED"
                ),
                "error": (
                    None
                    if self.connectome_error is None
                    else dict(self.connectome_error)
                ),
            },
        }


__all__ = ["FerroNeuralRuntime"]
