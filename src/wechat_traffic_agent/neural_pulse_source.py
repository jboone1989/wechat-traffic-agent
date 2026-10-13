"""Neural-native pulse center: Stage 1-2 of the neural-native roadmap.

A background loop periodically stimulates the hybrid nervous system with
drive-modulated semantic currents.  When the connectome answers with a
burst whose shadow counterfactual elects a cognition-bearing channel,
the elected channel becomes a *neural intent*: it is written to the
intent ledger and scheduled as ordinary runtime work.  When the work
finishes, the outcome is reward-fed back into connectome plasticity
(eligibility traces) and drive settlement, closing the loop:

    drive currents -> stimulation -> burst -> intent -> work
        -> outcome -> apply_feedback (synapses) + drive settlement

WakefulCore's time formulas remain only as watchdog and fallback; the
primary cause of waking is now a discharge pattern.

Brain-region provenance: channels are grouped by their Drosophila
homolog regions so readout calibration can later be organized per region.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import threading
from typing import Any

# Semantic channel -> (wakeful task, Drosophila homolog region).
# The region tags give readout calibration a per-region organization axis.
CHANNEL_BRAIN_MAP: dict[str, tuple[str, str]] = {
    "curiosity": ("OBSERVE_WORLD", "antennal_lobe/optic_lobe"),
    "opportunity": ("CONTENT_COGNITION", "fan_shaped_body"),
    "task_urgency": ("CONTENT_COGNITION", "central_complex"),
    "social_attention": ("CHECK_X", "lateral_horn"),
    "threat_risk": ("RESOURCE_REVIEW", "protocerebrum"),
    "resource_pressure": ("RESOURCE_REVIEW", "protocerebrum"),
    "error_recovery": ("REFLECT", "mushroom_body"),
    "prediction_due": ("VERIFY_PREDICTION", "central_complex"),
}

_DRIVE_CHANNEL_CURRENTS: dict[str, str] = {
    "curiosity": "curiosity",
    "creation": "opportunity",
    "social": "social_attention",
    "survival": "threat_risk",
    "resource": "resource_pressure",
    "mastery": "task_urgency",
}

_MAX_CHANNEL_CURRENT = 1.5


def _enabled(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class NeuralPulseSource:
    """Periodically stimulate the nervous system; bursts become intents."""

    def __init__(
        self,
        *,
        wakeful,
        tick_seconds: float | None = None,
        burst_ratio: float = 3.0,
        min_active_neurons: int = 4,
        min_score: float = 0.30,
        sensory_stream: Any | None = None,
    ) -> None:
        self._wakeful = wakeful
        self.state_root = wakeful.state_root
        self.tick_seconds = max(
            10.0,
            float(
                tick_seconds
                if tick_seconds is not None
                else os.environ.get("CONTENT_PULSE_TICK_SECONDS", "15")
            ),
        )
        self.burst_ratio = max(1.0, float(burst_ratio))
        self.min_active_neurons = max(1, int(min_active_neurons))
        self.min_score = float(min_score)
        self.sensory_stream = sensory_stream
        self.last_report: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _ledger_path(self):
        from pathlib import Path

        return Path(self.state_root) / "application" / "neural_intents.jsonl"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="neural-pulse-source", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                self.last_report = self.tick()
            except Exception as exc:  # a pulse never takes the organism offline
                self.last_report = {
                    "status": "PULSE_FAILED",
                    "error_type": type(exc).__name__,
                }

    def _append_intent(self, intent: dict[str, Any]) -> None:
        try:
            path = self._ledger_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(intent, ensure_ascii=False, sort_keys=True) + "\n"
                )
        except OSError:
            pass  # ledger loss must never break the pulse loop

    def _drive_currents(self, now: datetime) -> dict[str, float]:
        from .drive_system import DriveEngine

        projection = DriveEngine(self.state_root).projection()
        drives = projection.get("drives") if isinstance(projection.get("drives"), dict) else {}
        currents: dict[str, float] = {}
        for drive_name, channel in _DRIVE_CHANNEL_CURRENTS.items():
            row = drives.get(drive_name)
            intensity = float(row.get("intensity") or 0.0) if isinstance(row, dict) else 0.0
            currents[channel] = currents.get(channel, 0.0) + intensity
        return {
            channel: min(_MAX_CHANNEL_CURRENT, max(0.0, value))
            for channel, value in currents.items()
            if value > 0.0
        }

    def tick(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        reflex = self._wakeful.core.reflex
        substrate = getattr(reflex, "substrate", None)
        if substrate is None:
            return {"status": "NO_SUBSTRATE"}
        currents = self._drive_currents(now)
        sensory_novelty: dict[str, float] = {}
        if self.sensory_stream is not None:
            try:
                sensory_novelty = self.sensory_stream.currents(now)
            except Exception:
                sensory_novelty = {}
            for channel, value in self.sensory_stream.to_channel_currents(
                sensory_novelty
            ).items():
                currents[channel] = min(
                    _MAX_CHANNEL_CURRENT,
                    currents.get(channel, 0.0) + value,
                )
        if not currents:
            return {"status": "NO_STIMULUS"}
        reflex.integrate_many(currents, now)
        projection = substrate.projection()
        recent = projection.get("recent_bursts") or []
        row = recent[-1] if recent else None
        if not row:
            return {"status": "NO_BURST_DATA", "channels": sorted(currents)}
        active = int(row.get("active_neurons") or 0)
        background = int(row.get("background_active_neurons") or 0)
        burst = active >= max(
            self.min_active_neurons, background * self.burst_ratio
        )
        counterfactual = row.get("shadow_cognition_counterfactual") or {}
        channel = str(counterfactual.get("selected_channel") or "")
        seq = int(row.get("seq") or 0)
        report: dict[str, Any] = {
            "status": "BURST" if burst else "QUIET",
            "seq": seq,
            "active_neurons": active,
            "background_active_neurons": background,
            "elected_channel": channel,
            "drive_channels": sorted(currents),
        }
        if not burst or not channel:
            self._append_intent({
                "observed_at": now.isoformat(), "seq": seq,
                "status": report["status"], "channel": channel,
                "active_neurons": active, "background": background,
            })
            return report
        mapping = CHANNEL_BRAIN_MAP.get(channel)
        if mapping is None:
            report["status"] = "NO_TASK_MAPPING"
            return report
        task, region = mapping
        score = float(counterfactual.get("priority_margin") or 0.0)
        at = now + timedelta(minutes=5)
        for planned in self._wakeful.core.snapshot().pending_plans:
            if planned.task == task and planned.at <= at + timedelta(minutes=30):
                report["status"] = "ALREADY_SCHEDULED"
                report["task"] = task
                return report
        from agent_runtime.wakeful import PlannedWake

        source_event_id = "neural-pulse:" + str(seq)
        self._wakeful.core.schedule(
            PlannedWake(
                wake_id="content:NEURAL_PULSE",
                at=at,
                reason=(
                    "connectome burst elected channel "
                    + channel
                    + " ["
                    + region
                    + "] score "
                    + str(round(score, 4))
                ),
                task=task,
                relevant_refs=(
                    "pulse:" + channel,
                    "pulse_region:" + region,
                    source_event_id,
                ),
                preemptible=True,
            )
        )
        report.update({
            "status": "SCHEDULED",
            "task": task,
            "region": region,
            "score": score,
            "source_event_id": source_event_id,
        })
        # Expression urge: a creation-class channel elected by a burst that
        # fresh sensory information helped provoke.  The organism wanted to
        # say something; the post is the overflow, not a quota item.
        creation_class = channel in {"opportunity", "task_urgency", "curiosity"}
        fresh_info = bool(sensory_novelty)
        if creation_class and fresh_info:
            report["expression_urge"] = True
            report["sensory_novelty"] = sensory_novelty
            self._append_intent({
                "observed_at": now.isoformat(), "seq": seq,
                "status": "EXPRESSION_URGE", "channel": channel,
                "task": task, "region": region, "score": score,
                "sensory_novelty": sensory_novelty,
                "source_event_id": source_event_id,
            })
        self._append_intent({
            "observed_at": now.isoformat(), "seq": seq, "status": "SCHEDULED",
            "channel": channel, "task": task, "region": region,
            "score": score, "active_neurons": active, "background": background,
            "source_event_id": source_event_id,
        })
        return report

    def feedback_for(
        self, *, channel: str, source_event_id: str, task: str,
        result: dict[str, Any], cycle: str, now: datetime,
    ):
        """Build the NeuralFeedbackEvent for a finished pulse-driven task."""
        from agent_runtime.wakeful import NeuralFeedbackEvent

        status = str(result.get("status") or "UNKNOWN").upper()
        goal_progress = 0.5 if status == "OK" else (
            -0.5 if status in {"ERROR", "FAILED"} else (
                -0.2 if status == "DEGRADED" else None
            )
        )
        return NeuralFeedbackEvent(
            outcome_id="neural-pulse-outcome:" + str(source_event_id or cycle),
            observed_at=now,
            pathway=(channel,),
            evidence_refs=("pulse:" + channel, "task:" + task, "cycle:" + cycle),
            source_event_id=source_event_id or None,
            goal_progress=goal_progress,
        )


__all__ = ["NeuralPulseSource", "CHANNEL_BRAIN_MAP"]
