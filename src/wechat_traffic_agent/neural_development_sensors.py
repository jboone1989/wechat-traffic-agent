"""Neural development and self-sensor stimulation without scheduling authority."""
from __future__ import annotations


class NeuralDevelopmentSensors:
    def __init__(self, *, core, agency, transfer, motor, history,
                 extensions, stimulus_type):
        self.core, self.agency, self.transfer = core, agency, transfer
        self.motor, self.history, self.extensions = motor, history, extensions
        self.stimulus_type = stimulus_type

    def _needs(self, now, agency):
        for need in agency.get("needs") or ():
            if need.get("status") != "ACTIVE":
                continue
            pressure = max(0.0, min(1.0, float(need.get("neural_pressure") or 0.0)))
            channels = [str(value) for value in need.get("pressure_channels") or () if value]
            if pressure < 0.25 or not channels:
                continue
            self.core.emit(self.stimulus_type(
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


    def _extensions(self, now, agency):
        extension_projection = self.extensions.refresh(
            now=now,
            agency=agency,
            motor_cortex=self.motor,
        )
        for sample in self.extensions.sample_active_sensors(now=now):
            currents = sample.get("currents") if isinstance(sample.get("currents"), dict) else {}
            for index, (channel, current) in enumerate(
                sorted(currents.items(), key=lambda item: (-float(item[1]), item[0]))
            ):
                if index >= 3 or float(current) <= 0.0:
                    continue
                magnitude = max(0.05, min(0.9, float(current)))
                self.core.emit(self.stimulus_type(
                    event_id=(
                        f"self-sensor:{sample.get('sensor_id')}:"
                        f"{int(now.timestamp() // 300)}:{index}"
                    ),
                    source="ferro-neural-self-extension",
                    kind="SELF_EXTENDED_SENSOR",
                    observed_at=now,
                    salience=magnitude,
                    novelty=0.62,
                    urgency=max(0.18, magnitude * 0.55),
                    persistence=0.88,
                    payload_ref=str(sample.get("extension_id") or ""),
                    attention_channel=str(channel),
                ), enqueue=False)


    def observe(self, now, projection):
        agency = self.agency.refresh(
            now=now,
            neural_life=projection,
            neural_transfer=self.transfer.projection(),
            neural_motor=self.motor.projection(),
            developmental_history=self.history.projection(),
        )
        self._needs(now, agency)
        self._extensions(now, agency)
