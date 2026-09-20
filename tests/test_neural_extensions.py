from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from wechat_traffic_agent.neural_extensions import NeuralSelfExtension


NOW = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)


class FakeMotor:
    def __init__(self) -> None:
        self.units = 32
        self.extended = []

    def projection(self):
        return {
            "motor_unit_count": self.units,
            "habits": [],
        }

    def extend_units(self, *, count: int, origin_ref: str, now):
        born = tuple(
            f"motor_{self.units + index:03d}"
            for index in range(count)
        )
        self.units += len(born)
        self.extended.append((origin_ref, born, now))
        return born


def agency(*, revised: bool = True):
    need_id = "development-need:test"
    return {
        "needs": [{
            "need_id": need_id,
            "status": "ACTIVE",
            "neural_pressure": 0.51,
            "target_task": "CHECK_X",
            "capability": "CHECK_X",
        }],
        "worlds": [{
            "world_id": "WORLD_0001",
            "need_id": need_id,
            "status": "REVISE_REQUIRED" if revised else "EXPERIMENTING",
        }],
    }


def _write_x_state(root: Path, pressure: float = 0.42):
    path = root / "content" / "x" / "growth_mission.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pressure": pressure,
        "resource_willingness": 0.31,
        "progress": 0.05,
    }), encoding="utf-8")


def test_revised_real_world_training_can_birth_bounded_sensor_and_motor_capacity(tmp_path: Path):
    _write_x_state(tmp_path)
    motor = FakeMotor()
    extensions = NeuralSelfExtension(tmp_path)

    projection = extensions.refresh(now=NOW, agency=agency(), motor_cortex=motor)

    assert projection["sensor_extension_count"] == 1
    assert projection["motor_extension_count"] == 1
    assert projection["active_sensor_count"] == 1
    assert projection["ferro_born_motor_unit_count"] == 4
    assert projection["llm_used_for_extension_birth"] is False
    assert projection["arbitrary_code_generation"] is False
    assert projection["new_external_action_authority"] is False
    assert motor.units == 36

    sensor = next(
        row for row in projection["extensions"]
        if row["extension_type"] == "SENSOR"
    )
    assert sensor["source"]["relative_path"] == "content/x/growth_mission.json"
    assert sensor["status"] == "ACTIVE_EXPERIMENT"
    assert len(sensor["population_code"]) == 3


def test_self_extended_sensor_samples_real_state_into_sparse_neural_currents(tmp_path: Path):
    _write_x_state(tmp_path, pressure=0.63)
    motor = FakeMotor()
    extensions = NeuralSelfExtension(tmp_path)
    extensions.refresh(now=NOW, agency=agency(), motor_cortex=motor)

    samples = extensions.sample_active_sensors(now=NOW)
    assert len(samples) == 1
    sample = samples[0]
    assert sample["source_kind"] in {
        "X_MISSION_PRESSURE",
        "X_RESOURCE_WILLINGNESS",
        "X_PROGRESS",
    }
    assert sample["llm_used"] is False
    assert 0.0 <= sample["value"] <= 1.0
    assert len(sample["currents"]) == 3
    assert all(0.0 <= value <= 0.9 for value in sample["currents"].values())

    # Sampling frequency cannot manufacture extra sensory experience.
    assert extensions.sample_active_sensors(now=NOW + timedelta(seconds=60)) == []
    assert len(extensions.sample_active_sensors(now=NOW + timedelta(minutes=6))) == 1


def test_same_need_does_not_grow_body_repeatedly(tmp_path: Path):
    _write_x_state(tmp_path)
    motor = FakeMotor()
    extensions = NeuralSelfExtension(tmp_path)

    first = extensions.refresh(now=NOW, agency=agency(), motor_cortex=motor)
    second = extensions.refresh(now=NOW + timedelta(minutes=10), agency=agency(), motor_cortex=motor)

    assert first["extension_count"] == 2
    assert second["extension_count"] == 2
    assert motor.units == 36
    assert len(motor.extended) == 1


def test_need_must_survive_real_world_revision_before_body_growth(tmp_path: Path):
    _write_x_state(tmp_path)
    motor = FakeMotor()
    extensions = NeuralSelfExtension(tmp_path)

    projection = extensions.refresh(
        now=NOW,
        agency=agency(revised=False),
        motor_cortex=motor,
    )

    assert projection["extension_count"] == 0
    assert motor.units == 32
