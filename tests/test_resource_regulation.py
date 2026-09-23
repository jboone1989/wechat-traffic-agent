from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.resource_regulation import ResourceRegulator


NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def _policy(root, name, pressure, observed_at=NOW):
    file = root / "application" / name / "policy.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"pressure": pressure, "observed_at": observed_at.isoformat()}))


def test_free_verified_route_preserves_cognition_when_paid_balance_is_critical(tmp_path, monkeypatch):
    _policy(tmp_path, "survival_resources", "NORMAL")
    _policy(tmp_path, "llm_resource_pool", "CRITICAL")
    monkeypatch.setenv("TEST_FREE_API_KEY", "present")
    manifest = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    manifest.write_text(json.dumps({
        "observed_at": NOW.isoformat(),
        "resources": [{
            "resource_id": "free-1", "pricing_class": "FREE",
            "credential_env": "TEST_FREE_API_KEY", "metrics_source": "EMPIRICAL_TRIALS",
            "quota_remaining": 10, "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }],
    }))
    regulator = ResourceRegulator(tmp_path)
    assert regulator.pressure(NOW) == "WATCH"
    assert regulator.claim("CONTENT_COGNITION", NOW)["allowed"] is True
    assert regulator.assess("CONTENT_COGNITION", NOW + timedelta(seconds=1))["reason"] == "TASK_COOLDOWN"
    assert regulator.assess("RESOURCE_REVIEW", NOW + timedelta(seconds=1))["reason"] == "RECOVERY_LANE"


def test_unknown_sensor_and_exhausted_free_route_defer_nonessential_work(tmp_path, monkeypatch):
    _policy(tmp_path, "survival_resources", "NORMAL", NOW - timedelta(hours=3))
    _policy(tmp_path, "llm_resource_pool", "CRITICAL")
    monkeypatch.setenv("TEST_FREE_API_KEY", "present")
    manifest = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    manifest.write_text(json.dumps({
        "observed_at": NOW.isoformat(),
        "resources": [{
            "resource_id": "free-1", "pricing_class": "FREE",
            "credential_env": "TEST_FREE_API_KEY", "metrics_source": "EMPIRICAL_TRIALS",
            "quota_remaining": 0,
        }],
    }))
    regulator = ResourceRegulator(tmp_path)
    assert regulator.pressure(NOW) == "UNKNOWN"
    assert regulator.assess("NEURAL_PLAY", NOW)["allowed"] is False
    assert regulator.assess("RESOURCE_REVIEW", NOW)["allowed"] is True


def test_rest_intention_and_failed_body_sensor_inhibit_planned_work(tmp_path):
    _policy(tmp_path, "survival_resources", "NORMAL")
    _policy(tmp_path, "llm_resource_pool", "NORMAL")
    neural = tmp_path / "application" / "neural_life.json"
    neural.write_text(json.dumps({
        "updated_at": NOW.isoformat(), "intention": {"name": "REST"},
        "body": {"sensor_quality": {"cpu": "UNKNOWN", "memory": "OBSERVED"}},
    }))
    regulator = ResourceRegulator(tmp_path)
    blocked = regulator.assess("CONTENT_COGNITION", NOW)
    assert blocked["reason"] == "NEURAL_REST"
    assert blocked["pressure"] == "UNKNOWN"
    assert regulator.assess("RESOURCE_REVIEW", NOW)["allowed"] is True
