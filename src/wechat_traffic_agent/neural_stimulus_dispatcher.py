"""Single-process neural stimulus emitter for body, drives and dream recall.

Produces typed events through the injected core. It cannot schedule owner
tasks or modify action permissions.
"""
from __future__ import annotations

from datetime import datetime


class NeuralStimulusDispatcher:
    def __init__(self, *, core, life, dream, body_currents, stimulus_type):
        self.core, self.life, self.dream = core, life, dream
        self.body_currents, self.stimulus_type = body_currents, stimulus_type

    def _body(self, now, projection, wakeful):
        if self.life.should_stimulate(now):
            substrate = getattr(self.core.reflex, "substrate", None)
            aux_channels = set(getattr(substrate, "aux_channels", ()) or ())
            if substrate is not None and aux_channels:
                raw_currents = {
                    key: value
                    for key, value in self.body_currents(
                        projection,
                        recent_stimulus_kinds=tuple(wakeful["recent_stimulus_kinds"]),
                    ).items()
                    if key in aux_channels and value > 0.0
                }
                if raw_currents:
                    try:
                        substrate.stimulate(
                            raw_currents,
                            now,
                            source_event_id=f"body-sense:{int(now.timestamp() // 240)}",
                            feedback_eligible=False,
                        )
                    except Exception as exc:
                        record_error = getattr(substrate, "record_error", None)
                        if callable(record_error):
                            record_error(exc, now)
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
                self.core.emit(self.stimulus_type(
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
            self.life.mark_stimulated(now)


    def _dream(self, now):
        dream_influence = self.dream.claim_waking_influence(now=now)
        if dream_influence is not None:
            base_magnitude = max(
                0.0,
                min(1.0, float(dream_influence.get("magnitude") or 0.0)),
            )
            for index, channel in enumerate(dream_influence.get("channels") or ()):
                if index >= 3:
                    break
                magnitude = max(0.05, base_magnitude * (1.0 - 0.15 * index))
                self.core.emit(self.stimulus_type(
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


    def emit(self, now, projection, wakeful):
        self._body(now, projection, wakeful)
        self._dream(now)
