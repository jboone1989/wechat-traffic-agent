from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.neural_life import FerroNeuralLife


NOW = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)


def _wakeful(*, errors: int = 0, last_cognition_hours: float = 1.0):
    kinds = ["NETWORK_ERROR"] * errors
    return {
        "neural_graph": {
            "substrate": {
                "recent_bursts": [{
                    "delta_readout_hz": {
                        "curiosity": 7.0,
                        "social_attention": 2.0,
                        "resource_pressure": 1.0,
                        "error_recovery": 3.0,
                        "threat_risk": 2.0,
                        "opportunity": 6.0,
                        "prediction_due": 1.0,
                        "task_urgency": 2.0,
                    },
                    "shadow_cognition_counterfactual": {
                        "selected_channel": "curiosity",
                    },
                }]
            }
        },
        "neural_feedback": [],
        "recent_stimulus_kinds": kinds,
        "last_cognition_at": (NOW - timedelta(hours=last_cognition_hours)).isoformat(),
    }


def test_neural_life_persists_body_drives_affect_and_intention(tmp_path):
    life = FerroNeuralLife(tmp_path)
    state = life.update(
        now=NOW,
        wakeful=_wakeful(errors=4),
        body_override={
            "observed_at": NOW.isoformat(),
            "cpu_pressure": 0.25,
            "memory_pressure": 0.45,
            "disk_pressure": 0.94,
            "disk_free_gib": 2.1,
            "x_followers": 1835.0,
            "x_target": 5000.0,
            "x_follower_gap": 0.633,
            "x_recent_decline": 0.01,
        },
    )
    assert state["drives"]["survival"] > 0.3
    assert state["affect"]["resource_hunger"] == state["drives"]["survival"]
    assert state["intention"]["name"] in {"SURVIVE", "REPRODUCE", "EVOLVE", "REST"}
    assert state["intention"]["neural_basis"]["dominant_connectome_channel"] == "curiosity"
    assert state["history"]

    restored = FerroNeuralLife(tmp_path)
    assert restored.projection()["drives"] == state["drives"]
    assert restored.projection()["affect"] == state["affect"]


def test_evolution_drive_grows_during_idle_novelty_pressure_without_llm(tmp_path):
    life = FerroNeuralLife(tmp_path)
    first = life.update(
        now=NOW,
        wakeful=_wakeful(last_cognition_hours=12),
        body_override={
            "observed_at": NOW.isoformat(),
            "cpu_pressure": 0.05,
            "memory_pressure": 0.10,
            "disk_pressure": 0.20,
            "x_follower_gap": 0.0,
            "x_recent_decline": 0.0,
        },
    )
    second = life.update(
        now=NOW + timedelta(hours=2),
        wakeful=_wakeful(last_cognition_hours=14),
        body_override=first["body"],
    )
    assert second["drives"]["evolution"] > first["drives"]["evolution"]
    assert second["affect"]["boredom"] > 0.2


def test_strong_endogenous_drive_can_request_real_runtime_work(tmp_path):
    life = FerroNeuralLife(tmp_path)
    life.state["drives"] = {"survival": 0.91, "reproduction": 0.1, "evolution": 0.1}
    life.state["intention"] = {"name": "SURVIVE", "strength": 0.91, "margin": 0.7}
    recommendation = life.authority_recommendation(NOW)
    assert recommendation is not None
    assert recommendation["authority"] == "NEURAL_DRIVE"
    assert recommendation["task"] == "FAST_DECISION"
    assert "L1 must choose the concrete action" in recommendation["reason"]

    life.mark_authority_action(NOW, recommendation)
    assert life.authority_recommendation(NOW + timedelta(minutes=5)) is None
    assert life.authority_recommendation(NOW + timedelta(minutes=16)) is not None


def test_homeostasis_is_nearly_frequency_invariant(tmp_path):
    body = {
        "observed_at": NOW.isoformat(),
        "cpu_pressure": 0.20,
        "memory_pressure": 0.35,
        "disk_pressure": 0.80,
        "x_followers": 1835.0,
        "x_target": 5000.0,
        "x_follower_gap": 0.633,
        "x_recent_decline": 0.0,
    }
    one = FerroNeuralLife(tmp_path / "one")
    many = FerroNeuralLife(tmp_path / "many")
    one.update(now=NOW, wakeful=_wakeful(errors=1), body_override=body)
    many.update(now=NOW, wakeful=_wakeful(errors=1), body_override=body)

    after_one = one.update(
        now=NOW + timedelta(hours=1),
        wakeful=_wakeful(errors=1, last_cognition_hours=2),
        body_override=body,
    )
    after_many = None
    for minute in range(5, 61, 5):
        after_many = many.update(
            now=NOW + timedelta(minutes=minute),
            wakeful=_wakeful(errors=1, last_cognition_hours=1 + minute / 60),
            body_override=body,
        )
    assert after_many is not None
    for key in ("survival", "reproduction", "evolution"):
        assert abs(after_one["drives"][key] - after_many["drives"][key]) < 0.025
    for key in ("confidence", "frustration", "curiosity", "social_need"):
        assert abs(after_one["affect"][key] - after_many["affect"][key]) < 0.04


def test_zero_elapsed_update_does_not_ratchet_drives(tmp_path):
    life = FerroNeuralLife(tmp_path)
    body = {
        "observed_at": NOW.isoformat(),
        "cpu_pressure": 0.30,
        "memory_pressure": 0.45,
        "disk_pressure": 0.91,
        "x_follower_gap": 0.63,
        "x_recent_decline": 0.0,
    }
    first = life.update(now=NOW, wakeful=_wakeful(errors=4), body_override=body)
    second = life.update(now=NOW, wakeful=_wakeful(errors=4), body_override=body)
    assert second["drives"] == first["drives"]


def test_failed_body_sensors_are_unknown_and_conservatively_pressurized(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path

    life = FerroNeuralLife(tmp_path)
    def fail(*_args, **_kwargs):
        raise OSError("sensor unavailable")
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == "/proc/meminfo":
            return fail()
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(os, "getloadavg", fail)
    monkeypatch.setattr(shutil, "disk_usage", fail)
    monkeypatch.setattr(Path, "read_text", read)
    body = life.collect_body(NOW)
    assert body["sensor_quality"] == {"cpu": "UNKNOWN", "memory": "UNKNOWN", "disk": "UNKNOWN"}
    assert body["cpu_pressure"] == 1.0
    assert body["memory_pressure"] == 1.0
    assert body["disk_pressure"] == 1.0
