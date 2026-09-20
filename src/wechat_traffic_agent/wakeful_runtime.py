"""Content Agent adoption of Agent Runtime Wakeful Core + VirtualBrain LIF."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_runtime.wakeful import (
    NeuralFeedbackEvent, PlannedWake, StimulusEvent, WakefulCore, WakefulState,
)
from virtualbrain.research.neural_graph import LIFNeuralGraph


_DEFAULT_INTERVALS = {
    "OBSERVE_WORLD": 3600,
    "RESOURCE_REVIEW": 3600,
    "CHECK_OUTCOMES": 3600,
    "CHECK_X": 1800,
    "NEURAL_INTEROCEPTION": 300,
    "NEURAL_PLAY": 1800,
    "NEURAL_DREAM": 21600,
    "NEURAL_TRANSFER": 14400,
}

_EXTERNAL_WAKE_TASKS = frozenset({
    "OBSERVE_WORLD",
    "RESOURCE_REVIEW",
    "CHECK_OUTCOMES",
    "CONTENT_COGNITION",
    "REFLECT",
    "CHECK_X",
    "NEURAL_INTEROCEPTION",
    "NEURAL_PLAY",
    "NEURAL_DREAM",
    "NEURAL_TRANSFER",
    "NEURAL_DEVELOPMENT",
    "NEURAL_DEVELOPMENT_TRANSFER",
})


class ContentWakefulRuntime:
    def __init__(self, *, state_root: Path, content_interval_hours: int = 6, watchdog_seconds: int = 1800) -> None:
        self.state_root = Path(state_root)
        self.content_interval_hours = max(1, int(content_interval_hours))
        self.watchdog_seconds = max(60, int(watchdog_seconds))
        from .neural_life import FerroNeuralLife
        self.neural_life_enabled = os.environ.get("CONTENT_NEURAL_LIFE_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
        self.neural_life = FerroNeuralLife(self.state_root)
        semantic = LIFNeuralGraph()
        self.connectome_enabled = os.environ.get("CONTENT_FLY_CONNECTOME_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        self.connectome_error: dict[str, str] | None = None
        reflex = semantic
        if self.connectome_enabled:
            try:
                from virtualbrain.research.fly_connectome import FlyDronesBurstEngine, HybridConnectomeNervousSystem
                source = os.environ.get("CONTENT_FLY_CONNECTOME_SOURCE", "minifly")
                base_substrate = FlyDronesBurstEngine(
                    source=source,
                    state_path=self.state_root / "application" / "fly_connectome_state.npz",
                    channels=semantic.neuron_names,
                    upstream_commit=os.environ.get("CONTENT_FLY_CONNECTOME_UPSTREAM_COMMIT", "3e26934"),
                    electrode_size=int(os.environ.get("CONTENT_FLY_CONNECTOME_ELECTRODE_SIZE", "64")),
                    input_hz=float(os.environ.get("CONTENT_FLY_CONNECTOME_INPUT_HZ", "120")),
                    stimulus_ms=float(os.environ.get("CONTENT_FLY_CONNECTOME_STIMULUS_MS", "10")),
                    settle_ms=float(os.environ.get("CONTENT_FLY_CONNECTOME_SETTLE_MS", "40")),
                    baseline_ms=float(os.environ.get("CONTENT_FLY_CONNECTOME_BASELINE_MS", "20")),
                )
                neurogenesis_enabled = os.environ.get(
                    "CONTENT_NEUROGENESIS_ENABLED", "1"
                ).lower() in {"1", "true", "yes", "on"}
                if neurogenesis_enabled:
                    from virtualbrain.research.developmental_connectome import DevelopmentalConnectomeSubstrate
                    substrate = DevelopmentalConnectomeSubstrate(
                        base=base_substrate,
                        state_path=self.state_root / "application" / "synthetic_neural_development.json",
                    )
                else:
                    substrate = base_substrate
                reflex = HybridConnectomeNervousSystem(semantic=semantic, substrate=substrate)
            except Exception as exc:
                # The connectome is initially SHADOW and must never become a life dependency.
                self.connectome_error = {"error_type": type(exc).__name__, "message": str(exc)[:300]}
        self.core = WakefulCore(
            state_path=self.state_root / "application" / "wakeful.json",
            reflex=reflex,
        )
        from .neural_playground import FerroBrainLab
        self.brain_lab = FerroBrainLab(
            self.state_root,
            substrate=getattr(self.core.reflex, "substrate", None),
        )
        from .neural_dreaming import FerroDreamEngine
        self.dream_engine = FerroDreamEngine(
            self.state_root,
            substrate=getattr(self.core.reflex, "substrate", None),
        )
        from .neural_identity import (
            BrainCortexLedger,
            NeuralPersonality,
            NeuralSocialSelf,
            NeuralTasteEngine,
            PsychodynamicState,
        )
        from .neural_motor import NeuralMotorCortex
        substrate = getattr(self.core.reflex, "substrate", None)
        self.neural_taste = NeuralTasteEngine(self.state_root, substrate=substrate)
        self.neural_social = NeuralSocialSelf(self.state_root, substrate=substrate)
        self.neural_personality = NeuralPersonality(self.state_root)
        self.brain_cortex = BrainCortexLedger(self.state_root)
        self.psychodynamics = PsychodynamicState(self.state_root)
        self.neural_motor = NeuralMotorCortex(self.state_root, substrate=substrate)
        from .neural_transfer import CrossDomainTransferLab
        self.neural_transfer = CrossDomainTransferLab(
            self.state_root,
            substrate=substrate,
            brain_lab=self.brain_lab,
        )
        from .cortex_continuity import CortexContinuityMonitor
        self.cortex_continuity = CortexContinuityMonitor(self.state_root)
        from .developmental_agency import DevelopmentalAgency
        self.developmental_agency = DevelopmentalAgency(self.state_root)
        from .developmental_transfer import DevelopmentalWorldTransferLab
        self.developmental_world_transfer = DevelopmentalWorldTransferLab(
            self.state_root,
            brain_lab=self.brain_lab,
            agency=self.developmental_agency,
        )
        self._wake_request_root = self.state_root / "application" / "wake_requests"

    def _enqueue_external_wake(self, wake: PlannedWake) -> Path:
        """Persist an additive wake intent that survives cross-process state races."""
        self._wake_request_root.mkdir(parents=True, exist_ok=True)
        nonce = hashlib.sha256(
            (
                wake.wake_id
                + "\0"
                + wake.at.astimezone(timezone.utc).isoformat()
                + "\0"
                + str(os.getpid())
                + "\0"
                + datetime.now(timezone.utc).isoformat()
            ).encode("utf-8")
        ).hexdigest()[:20]
        payload = {
            "schema_version": "content-wake-request.v1",
            "wake_id": wake.wake_id,
            "at": wake.at.astimezone(timezone.utc).isoformat(),
            "reason": wake.reason,
            "task": wake.task,
            "relevant_refs": list(wake.relevant_refs),
            "cancel_conditions": list(wake.cancel_conditions),
            "preemptible": bool(wake.preemptible),
        }
        path = self._wake_request_root / f"{nonce}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path

    def _drain_external_wake_requests(self) -> int:
        """Merge external wake intents into the live core without postponing work."""
        if not self._wake_request_root.exists():
            return 0
        current = {
            wake.wake_id: wake
            for wake in self.core.snapshot().pending_plans
        }
        merged = 0
        for path in sorted(self._wake_request_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                task = str(payload.get("task") or "")
                wake_id = str(payload.get("wake_id") or "")
                raw_at = datetime.fromisoformat(str(payload.get("at") or ""))
                if raw_at.tzinfo is None or raw_at.utcoffset() is None:
                    raise ValueError("external wake time must be timezone-aware")
                if task not in _EXTERNAL_WAKE_TASKS:
                    raise ValueError("external wake task is not allowed")
                if wake_id != f"content:{task}":
                    raise ValueError("external wake id does not match task")
                wake = PlannedWake(
                    wake_id=wake_id,
                    at=raw_at.astimezone(timezone.utc),
                    reason=str(payload.get("reason") or task.lower().replace("_", " "))[:300],
                    task=task,
                    relevant_refs=tuple(str(x) for x in payload.get("relevant_refs") or ()),
                    cancel_conditions=tuple(str(x) for x in payload.get("cancel_conditions") or ()),
                    preemptible=bool(payload.get("preemptible", True)),
                )
                existing = current.get(wake_id)
                if existing is None or wake.at < existing.at:
                    self.core.schedule(wake)
                    current[wake_id] = wake
                    merged += 1
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        return merged

    def _watch_external_wake_requests(self, stop: threading.Event) -> None:
        while not stop.wait(0.25):
            self._drain_external_wake_requests()

    def ensure_initial_plans(self, now: datetime) -> None:
        existing = {wake.task for wake in self.core.snapshot().pending_plans}
        for task in ("NEURAL_INTEROCEPTION", "NEURAL_PLAY", "NEURAL_DREAM", "NEURAL_TRANSFER", "OBSERVE_WORLD", "RESOURCE_REVIEW", "CHECK_OUTCOMES", "CONTENT_COGNITION", "REFLECT"):
            if task not in existing:
                self.core.schedule(PlannedWake(
                    wake_id=f"content:{task}", at=now, reason=self._reason(task), task=task,
                ))
        if os.environ.get("CONTENT_X_ENABLED", "").lower() in {"1", "true", "yes", "on"} and "CHECK_X" not in existing:
            self.core.schedule(PlannedWake(
                wake_id="content:CHECK_X", at=now, reason=self._reason("CHECK_X"), task="CHECK_X",
            ))
        self.sync_prediction_wakes(now)

    def sync_prediction_wakes(self, now: datetime) -> None:
        from .prospective_predictions import ProspectivePredictionLedger
        ledger = ProspectivePredictionLedger(self.state_root)
        rows = {row.prediction_id: row for row in ledger.open()}
        for wake in self.core.snapshot().pending_plans:
            ref = wake.wake_id[5:] if wake.wake_id.startswith("wake:prediction:") else None
            if ref is not None and ref not in rows:
                self.core.cancel(wake.wake_id)
        for row in rows.values():
            at = row.due_at
            if row.checks and at <= now:
                at = max(now, row.checks[-1].checked_at + timedelta(hours=1))
            elif at <= now:
                at = now
            self.core.schedule(PlannedWake(
                wake_id=f"wake:{row.prediction_id}", at=at,
                reason=f"verify prediction {row.prediction_id}", task="VERIFY_PREDICTION",
                relevant_refs=(row.prediction_id,),
            ))

    def schedule_next(self, task: str, now: datetime) -> None:
        if task == "CONTENT_COGNITION":
            at = now + timedelta(hours=self.content_interval_hours)
        elif task == "REFLECT":
            at = self._next_reflection(now)
        else:
            seconds = _DEFAULT_INTERVALS.get(task)
            if seconds is None:
                return
            at = now + timedelta(seconds=seconds)
            if task == "CHECK_OUTCOMES":
                exact_due = self._next_action_outcome_due(now)
                if exact_due is not None:
                    at = min(at, exact_due)
        wake_id = f"content:{task}"
        existing = next(
            (wake for wake in self.core.snapshot().pending_plans if wake.wake_id == wake_id),
            None,
        )
        # A task may schedule an urgent follow-up while it is running. The normal
        # cadence that main() applies after the task completes must never postpone
        # that earlier wake; it may only fill a missing plan or make one earlier.
        if existing is not None and existing.at <= at:
            return
        self.core.schedule(PlannedWake(
            wake_id=wake_id, at=at, reason=self._reason(task), task=task,
        ))

    def refresh_neural_life(self, now: datetime | None = None) -> dict:
        """Update Ferro's non-LLM body, drives and affective state.

        The connectome receives endogenous drive stimulation without requiring a
        model call.  Strong drives can schedule ordinary runtime work directly.
        """
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self.neural_life_enabled:
            return {"status": "DISABLED"}
        snap = self.core.snapshot()
        wakeful = {
            "neural_graph": dict(snap.neural_state),
            "neural_feedback": [
                {
                    "outcome_id": row.outcome_id,
                    "observed_at": row.observed_at.isoformat(),
                    "pathway": list(row.pathway),
                    "source_event_id": row.source_event_id,
                }
                for row in snap.recent_neural_feedback[-16:]
            ],
            "recent_stimulus_kinds": [event.kind for event in snap.recent_stimuli[-16:]],
            "last_cognition_at": snap.last_cognition_at.isoformat() if snap.last_cognition_at else None,
        }
        projection = self.neural_life.update(now=now, wakeful=wakeful)

        if self.neural_life.should_stimulate(now):
            drives = projection.get("drives") or {}
            mapping = {
                "survival": ("resource_pressure", "INTEROCEPTION_SURVIVAL"),
                "reproduction": ("social_attention", "INTEROCEPTION_REPRODUCTION"),
                "evolution": ("curiosity", "INTEROCEPTION_EVOLUTION"),
            }
            for drive_name, (channel, kind) in mapping.items():
                magnitude = max(0.0, min(1.0, float(drives.get(drive_name) or 0.0)))
                if magnitude < 0.05:
                    continue
                event_id = f"interoception:{drive_name}:{int(now.timestamp() // 240)}"
                self.core.emit(StimulusEvent(
                    event_id=event_id,
                    source="ferro-nervous-system",
                    kind=kind,
                    observed_at=now,
                    salience=magnitude,
                    novelty=0.12,
                    urgency=magnitude,
                    persistence=0.90,
                    payload_ref=f"neural-drive:{drive_name}",
                    attention_channel=channel,
                ), enqueue=False)
            self.neural_life.mark_stimulated(now)

        dream_influence = self.dream_engine.claim_waking_influence(now=now)
        if dream_influence is not None:
            base_magnitude = max(
                0.0,
                min(1.0, float(dream_influence.get("magnitude") or 0.0)),
            )
            for index, channel in enumerate(dream_influence.get("channels") or ()):
                if index >= 3:
                    break
                magnitude = max(0.05, base_magnitude * (1.0 - 0.15 * index))
                self.core.emit(StimulusEvent(
                    event_id=(
                        f"dream-consolidation:{dream_influence.get('attractor_id')}:"
                        f"{int(now.timestamp() // 21600)}:{index}"
                    ),
                    source="ferro-dream-engine",
                    kind="DREAM_CONSOLIDATION",
                    observed_at=now,
                    salience=magnitude,
                    novelty=0.35,
                    urgency=max(0.20, magnitude * 0.65),
                    persistence=0.92,
                    payload_ref=str(dream_influence.get("attractor_id") or ""),
                    attention_channel=str(channel),
                ), enqueue=False)

        recommendation = self.neural_life.authority_recommendation(now)
        if recommendation is not None:
            task = str(recommendation["task"])
            if task == "CHECK_X" and os.environ.get("CONTENT_X_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
                task = "CONTENT_COGNITION"
                recommendation = {**recommendation, "task": task}
            existing = [
                wake for wake in self.core.snapshot().pending_plans
                if wake.task == task and wake.at <= now + timedelta(minutes=15)
            ]
            if not existing:
                self.core.schedule(PlannedWake(
                    wake_id=f"neural:{str(recommendation['intention']).lower()}",
                    at=now + timedelta(seconds=5),
                    reason=str(recommendation["reason"]),
                    task=task,
                    relevant_refs=(f"neural-intention:{recommendation['intention']}",),
                    preemptible=True,
                ))
                self.neural_life.mark_authority_action(now, recommendation)
                projection = self.neural_life.projection()
        scoreboard = self.brain_cortex.projection().get("scoreboard") or {}
        executed = sum(int((row or {}).get("executed") or 0) for row in scoreboard.values())
        neural_executed = int((scoreboard.get("NEURAL") or {}).get("executed") or 0)
        authority_fraction = (neural_executed / executed) if executed else 0.5
        self.neural_personality.refresh(
            now=now,
            neural_life=projection,
            brain_lab=self.brain_lab.projection(),
            social=self.neural_social.projection(),
            neural_authority_fraction=authority_fraction,
        )
        motor = self.neural_motor.select(neural_life=projection, now=now)
        motor_task = str(motor.get("task") or "")
        if motor_task:
            target_at = now + timedelta(seconds=5)
            existing = next(
                (
                    wake for wake in self.core.snapshot().pending_plans
                    if wake.task == motor_task and wake.at <= target_at
                ),
                None,
            )
            if existing is None:
                self.core.schedule(PlannedWake(
                    wake_id=f"content:{motor_task}",
                    at=target_at,
                    reason=f"{motor.get('authority') or 'NEURAL_MOTOR'} selected {motor_task} via {motor.get('motor_id') or 'motor unit'}",
                    task=motor_task,
                    relevant_refs=(
                        str(motor.get("decision_id") or ""),
                        str(motor.get("motor_id") or ""),
                    ),
                    preemptible=True,
                ))
        agency = self.developmental_agency.refresh(
            now=now,
            neural_life=projection,
            neural_transfer=self.neural_transfer.projection(),
            neural_motor=self.neural_motor.projection(),
        )
        for need in agency.get("needs") or ():
            if need.get("status") != "ACTIVE":
                continue
            pressure = max(0.0, min(1.0, float(need.get("neural_pressure") or 0.0)))
            channels = [str(value) for value in need.get("pressure_channels") or () if value]
            if pressure < 0.25 or not channels:
                continue
            self.core.emit(StimulusEvent(
                event_id=f"development-need:{need.get('need_id')}:{int(now.timestamp() // 1800)}",
                source="ferro-developmental-agency",
                kind="DEVELOPMENT_NEED",
                observed_at=now,
                salience=pressure,
                novelty=0.55,
                urgency=max(pressure, float(need.get("severity") or 0.0)),
                persistence=0.95,
                payload_ref=str(need.get("need_id") or ""),
                attention_channel=channels[0],
            ), enqueue=False)

        candidate_world = self.developmental_agency.next_candidate_world()
        if candidate_world is not None and int(candidate_world.get("trial_count") or 0) == 0:
            target_at = now + timedelta(seconds=10)
            if not any(
                wake.task == "NEURAL_DEVELOPMENT" and wake.at <= target_at
                for wake in self.core.snapshot().pending_plans
            ):
                self.core.schedule(PlannedWake(
                    wake_id="content:NEURAL_DEVELOPMENT",
                    at=target_at,
                    reason=f"test Ferro-born world {candidate_world.get('world_id')} for {candidate_world.get('need_id')}",
                    task="NEURAL_DEVELOPMENT",
                    relevant_refs=(
                        str(candidate_world.get("need_id") or ""),
                        str(candidate_world.get("proposal_id") or ""),
                        str(candidate_world.get("world_id") or ""),
                    ),
                    preemptible=True,
                ))
        elif candidate_world is not None and self.developmental_world_transfer.pending() is None:
            evaluation = (
                self.developmental_world_transfer.projection()
                .get("worlds", {})
                .get(str(candidate_world.get("world_id") or ""), {})
            )
            if not bool(evaluation.get("causal_claim_ready")):
                target_at = now + timedelta(seconds=10)
                if not any(
                    wake.task == "NEURAL_DEVELOPMENT_TRANSFER" and wake.at <= target_at
                    for wake in self.core.snapshot().pending_plans
                ):
                    self.core.schedule(PlannedWake(
                        wake_id="content:NEURAL_DEVELOPMENT_TRANSFER",
                        at=target_at,
                        reason=(
                            f"prospectively test real-world transfer of "
                            f"{candidate_world.get('world_id')}"
                        ),
                        task="NEURAL_DEVELOPMENT_TRANSFER",
                        relevant_refs=(
                            str(candidate_world.get("need_id") or ""),
                            str(candidate_world.get("proposal_id") or ""),
                            str(candidate_world.get("world_id") or ""),
                        ),
                        preemptible=False,
                    ))

        current_snap = self.core.snapshot()
        self.cortex_continuity.observe(
            neural_graph=dict(current_snap.neural_state),
            motor=self.neural_motor.projection(),
            personality=self.neural_personality.projection(),
            observed_at=now,
        )
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return projection

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
            brain_lab=self.brain_lab.projection(),
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

    def observe_motor_outcome(
        self,
        *,
        task: str,
        result,
        observed_at: datetime | None = None,
    ) -> dict | None:
        return self.neural_motor.observe_outcome(
            task=task,
            result=result,
            observed_at=observed_at,
        )

    def run_neural_transfer(self, now: datetime | None = None, *, training_steps: int = 12) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = self.neural_transfer.begin(now=now, training_steps=training_steps)
        experiment = result.get("experiment") if isinstance(result, dict) else None
        if isinstance(experiment, dict):
            target_task = str(experiment.get("target_task") or "")
            if target_task:
                target_at = now + timedelta(seconds=5)
                existing = next(
                    (
                        wake for wake in self.core.snapshot().pending_plans
                        if wake.task == target_task and wake.at <= target_at
                    ),
                    None,
                )
                if existing is None:
                    self.core.schedule(PlannedWake(
                        wake_id=f"transfer-target:{experiment.get('experiment_id')}",
                        at=target_at,
                        reason=(
                            f"prospective neural transfer target for {experiment.get('experiment_id')} "
                            f"after {experiment.get('assignment')} source intervention"
                        ),
                        task=target_task,
                        relevant_refs=(str(experiment.get("experiment_id") or ""),),
                        preemptible=False,
                    ))
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return result

    def observe_transfer_outcome(
        self,
        *,
        task: str,
        result,
        observed_at: datetime | None = None,
    ) -> dict | None:
        return self.neural_transfer.settle_target(
            task=task,
            result=result,
            observed_at=observed_at,
        )

    def run_brain_lab(self, now: datetime | None = None, *, environment: str | None = None, steps: int = 12) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = self.brain_lab.run_session(environment=environment, steps=steps, now=now)
        # Brain Lab deliberately trains the same persistent production connectome.
        # Persist the reflex checkpoint immediately so game learning survives a crash.
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return result

    def run_development_trial(self, now: datetime | None = None, *, steps: int = 12) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        world = self.developmental_agency.next_candidate_world()
        if world is None:
            return {"status": "NO_DEVELOPMENT_WORLD"}
        result = self.brain_lab.run_session(
            environment=str(world["world_id"]),
            steps=max(1, min(32, int(steps))),
            now=now,
        )
        updated = self.developmental_agency.record_world_trial(
            world_id=str(world["world_id"]),
            result=result,
            observed_at=now,
        )
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return {
            "status": "TRAINED_SELF_GENERATED_WORLD",
            "world_id": world["world_id"],
            "need_id": world["need_id"],
            "proposal_id": world["proposal_id"],
            "training": result,
            "world": updated,
        }

    def run_development_transfer(
        self,
        now: datetime | None = None,
        *,
        training_steps: int = 12,
    ) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = self.developmental_world_transfer.begin(
            now=now,
            training_steps=training_steps,
        )
        trial = result.get("trial") if isinstance(result, dict) else None
        if isinstance(trial, dict) and trial.get("status") == "AWAITING_TARGET_OUTCOME":
            target_task = str(trial.get("target_task") or "")
            if target_task:
                target_at = now + timedelta(seconds=5)
                if not any(
                    wake.task == target_task and wake.at <= target_at
                    for wake in self.core.snapshot().pending_plans
                ):
                    self.core.schedule(PlannedWake(
                        wake_id=f"development-target:{trial.get('trial_id')}",
                        at=target_at,
                        reason=(
                            f"real target outcome for {trial.get('trial_id')} after "
                            f"{trial.get('assignment')} {trial.get('world_id')}"
                        ),
                        task=target_task,
                        relevant_refs=(
                            str(trial.get("trial_id") or ""),
                            str(trial.get("world_id") or ""),
                            str(trial.get("need_id") or ""),
                        ),
                        preemptible=False,
                    ))
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return result

    def observe_development_transfer_outcome(
        self,
        *,
        task: str,
        result,
        observed_at: datetime | None = None,
    ) -> dict | None:
        return self.developmental_world_transfer.settle_target(
            task=task,
            result=result,
            observed_at=observed_at,
        )

    def brain_lab_projection(self) -> dict:
        return self.brain_lab.projection()

    def run_neural_dream(self, now: datetime | None = None, *, replay_count: int = 6) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = self.dream_engine.dream(now=now, replay_count=replay_count)
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        preconcept = result.get("preconcept") if isinstance(result, dict) else None
        if isinstance(preconcept, dict) and float(preconcept.get("association_strength") or 0.0) >= 0.30:
            wake = PlannedWake(
                wake_id=f"dream-insight:{preconcept['preconcept_id']}",
                at=now + timedelta(seconds=10),
                reason="a recurrent dream association is strong enough for rational interpretation",
                task="CONTENT_COGNITION",
                relevant_refs=(str(preconcept["preconcept_id"]), str(preconcept.get("source_dream") or "")),
                preemptible=True,
            )
            self.core.schedule(wake)
        return result

    def dream_projection(self) -> dict:
        return self.dream_engine.projection()

    def schedule_resource_followup(self, now: datetime, *, seconds: int = 300) -> None:
        # Operator-authorized resource onboarding is event-driven and should not
        # wait for the normal hourly review. Autonomous retries still pass 300s.
        delay = max(5, min(int(seconds), 3600))
        self.core.schedule(PlannedWake(
            wake_id="content:RESOURCE_REVIEW",
            at=now + timedelta(seconds=delay),
            reason="continue unresolved autonomous resource verification",
            task="RESOURCE_REVIEW",
        ))

    def schedule_goal_followup(self, now: datetime, *, seconds: int = 5) -> None:
        delay = max(1, min(int(seconds), 300))
        at = now + timedelta(seconds=delay)
        wake_id = "content:CONTENT_COGNITION"
        existing = next(
            (wake for wake in self.core.snapshot().pending_plans if wake.wake_id == wake_id),
            None,
        )
        if existing is not None and existing.at <= at:
            return
        self.core.schedule(PlannedWake(
            wake_id=wake_id,
            at=at,
            reason="reconsider priorities after owner activated a goal",
            task="CONTENT_COGNITION",
        ))

    def schedule_self_maintenance(self, now: datetime, *, seconds: int = 60) -> None:
        delay = max(30, min(int(seconds), 900))
        at = now + timedelta(seconds=delay)
        wake_id = "content:REFLECT"
        existing = next(
            (wake for wake in self.core.snapshot().pending_plans if wake.wake_id == wake_id),
            None,
        )
        if existing is not None and existing.at <= at:
            return
        wake = PlannedWake(
            wake_id=wake_id,
            at=at,
            reason="diagnose production failure and form bounded self-repair hypothesis",
            task="REFLECT",
        )
        # The request spool is authoritative across processes. Direct scheduling
        # keeps in-process callers immediate; an external caller cannot lose the
        # wake if the live runtime persists its own state concurrently.
        self._enqueue_external_wake(wake)
        self.core.schedule(wake)

    def schedule_windfall_reflection(
        self, now: datetime, *, seconds: int = 60
    ) -> None:
        """Spend a newly verified high-capability free window on bounded reflection."""
        delay = max(30, min(int(seconds), 900))
        at = now + timedelta(seconds=delay)
        wake_id = "content:REFLECT"
        existing = next(
            (
                wake for wake in self.core.snapshot().pending_plans
                if wake.wake_id == wake_id
            ),
            None,
        )
        if existing is not None and existing.at <= at:
            return
        self.core.schedule(PlannedWake(
            wake_id=wake_id,
            at=at,
            reason="consume verified high-capability free LLM windfall on self-improvement",
            task="REFLECT",
        ))

    def _next_action_outcome_due(self, now: datetime) -> datetime | None:
        """Return the next prospective action-outcome maturity after ``now``.

        The hourly CHECK_OUTCOMES cadence remains the liveness fallback. A known
        prospective maturity may only make the wake earlier; it can never delay
        the fallback or create a busy loop for already-due/missing evidence.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("outcome scheduling time requires timezone awareness")
        root = self.state_root / "content" / "continuous" / "cycles"
        due: list[datetime] = []
        for prediction_path in root.glob("*/action_prediction.json") if root.exists() else ():
            cycle_root = prediction_path.parent
            publication_path = cycle_root / "publication.json"
            if not publication_path.exists():
                continue
            try:
                publication = json.loads(publication_path.read_text(encoding="utf-8"))
                attr = publication.get("outcome_attribution") if isinstance(publication, dict) else None
                published_at = datetime.fromisoformat(str((attr or {}).get("published_at")).replace("Z", "+00:00"))
                if published_at.tzinfo is None or published_at.utcoffset() is None:
                    continue
                published_at = published_at.astimezone(timezone.utc)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            for label, hours in (("1h", 1), ("4h", 4), ("24h", 24)):
                outcome_path = cycle_root / "action_prediction_outcomes" / f"{label}.json"
                if outcome_path.exists():
                    continue
                candidate = published_at + timedelta(hours=hours)
                if candidate > now:
                    due.append(candidate)
        return min(due) if due else None

    def emit_talk(self, *, message: str, observed_at: datetime | None = None) -> None:
        now = observed_at or datetime.now(timezone.utc)
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:20]
        self.core.emit(StimulusEvent(
            event_id=f"talk:{digest}:{int(now.timestamp())}",
            source="talk", kind="TALK_MESSAGE", observed_at=now,
            salience=0.45, novelty=0.45, urgency=0.35, persistence=0.35,
            payload_ref=f"talk-sha256:{digest}",
        ))

    def emit_network_error(self, *, code: str, observed_at: datetime | None = None) -> None:
        now = observed_at or datetime.now(timezone.utc)
        self.core.emit(StimulusEvent(
            event_id=f"network-error:{int(now.timestamp() * 1000)}",
            source="agent-network", kind="NETWORK_ERROR", observed_at=now,
            salience=0.75, novelty=0.4, urgency=0.65, persistence=0.7,
            payload_ref=f"network-error:{code or 'unknown'}",
        ))

    def emit_network_evidence(self, *, evidence_ref: str, observed_at: datetime | None = None) -> None:
        now = observed_at or datetime.now(timezone.utc)
        digest = hashlib.sha256(str(evidence_ref).encode("utf-8")).hexdigest()[:20]
        self.core.emit(StimulusEvent(
            event_id=f"network-evidence:{digest}:{int(now.timestamp())}",
            source="agent-network", kind="NETWORK_EVIDENCE", observed_at=now,
            salience=0.65, novelty=0.55, urgency=0.35, persistence=0.55,
            payload_ref=f"network-evidence:{digest}",
        ))

    def emit_x_mentions(self, *, count: int, observed_at: datetime | None = None) -> None:
        if int(count) <= 0:
            return
        now = observed_at or datetime.now(timezone.utc)
        strength = min(1.0, 0.35 + 0.10 * int(count))
        self.core.emit(StimulusEvent(
            event_id=f"x-mentions:{int(now.timestamp())}:{int(count)}",
            source="x", kind="X_MENTION", observed_at=now,
            salience=strength, novelty=min(1.0, 0.45 + 0.05 * int(count)),
            urgency=0.35, persistence=0.45, payload_ref=f"x-mentions-count:{int(count)}",
        ))

    def emit_action_commitment(self, *, prediction_id: str, observed_at: datetime | None = None) -> dict:
        prediction_id = str(prediction_id or "").strip()
        if not prediction_id:
            raise ValueError("action commitment requires prediction_id")
        now = observed_at or datetime.now(timezone.utc)
        event_id = f"action:{prediction_id}"
        signal = self.core.emit(StimulusEvent(
            event_id=event_id, source="content-cognition", kind="ACTION_COMMITMENT", observed_at=now,
            salience=0.80, novelty=0.35, urgency=0.65, persistence=0.85, payload_ref=prediction_id,
        ), enqueue=False)
        return {
            "status": "OBSERVED", "event_id": event_id, "prediction_id": prediction_id,
            "attention_channel": signal.channel, "threshold_crossed": signal.threshold_crossed,
        }

    def emit_prediction_due(self, *, prediction_ref: str, observed_at: datetime | None = None) -> None:
        now = observed_at or datetime.now(timezone.utc)
        self.core.emit(StimulusEvent(
            event_id=f"prediction-due:{hashlib.sha256(prediction_ref.encode()).hexdigest()[:20]}:{int(now.timestamp())}",
            source="planner", kind="PREDICTION_DUE", observed_at=now,
            salience=0.85, novelty=0.20, urgency=0.90, persistence=0.85,
            payload_ref=prediction_ref,
        ), enqueue=False)

    def set_resource_pressure(self, pressure: str) -> None:
        normalized = str(pressure or "UNKNOWN").upper()
        previous = self.core.snapshot().resource_pressure
        self.core.set_resource_pressure(normalized)
        if normalized == previous:
            return
        now = datetime.now(timezone.utc)
        urgency = {"NORMAL": 0.1, "WATCH": 0.35, "CONSERVE": 0.65, "CRITICAL": 1.0}.get(normalized, 0.3)
        self.core.emit(StimulusEvent(
            event_id=f"resource:{normalized}:{int(now.timestamp())}",
            source="resource-state", kind="RESOURCE_CHANGE", observed_at=now,
            salience=urgency, novelty=0.35, urgency=urgency, persistence=0.8,
            payload_ref=f"resource-pressure:{normalized}",
        ))

    def apply_prediction_outcome_feedback(self, outcome: dict) -> dict:
        prediction_id = str(outcome.get("prediction_id") or "")
        window = str(outcome.get("window") or "")
        evidence_refs = tuple(str(x) for x in outcome.get("evidence_refs") or () if x)
        if not prediction_id or window not in {"1h", "4h", "24h"} or not evidence_refs:
            raise ValueError("prediction outcome feedback requires identity, canonical window, and evidence")
        observed_raw = outcome.get("observed_at")
        if isinstance(observed_raw, datetime):
            observed_at = observed_raw
        else:
            observed_at = datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
        expected = max(0.0, float(outcome.get("expected_pageviews") or 0.0))
        actual = max(0.0, float(outcome.get("actual_pageviews") or 0.0))
        reliability = max(0.0, min(1.0, float(outcome.get("reliability") or 0.0)))
        prediction_error = max(0.0, min(1.0, float(outcome.get("prediction_error") or 0.0)))
        signed_progress = (actual - expected) / (actual + expected + 1.0)
        feedback = NeuralFeedbackEvent(
            outcome_id=f"content-action:{prediction_id}:{window}",
            observed_at=observed_at,
            pathway=("prediction_due", "task_urgency"),
            evidence_refs=evidence_refs,
            source_event_id=f"action:{prediction_id}",
            information_gain=0.25 * reliability,
            prediction_error=prediction_error * reliability,
            goal_progress=max(-1.0, min(1.0, signed_progress * reliability)),
        )
        return self.core.apply_neural_feedback(feedback)

    def mark_cognition(self, now: datetime | None = None) -> None:
        self.core.mark_cognition(now)

    def wait(self, *, stopping):
        # Merge requests persisted before entering the wait, then keep a tiny
        # watcher alive so cross-process Operator/self-maintenance requests can
        # notify the live core's condition variable without restarting Ferro.
        self._drain_external_wake_requests()
        stop = threading.Event()
        watcher = threading.Thread(
            target=self._watch_external_wake_requests,
            args=(stop,),
            name="content-wake-request-watcher",
            daemon=True,
        )
        watcher.start()
        try:
            return self.core.wait_for_wake(
                watchdog_seconds=float(self.watchdog_seconds),
                stop_requested=stopping,
            )
        finally:
            stop.set()
            watcher.join(timeout=1.0)

    def public_snapshot(self) -> dict:
        snap = self.core.snapshot()
        return {
            "presence": "AWAKE",
            "state": snap.state.value,
            "next_wake_at": snap.next_wake_at.isoformat() if snap.next_wake_at else None,
            "next_wake_reason": snap.next_wake_reason,
            "attention": dict(snap.attention),
            "neural_graph": dict(snap.neural_state),
            "neural_feedback": [
                {
                    "outcome_id": row.outcome_id,
                    "observed_at": row.observed_at.isoformat(),
                    "pathway": list(row.pathway),
                    "source_event_id": row.source_event_id,
                    "evidence_refs": list(row.evidence_refs),
                }
                for row in snap.recent_neural_feedback[-8:]
            ],
            "neural_control_signals": [
                {
                    "source_event_id": row.source_event_id, "neuron": row.neuron,
                    "kind": row.kind, "weight": row.weight, "intent": row.intent,
                }
                for row in snap.neural_control_signals[-12:]
            ],
            "resource_pressure": snap.resource_pressure,
            "last_cognition_at": snap.last_cognition_at.isoformat() if snap.last_cognition_at else None,
            "last_action_at": snap.last_action_at.isoformat() if snap.last_action_at else None,
            "pending_plan_count": len(snap.pending_plans),
            "cognition_threshold_crossed": bool(snap.cognition_triggers),
            "recent_stimulus_kinds": [event.kind for event in snap.recent_stimuli[-8:]],
            "neural_life": self.neural_life.projection() if self.neural_life_enabled else {"status": "DISABLED"},
            "brain_lab": self.brain_lab.projection(),
            "dreaming": self.dream_engine.projection(),
            "neural_taste": self.neural_taste.projection(),
            "neural_social": self.neural_social.projection(),
            "personality": self.neural_personality.projection(),
            "brain_vs_cortex": self.brain_cortex.projection(),
            "neural_motor": self.neural_motor.projection(),
            "neural_transfer": self.neural_transfer.projection(),
            "developmental_agency": self.developmental_agency.projection(),
            "developmental_world_transfer": self.developmental_world_transfer.projection(),
            "cortex_continuity": self.cortex_continuity.projection(),
            "psychodynamics": self.psychodynamics.projection(
                neural_life=self.neural_life.projection(),
                personality=self.neural_personality.projection(),
                active_goal_count=len(snap.active_goal_refs),
            ),
            "connectome": {
                "enabled": self.connectome_enabled,
                "status": (
                    "SHADOW_ACTIVE" if isinstance(snap.neural_state.get("substrate"), dict)
                    else "DEGRADED" if self.connectome_error else "DISABLED"
                ),
                "error": None if self.connectome_error is None else dict(self.connectome_error),
            },
        }

    @staticmethod
    def _reason(task: str) -> str:
        return {
            "OBSERVE_WORLD": "observe current world state",
            "RESOURCE_REVIEW": "review resource runway and pressure",
            "CHECK_OUTCOMES": "review matured website outcomes",
            "CONTENT_COGNITION": "run the next due content cognition cycle",
            "CHECK_X": "check X for new social evidence",
            "REFLECT": "daily reflection",
            "NEURAL_INTEROCEPTION": "sense body state and update endogenous drives",
            "NEURAL_PLAY": "develop the production nervous system through a sensorimotor world",
            "NEURAL_DREAM": "replay and recombine salient experience in the production nervous system",
            "NEURAL_TRANSFER": "run one prospective cross-domain neural transfer trial",
            "NEURAL_DEVELOPMENT": "test a Ferro-born bounded developmental world",
            "NEURAL_DEVELOPMENT_TRANSFER": "prospectively test a Ferro-born world against a real task",
        }.get(task, task.lower().replace("_", " "))

    @staticmethod
    def _next_reflection(now: datetime) -> datetime:
        tz = ZoneInfo(str(os.environ.get("CONTENT_REFLECTION_TIMEZONE") or "Asia/Shanghai"))
        hour = int(os.environ.get("CONTENT_DAILY_REFLECTION_HOUR", "8"))
        local = now.astimezone(tz)
        candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)


__all__ = ["ContentWakefulRuntime", "WakefulState"]
