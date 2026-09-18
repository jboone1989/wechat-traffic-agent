from datetime import datetime, timedelta, timezone
import json
import os

import pytest
from starlette.testclient import TestClient

from wechat_traffic_agent.human_assistance import HumanAssistanceService
from wechat_traffic_agent.live_agent import LiveAgentService
from wechat_traffic_agent.operator_console import (
    OperatorAuthenticator,
    OperatorConsole,
    OperatorSecretVault,
)


NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    keys = (
        "CEREBRAS_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY",
        "VIRTUALBRAIN_LLM_API_KEY",
    )
    managed = []
    for key in keys:
        managed.append(key)
        for suffix in (
            "_QUOTA_REMAINING", "_QUOTA_LIMIT", "_QUOTA_UNIT", "_RESET_AT", "_EXPIRES_AT"
        ):
            managed.append(key + suffix)
    for key in managed:
        monkeypatch.delenv(key, raising=False)
    yield
    # OperatorSecretVault intentionally updates os.environ so a newly provisioned
    # credential is usable by this process immediately. Clean those direct writes
    # explicitly so one operator-console test cannot change provider discovery in
    # later resource-routing tests.
    for key in managed:
        os.environ.pop(key, None)


def test_secret_vault_is_write_only_and_persists_mode_0600(tmp_path):
    vault = OperatorSecretVault(tmp_path)
    value = "csk-test-secret-value"
    result = vault.put("CEREBRAS_API_KEY", value)

    assert result["configured"] is True
    assert value not in json.dumps(result)
    assert os.environ["CEREBRAS_API_KEY"] == value

    path = tmp_path / "secrets" / "model-providers.env"
    assert path.read_text() == "CEREBRAS_API_KEY=" + value + "\n"
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    status = vault.status()
    assert status[0]["env_name"] == "CEREBRAS_API_KEY"
    assert status[0]["configured"] is True
    assert value not in json.dumps(status)


def test_secret_vault_rejects_unknown_names_and_multiline_values(tmp_path):
    vault = OperatorSecretVault(tmp_path)
    with pytest.raises(ValueError, match="unsupported secret"):
        vault.put("ARBITRARY_ENV", "abcdefgh")
    with pytest.raises(ValueError, match="no newlines"):
        vault.put("CEREBRAS_API_KEY", "abcd\nefgh")


def test_operator_auth_uses_short_lived_server_session(tmp_path):
    auth = OperatorAuthenticator(tmp_path, ttl=timedelta(minutes=1))
    token = auth.token_path.read_text().strip()
    assert oct(auth.token_path.stat().st_mode & 0o777) == "0o600"
    assert auth.login("wrong", now=NOW) is None

    session = auth.login(token, now=NOW)
    assert session
    assert auth.valid(session, now=NOW + timedelta(seconds=59))
    assert not auth.valid(session, now=NOW + timedelta(minutes=2))


def test_store_secret_fulfills_matching_human_request(tmp_path):
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS",
        summary="Cerebras credential needed",
        requested_action="Configure CEREBRAS_API_KEY securely",
        now=NOW,
        details={
            "credential_env": "CEREBRAS_API_KEY",
            "provider_label": "Cerebras",
            "signup_url": "https://cloud.cerebras.ai/",
        },
    )
    console = OperatorConsole(tmp_path)
    result = console.store_secret("CEREBRAS_API_KEY", "csk-test-secret-value", now=NOW)

    assert request["request_id"] in result["fulfilled_request_ids"]
    saved = json.loads(next((tmp_path / "application" / "human_assistance").glob("*.json")).read_text())
    assert saved["resolution_status"] == "FULFILLED"
    assert saved["resolution_evidence_refs"] == ["secret-configured:CEREBRAS_API_KEY"]
    assert "csk-test-secret-value" not in json.dumps(saved)


def test_operator_http_flow_never_echoes_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    service = LiveAgentService(
        state_root=tmp_path,
        application=None,
        backend=None,
        mind=None,
    )
    app = service._app()
    client = TestClient(app)

    unauth = client.get("/operator/api/status")
    assert unauth.status_code == 401

    token = (tmp_path / "secrets" / "operator-console.token").read_text().strip()
    login = client.post(
        "/operator/api/session",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert login.status_code == 200
    assert "ferro_operator_session" in login.cookies

    value = "csk-http-secret-value"
    stored = client.post(
        "/operator/api/secrets/CEREBRAS_API_KEY",
        headers={"X-Operator-Request": "1"},
        json={"value": value},
    )
    assert stored.status_code == 200
    assert stored.json()["configured"] is True
    assert value not in stored.text

    status = client.get("/operator/api/status")
    assert status.status_code == 200
    assert value not in status.text
    assert any(
        row["env_name"] == "CEREBRAS_API_KEY" and row["configured"]
        for row in status.json()["secrets"]
    )


def test_quota_metadata_persists_without_echoing_secret(tmp_path, monkeypatch):
    vault = OperatorSecretVault(tmp_path)
    vault.put("OPENROUTER_API_KEY", "or-test-secret-value")
    result = vault.put_metadata("OPENROUTER_API_KEY", {
        "quota_remaining": "120",
        "quota_limit": "500",
        "quota_unit": "REQUESTS",
        "quota_reset_at": "2026-09-19T00:00:00+00:00",
        "expires_at": "2026-10-01T00:00:00+00:00",
    })

    assert result["quota_remaining"] == 120.0
    assert result["quota_limit"] == 500.0
    assert result["quota_unit"] == "REQUESTS"
    assert result["expires_at"] == "2026-10-01T00:00:00+00:00"
    raw = (tmp_path / "secrets" / "model-providers.env").read_text()
    assert "OPENROUTER_API_KEY_QUOTA_REMAINING=120" in raw
    assert "OPENROUTER_API_KEY_EXPIRES_AT=2026-10-01T00:00:00+00:00" in raw
    assert "or-test-secret-value" not in json.dumps(result)


def test_quota_metadata_rejects_negative_or_timezone_free_values(tmp_path):
    vault = OperatorSecretVault(tmp_path)
    with pytest.raises(ValueError, match="non-negative"):
        vault.put_metadata("NVIDIA_API_KEY", {
            "quota_remaining": "-1",
            "quota_unit": "REQUESTS",
        })
    with pytest.raises(ValueError, match="timezone"):
        vault.put_metadata("NVIDIA_API_KEY", {
            "quota_remaining": "1",
            "quota_unit": "REQUESTS",
            "expires_at": "2026-10-01T00:00:00",
        })


def test_resource_status_exposes_health_policy_and_route_without_prompt_data(tmp_path):
    root = tmp_path / "application" / "llm_resource_pool"
    (root / "runtime").mkdir(parents=True)
    (root / "resources.json").write_text(json.dumps({
        "resources": [{
            "resource_id": "llm-resource:openrouter:test",
            "provider_label": "OpenRouter",
            "model": "openrouter/free",
            "pricing_class": "FREE",
            "lifecycle": "RECURRING_FREE",
            "reserve": False,
            "quality_score": 0.75,
            "success_rate": 0.8,
            "mean_latency_ms": 1234.0,
            "metrics_source": "EMPIRICAL_TRIALS",
            "quota_remaining": 10,
            "quota_limit": 100,
            "quota_unit": "REQUESTS",
            "quota_reset_at": "2026-09-19T00:00:00+00:00",
            "expires_at": None,
        }]
    }))
    (root / "policy.json").write_text(json.dumps({
        "routing_mode": "FREE_FIRST_RESERVE_PAID",
        "pressure": "CONSERVE",
        "prefer_free": True,
        "minimum_quality": 0.7,
        "max_failovers": 2,
        "paid_available_funds": 93.0,
        "paid_reserve_floor": 20.0,
        "mind_tick": 99,
        "secret_should_not_surface": "hidden",
    }))
    (root / "runtime" / "health.json").write_text(json.dumps({
        "resources": {
            "llm-resource:openrouter:test": {
                "successes": 3,
                "failures": 1,
                "consecutive_failures": 0,
                "last_error_code": "",
                "cooldown_until": None,
                "consumed_quota": 4,
            }
        }
    }))
    (root / "runtime" / "latest.json").write_text(json.dumps({
        "result": "COMPLETED",
        "selected_resource_id": "llm-resource:openrouter:test",
        "attempted_resource_ids": ["llm-resource:openrouter:test"],
        "pressure": "CONSERVE",
        "mind_tick": 99,
        "prompt_sha256": "sha256:should-not-surface",
    }))

    status = OperatorConsole(tmp_path).resource_status()
    assert status["free_count"] == 1
    assert status["resources"][0]["successes"] == 3
    assert status["resources"][0]["quota_remaining"] == 10
    assert status["policy"]["pressure"] == "CONSERVE"
    assert status["latest_route"]["result"] == "COMPLETED"
    encoded = json.dumps(status)
    assert "secret_should_not_surface" not in encoded
    assert "should-not-surface" not in encoded


def test_operator_http_can_update_resource_metadata_without_secret_echo(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    service = LiveAgentService(
        state_root=tmp_path,
        application=None,
        backend=None,
        mind=None,
    )
    client = TestClient(service._app())
    token = (tmp_path / "secrets" / "operator-console.token").read_text().strip()
    login = client.post(
        "/operator/api/session",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert login.status_code == 200

    saved = client.post(
        "/operator/api/resources/OPENROUTER_API_KEY",
        headers={"X-Operator-Request": "1"},
        json={
            "quota_remaining": "9",
            "quota_limit": "100",
            "quota_unit": "REQUESTS",
            "quota_reset_at": "2026-09-19T00:00:00+00:00",
            "expires_at": "",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["quota_remaining"] == 9.0
    assert "API_KEY=" not in saved.text

    status = client.get("/operator/api/status")
    assert status.status_code == 200
    row = next(x for x in status.json()["secrets"] if x["env_name"] == "OPENROUTER_API_KEY")
    assert row["quota_remaining"] == 9.0
    assert row["quota_limit"] == 100.0


def test_deepseek_stable_reserve_key_is_write_only(tmp_path):
    vault = OperatorSecretVault(tmp_path)
    secret = "ds-stable-secret-value"
    result = vault.put("VIRTUALBRAIN_LLM_API_KEY", secret)
    assert result["configured"] is True
    assert secret not in json.dumps(vault.status())
    row = next(x for x in vault.status() if x["env_name"] == "VIRTUALBRAIN_LLM_API_KEY")
    assert row["label"] == "DeepSeek stable reserve"
    assert row["quota_supported"] is False


def test_one_time_operator_access_persists_only_digest_and_is_single_use(tmp_path):
    from datetime import timedelta
    auth=OperatorAuthenticator(tmp_path,ttl=timedelta(minutes=30))
    token=auth.issue_one_time_access(now=NOW,ttl=timedelta(hours=2))
    raw=auth.bootstrap_path.read_text()
    assert token not in raw
    assert "token_sha256" in raw

    session=auth.consume_one_time_access(token,now=NOW+timedelta(minutes=1))
    assert session
    assert auth.valid(session,now=NOW+timedelta(minutes=2))
    assert not auth.bootstrap_path.exists()
    assert auth.consume_one_time_access(token,now=NOW+timedelta(minutes=3)) is None


def test_operator_http_one_time_bootstrap_sets_session_and_cannot_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED","true")
    service=LiveAgentService(state_root=tmp_path,application=None,backend=None,mind=None)
    client=TestClient(service._app())
    auth=service._app  # keep service reachable; token issued from same persisted state
    console=OperatorConsole(tmp_path)
    token=console.auth.issue_one_time_access()

    first=client.post(
        "/operator/api/bootstrap",
        headers={"X-Operator-Request":"1"},
        json={"token":token},
    )
    assert first.status_code==200
    assert "ferro_operator_session" in first.cookies
    assert token not in first.text

    replay=client.post(
        "/operator/api/bootstrap",
        headers={"X-Operator-Request":"1"},
        json={"token":token},
    )
    assert replay.status_code==401
