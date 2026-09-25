from datetime import datetime, timedelta, timezone
import json

import pytest

from wechat_traffic_agent.resource_regulation import ResourceRegulator


NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _legacy_llm_mode(monkeypatch):
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")


def _policy(root, name, pressure, observed_at=NOW):
    file = root / "application" / name / "policy.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"pressure": pressure, "observed_at": observed_at.isoformat()}))


def test_chatgpt_bridge_replaces_api_balance_pressure_for_cognition(tmp_path, monkeypatch):
    _policy(tmp_path, "survival_resources", "NORMAL")
    _policy(tmp_path, "llm_resource_pool", "CRITICAL")
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# available\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))

    regulator = ResourceRegulator(tmp_path)

    assert regulator.pressure(NOW) == "NORMAL"
    admitted = regulator.claim("CONTENT_COGNITION", NOW)
    assert admitted["allowed"] is True
    assert admitted["reason"] == "WITHIN_BUDGET"


def test_owner_goal_chatgpt_lane_survives_host_pressure(tmp_path, monkeypatch):
    _policy(tmp_path, "survival_resources", "CRITICAL")
    _policy(tmp_path, "llm_resource_pool", "CRITICAL")
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# available\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))

    admitted = ResourceRegulator(tmp_path).claim(
        "CONTENT_COGNITION", NOW, owner_authorized=True
    )

    assert admitted["allowed"] is True
    assert admitted["reason"] == "OWNER_GOAL_CHATGPT_COGNITION_LANE"


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


def test_owner_goal_cognition_uses_verified_free_route_under_host_pressure(tmp_path, monkeypatch):
    _policy(tmp_path, "survival_resources", "CRITICAL")
    _policy(tmp_path, "llm_resource_pool", "CRITICAL")
    monkeypatch.setenv("TEST_FREE_API_KEY", "present")
    manifest = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    manifest.write_text(json.dumps({
        "observed_at": NOW.isoformat(),
        "resources": [{
            "resource_id": "free-owner", "pricing_class": "FREE",
            "credential_env": "TEST_FREE_API_KEY", "metrics_source": "EMPIRICAL_TRIALS",
            "quota_remaining": 10, "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }],
    }))

    regulator = ResourceRegulator(tmp_path)

    assert regulator.claim("CONTENT_COGNITION", NOW)["allowed"] is False
    admitted = regulator.claim(
        "CONTENT_COGNITION",
        NOW,
        owner_authorized=True,
    )
    assert admitted["allowed"] is True
    assert admitted["pressure"] == "CRITICAL"
    assert admitted["reason"] == "OWNER_GOAL_FREE_COGNITION_LANE"


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
