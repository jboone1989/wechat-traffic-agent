from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.interoceptive_fault import (
    InteroceptiveFaultSystem,
    RECOVERY_HEALTHY_STREAK,
    classify_task_result,
    read_interoceptive_projection,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


class _NeutralL1:
    def assess_health(self, *, task, deterministic, recent_observations=()):
        abnormal = bool(deterministic.get("abnormal"))
        return {
            "schema_version": "ferro-l1-health.v1",
            "status": "ASSESSED",
            "health_state": "UNKNOWN" if abnormal else "HEALTHY",
            "subsystem": deterministic.get("subsystem") if abnormal else None,
            "provider": "TEST",
            "model": "test",
            "confidence": 1.0,
            "probabilities": {},
            "latency_ms": 0,
            "decided_at": NOW.isoformat(),
        }


def _system(tmp_path):
    return InteroceptiveFaultSystem(tmp_path, l1_decisions=_NeutralL1())


def test_repeated_fallback_becomes_felt_fault_and_requests_diagnosis(tmp_path):
    system = _system(tmp_path)

    first = system.observe(
        "CONTENT_COGNITION",
        {
            "status": "OK",
            "composition": {"status": "FALLBACK_USED"},
            "cycle": "c1",
        },
        now=NOW,
    )
    assert first["status"] == "SYMPTOM_OBSERVED"
    assert first["observation"]["primary_symptom"] == "fallback_dependency"
    assert first["should_diagnose"] is False
    assert first["projection"]["status"] == "WATCH"

    second = system.observe(
        "CONTENT_COGNITION",
        {
            "status": "OK",
            "composition": {"status": "FALLBACK_USED"},
            "cycle": "c2",
        },
        now=NOW + timedelta(minutes=1),
    )
    assert second["should_diagnose"] is False
    assert second["observation"]["severity"] > first["observation"]["severity"]

    third = system.observe(
        "CONTENT_COGNITION",
        {
            "status": "OK",
            "composition": {"status": "FALLBACK_USED"},
            "cycle": "c3",
        },
        now=NOW + timedelta(minutes=2),
    )
    assert third["should_diagnose"] is True
    assert third["diagnostic_failure"]["error_type"] == "INTEROCEPTIVE_FAULT"
    assert third["diagnostic_failure"]["symptoms"][0]["kind"] == "fallback_dependency"
    assert third["diagnostic_failure"]["symptoms"][0]["path"] == "result.composition"
    assert third["diagnostic_failure"]["symptoms"][0]["detail"] == "FALLBACK_USED"
    assert third["diagnostic_failure"]["status_evidence"]
    assert third["projection"]["distress"] >= third["observation"]["severity"]
    assert third["projection"]["active_episode_ids"] == [
        third["observation"]["episode_id"]
    ]

    episode_path = next(
        (tmp_path / "application" / "interoception" / "episodes").glob("*.json")
    )
    episode = json.loads(episode_path.read_text())
    assert episode["occurrences"] == 3
    assert episode["diagnosis_requested_at"] is not None


def test_recovery_requires_sustained_healthy_evidence(tmp_path):
    system = _system(tmp_path)
    sick = system.observe(
        "NEURAL_PLAY",
        {"status": "DEGRADED", "brain_lab": {"status": "ERROR"}, "cycle": "bad"},
        now=NOW,
    )
    episode_id = sick["observation"]["episode_id"]

    for index in range(1, RECOVERY_HEALTHY_STREAK):
        observed = system.observe(
            "NEURAL_PLAY",
            {"status": "OK", "cycle": f"healthy-{index}"},
            now=NOW + timedelta(minutes=index),
        )
        assert episode_id in observed["projection"]["active_episode_ids"]

    recovered = system.observe(
        "NEURAL_PLAY",
        {"status": "OK", "cycle": "healthy-final"},
        now=NOW + timedelta(minutes=RECOVERY_HEALTHY_STREAK),
    )
    assert episode_id not in recovered["projection"]["active_episode_ids"]
    episode_path = next(
        (tmp_path / "application" / "interoception" / "episodes").glob("*.json")
    )
    episode = json.loads(episode_path.read_text())
    assert episode["status"] == "RECOVERED_VERIFIED"
    assert (
        episode["recovery_verification"]["observed_consecutive_healthy"]
        >= RECOVERY_HEALTHY_STREAK
    )


def test_retry_breaks_recovery_and_remains_pathological(tmp_path):
    system = _system(tmp_path)
    system.observe(
        "OBSERVE_WORLD",
        {"status": "ERROR", "cycle": "bad"},
        now=NOW,
    )
    system.observe(
        "OBSERVE_WORLD",
        {"status": "OK", "cycle": "healthy-1"},
        now=NOW + timedelta(minutes=1),
    )
    retry = system.observe(
        "OBSERVE_WORLD",
        {
            "status": "OK",
            "cycle": "retry",
            "network": {"status": "RETRY_SCHEDULED"},
        },
        now=NOW + timedelta(minutes=2),
    )
    assert retry["status"] == "SYMPTOM_OBSERVED"
    assert retry["observation"]["primary_symptom"] == "retry_dependency"

    old_episode = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/interoception/episodes").glob("*.json")
        if json.loads(path.read_text()).get("primary_symptom") == "hard_error"
    ][0]
    assert old_episode["healthy_streak"] == 0
    assert old_episode["status"] == "OPEN"


def test_prediction_error_and_negative_world_utility_are_body_signals():
    classified = classify_task_result(
        "CHECK_OUTCOMES",
        {
            "status": "SETTLED",
            "outcome": {
                "prediction_error": 0.72,
                "world_utility": -0.6,
            },
        },
    )
    kinds = {row["kind"] for row in classified["symptoms"]}
    assert classified["abnormal"] is True
    assert "prediction_error" in kinds
    assert "negative_world_utility" in kinds
    assert classified["base_severity"] >= 0.6


def test_persisted_projection_is_readable_by_cognition(tmp_path):
    system = _system(tmp_path)
    observed = system.observe(
        "CONTENT_COGNITION",
        {"status": "DEGRADED", "cycle": "c1"},
        now=NOW,
    )
    projection = read_interoceptive_projection(tmp_path)
    assert projection["latest_observation_id"] == observed["observation"]["observation_id"]
    assert projection["status"] in {"WATCH", "ILL"}
    assert projection["suspected_faults"]



def test_l1_can_detect_stuck_without_a_hard_error(tmp_path):
    class StuckL1:
        def assess_health(self, *, task, deterministic, recent_observations=()):
            return {
                "schema_version": "ferro-l1-health.v1",
                "status": "ASSESSED",
                "health_state": "STUCK",
                "subsystem": "goal_execution",
                "provider": "TEST",
                "model": "test",
                "confidence": 0.92,
                "probabilities": {"STUCK__goal_execution": 0.92},
                "latency_ms": 1,
                "decided_at": NOW.isoformat(),
            }

    system = InteroceptiveFaultSystem(tmp_path, l1_decisions=StuckL1())
    observed = system.observe(
        "CONTENT_COGNITION",
        {"status": "OK", "cycle": "looks-successful-but-no-progress"},
        now=NOW,
    )

    assert observed["status"] == "SYMPTOM_OBSERVED"
    assert observed["observation"]["l1_health"]["health_state"] == "STUCK"
    assert observed["observation"]["primary_symptom"] == "l1_stuck"
    assert observed["observation"]["subsystem"] == "goal_execution"
    assert observed["projection"]["status"] in {"WATCH", "ILL"}
