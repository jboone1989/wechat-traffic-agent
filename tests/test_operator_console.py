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
        "TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY", "VIRTUALBRAIN_LLM_API_KEY",
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


def test_resource_request_requires_explicit_authorization_before_secret(tmp_path):
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS",
        summary="Cerebras credential needed",
        requested_action="Configure CEREBRAS_API_KEY securely",
        now=NOW,
        details={
            "credential_env": "CEREBRAS_API_KEY",
            "provider_label": "Cerebras",
            "signup_url": "https://cloud.cerebras.ai/",
            "lead_id": "provider-lead:cerebras-test",
        },
    )
    console = OperatorConsole(tmp_path)
    with pytest.raises(ValueError, match="approve the resource request"):
        console.store_secret("CEREBRAS_API_KEY", "csk-test-secret-value", now=NOW)

    approval = console.authorize_resource_request(request["request_id"], approved=True, now=NOW)
    assert approval["authorization_status"] == "APPROVED"
    result = console.store_secret("CEREBRAS_API_KEY", "csk-test-secret-value", now=NOW)

    assert request["request_id"] in result["fulfilled_request_ids"]
    saved = json.loads(next((tmp_path / "application" / "human_assistance").glob("*.json")).read_text())
    assert saved["authorization_status"] == "APPROVED"
    assert saved["resolution_status"] == "FULFILLED"
    assert saved["resolution_evidence_refs"] == ["secret-configured:CEREBRAS_API_KEY"]
    assert "csk-test-secret-value" not in json.dumps(saved)


def test_resource_request_can_be_rejected_and_decision_is_immutable(tmp_path):
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS", summary="NVIDIA access", requested_action="authorize",
        now=NOW, details={"credential_env":"NVIDIA_API_KEY","provider_label":"NVIDIA NIM"},
    )
    console = OperatorConsole(tmp_path)
    rejected = console.authorize_resource_request(request["request_id"], approved=False, now=NOW)
    assert rejected["authorization_status"] == "REJECTED"
    saved = json.loads(next((tmp_path / "application" / "human_assistance").glob("*.json")).read_text())
    assert saved["resolution_status"] == "REJECTED"
    with pytest.raises(ValueError, match="already rejected"):
        console.authorize_resource_request(request["request_id"], approved=True, now=NOW)


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



def test_operator_approval_schedules_immediate_resource_onboarding(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS", summary="Cerebras access", requested_action="authorize",
        now=NOW, details={"credential_env":"CEREBRAS_API_KEY","provider_label":"Cerebras"},
    )

    class Wakeful:
        def __init__(self):
            self.calls = []
        def schedule_resource_followup(self, now, *, seconds=300):
            self.calls.append((now, seconds))

    wakeful = Wakeful()
    service = LiveAgentService(
        state_root=tmp_path, application=None, backend=None, mind=None, wakeful=wakeful,
    )
    client = TestClient(service._app())
    token = (tmp_path / "secrets" / "operator-console.token").read_text().strip()
    login = client.post(
        "/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token},
    )
    assert login.status_code == 200
    decision = client.post(
        f"/operator/api/requests/{request['request_id']}/decision",
        headers={"X-Operator-Request":"1"}, json={"decision":"APPROVE"},
    )
    assert decision.status_code == 200
    assert decision.json()["authorization_status"] == "APPROVED"
    assert decision.json()["onboarding_scheduled"] is True
    assert len(wakeful.calls) == 1
    assert wakeful.calls[0][1] == 5


def test_resource_lifecycle_projects_request_through_verified_active_and_used(tmp_path, monkeypatch):
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS", summary="NVIDIA credential needed", requested_action="authorize",
        now=NOW, details={
            "credential_env":"NVIDIA_API_KEY", "provider_label":"NVIDIA NIM",
            "lead_id":"provider-lead:nvidia-test",
        },
    )
    console = OperatorConsole(tmp_path)
    console.authorize_resource_request(request["request_id"], approved=True, now=NOW)
    console.store_secret("NVIDIA_API_KEY", "nvapi-test-secret", now=NOW)

    discovery = tmp_path / "application" / "resource_discovery"
    (discovery / "provider_verifications").mkdir(parents=True, exist_ok=True)
    (discovery / "trial_receipts").mkdir(parents=True, exist_ok=True)
    (discovery / "provider_verifications" / "nvidia-test.json").write_text(json.dumps({
        "lead_id":"provider-lead:nvidia-test", "status":"VERIFIED_RECURRING_FREE",
        "observed_at":"2026-09-18T00:01:00+00:00",
    }))
    (discovery / "trial_receipts" / "trial.json").write_text(json.dumps({
        "lead_id":"provider-lead:nvidia-test", "credential_env":"NVIDIA_API_KEY",
        "status":"COMPLETED", "model":"nvidia/test-model",
        "observed_at":"2026-09-18T00:02:00+00:00",
    }))
    pool = tmp_path / "application" / "llm_resource_pool"
    (pool / "runtime").mkdir(parents=True, exist_ok=True)
    (pool / "resources.json").write_text(json.dumps({"resources":[{
        "resource_id":"llm-resource:nvidia:test", "provider_label":"NVIDIA NIM",
        "credential_env":"NVIDIA_API_KEY", "model":"nvidia/test-model",
        "pricing_class":"FREE", "lifecycle":"RECURRING_FREE", "reserve":False,
    }]}))
    (pool / "runtime" / "health.json").write_text(json.dumps({"resources":{
        "llm-resource:nvidia:test": {"successes":2,"failures":0,"last_success_at":"2026-09-18T00:03:00+00:00"}
    }}))
    (pool / "runtime" / "latest.json").write_text(json.dumps({
        "selected_resource_id":"llm-resource:nvidia:test","result":"COMPLETED",
        "observed_at":"2026-09-18T00:03:00+00:00",
    }))

    row = OperatorConsole(tmp_path).resource_requests()[0]
    assert row["authorization_status"] == "APPROVED"
    assert row["credential_configured"] is True
    assert row["provider_discovery_status"] == "VERIFIED_RECURRING_FREE"
    assert row["verification_status"] == "VERIFIED_BY_TRIAL"
    assert row["completed_trial_count"] == 1
    assert row["active_resource_ids"] == ["llm-resource:nvidia:test"]
    assert row["used"] is True
    assert [stage["status"] for stage in row["stages"]] == [
        "COMPLETE", "APPROVED", "COMPLETE", "COMPLETE", "COMPLETE", "COMPLETE", "COMPLETE", "COMPLETE"
    ]



def test_provider_discovery_does_not_claim_credential_verification(tmp_path):
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS", summary="Cerebras credential needed", requested_action="authorize",
        now=NOW, details={
            "credential_env":"CEREBRAS_API_KEY", "provider_label":"Cerebras",
            "lead_id":"provider-lead:cerebras-public",
        },
    )
    discovery = tmp_path / "application" / "resource_discovery" / "provider_verifications"
    discovery.mkdir(parents=True, exist_ok=True)
    (discovery / "cerebras-public.json").write_text(json.dumps({
        "lead_id":"provider-lead:cerebras-public",
        "status":"VERIFIED_RECURRING_FREE",
        "observed_at":"2026-09-18T00:01:00+00:00",
    }))
    row = OperatorConsole(tmp_path).resource_requests()[0]
    stages = {stage["stage"]: stage["status"] for stage in row["stages"]}
    assert stages["PROVIDER_DISCOVERED"] == "COMPLETE"
    assert stages["CREDENTIAL_READY"] == "WAITING"
    assert stages["VERIFIED"] == "WAITING"
    assert stages["TRIALLED"] == "WAITING"
    assert row["provider_discovery_status"] == "VERIFIED_RECURRING_FREE"
    assert row["verification_status"] is None


def test_survival_status_unifies_llm_storage_compute_and_hosting(tmp_path):
    status = OperatorConsole(tmp_path).survival_status()
    domains = {row["domain"]: row for row in status["domains"]}
    assert set(domains) == {"LLM", "STORAGE", "COMPUTE", "HOSTING", "REMOTE_MACHINE", "SERVICE"}
    assert domains["STORAGE"]["total_gib"] > 0
    assert domains["COMPUTE"]["cpu_count"] >= 1
    assert domains["HOSTING"]["status"] == "LOCAL_PRIMARY"


def test_operator_http_approve_then_secret_schedules_immediate_onboarding(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    request = HumanAssistanceService(state_root=tmp_path).request(
        kind="MODEL_RESOURCE_ACCESS", summary="NVIDIA access", requested_action="authorize",
        now=NOW, details={"credential_env":"NVIDIA_API_KEY","provider_label":"NVIDIA NIM"},
    )

    class Wakeful:
        def __init__(self): self.calls=[]
        def schedule_resource_followup(self, now, *, seconds=300): self.calls.append((now,seconds))

    wakeful=Wakeful()
    service=LiveAgentService(state_root=tmp_path,application=None,backend=None,mind=None,wakeful=wakeful)
    client=TestClient(service._app())
    token=(tmp_path/"secrets/operator-console.token").read_text().strip()
    assert client.post(
        "/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token}
    ).status_code==200

    blocked=client.post(
        "/operator/api/secrets/NVIDIA_API_KEY", headers={"X-Operator-Request":"1"},
        json={"value":"nvapi-http-secret"},
    )
    assert blocked.status_code==400

    approved=client.post(
        "/operator/api/requests/"+request["request_id"]+"/decision",
        headers={"X-Operator-Request":"1"}, json={"decision":"APPROVE"},
    )
    assert approved.status_code==200
    assert approved.json()["authorization_status"]=="APPROVED"
    assert approved.json()["onboarding_scheduled"] is True

    stored=client.post(
        "/operator/api/secrets/NVIDIA_API_KEY", headers={"X-Operator-Request":"1"},
        json={"value":"nvapi-http-secret"},
    )
    assert stored.status_code==200
    assert stored.json()["onboarding_scheduled"] is True
    assert [seconds for _,seconds in wakeful.calls]==[5,5]
    assert "nvapi-http-secret" not in stored.text


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


def test_operator_login_page_requests_one_time_link_via_wecom(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    monkeypatch.setenv("CONTENT_OPERATOR_PUBLIC_URL", "https://livingruntime.com/operator/")

    class FakeNotifier:
        def __init__(self):
            self.messages = []
        def send_text(self, text):
            self.messages.append(text)
            return {"status": "SENT"}

    notifier = FakeNotifier()
    monkeypatch.setattr(
        "wechat_traffic_agent.human_assistance.operator_notifier",
        lambda: notifier,
    )
    service = LiveAgentService(state_root=tmp_path, application=None, backend=None, mind=None)
    client = TestClient(service._app())

    page = client.get("/operator/")
    assert page.status_code == 200
    assert "Send login link to Enterprise WeChat" in page.text
    assert "Emergency token fallback" in page.text

    missing_header = client.post("/operator/api/request-login", json={})
    assert missing_header.status_code == 403

    response = client.post(
        "/operator/api/request-login",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert response.status_code == 202
    assert response.json() == {"ok": True, "status": "SENT", "expires_in_seconds": 600}
    assert "bootstrap" not in response.text.lower()
    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert "https://livingruntime.com/operator/#bootstrap=" in message
    token = message.split("#bootstrap=", 1)[1].splitlines()[0].strip()
    assert token
    assert token not in (tmp_path / "secrets" / "operator-console-bootstrap.json").read_text()

    accepted = client.post(
        "/operator/api/bootstrap",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert accepted.status_code == 200
    replay = client.post(
        "/operator/api/bootstrap",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert replay.status_code == 401


def test_operator_login_request_is_rate_limited(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")

    class FakeNotifier:
        def __init__(self):
            self.count = 0
        def send_text(self, text):
            self.count += 1
            return {"status": "SENT"}

    notifier = FakeNotifier()
    monkeypatch.setattr(
        "wechat_traffic_agent.human_assistance.operator_notifier",
        lambda: notifier,
    )
    client = TestClient(LiveAgentService(
        state_root=tmp_path, application=None, backend=None, mind=None,
    )._app())
    headers = {"X-Operator-Request": "1"}
    assert client.post("/operator/api/request-login", headers=headers, json={}).status_code == 202
    assert client.post("/operator/api/request-login", headers=headers, json={}).status_code == 429
    assert notifier.count == 1


def test_failed_operator_login_delivery_revokes_one_time_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")

    class FailedNotifier:
        def send_text(self, text):
            return {"status": "NOT_CONFIGURED"}

    monkeypatch.setattr(
        "wechat_traffic_agent.human_assistance.operator_notifier",
        lambda: FailedNotifier(),
    )
    client = TestClient(LiveAgentService(
        state_root=tmp_path, application=None, backend=None, mind=None,
    )._app())
    response = client.post(
        "/operator/api/request-login",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert response.status_code == 503
    assert response.json() == {"error": "login_delivery_unavailable"}
    assert not (tmp_path / "secrets" / "operator-console-bootstrap.json").exists()


def test_survival_control_http_registers_verifies_and_executes_signed_offload(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    from agent_network.crypto import Identity
    from wechat_traffic_agent.survival_resources import SurvivalResourceManager

    remote_identity = Identity.generate("remote.edge.http")

    def remote_call(self, url, *, method, payload=None, timeout):
        if method == "GET":
            return {
                "status":"HEALTHY",
                "agent_id":remote_identity.agent_id,
                "key_id":remote_identity.key_id,
                "public_key":remote_identity.public_key,
                "capabilities":["survival.workload.deploy.v1"],
            }
        receipt = {
            "type":"survival.workload.deploy.receipt.v1",
            "status":"DEPLOYED",
            "action_id":payload["action_id"],
            "request_hash":payload["object_hash"],
            "deployment_id":"deployment:http-1",
            "agent_id":remote_identity.agent_id,
            "key_id":remote_identity.key_id,
        }
        receipt["signature"] = remote_identity.sign_object(receipt)
        return receipt

    monkeypatch.setattr(SurvivalResourceManager, "_http_json", remote_call)

    class Wakeful:
        def schedule_resource_followup(self, now, *, seconds=300):
            self.followup = (now, seconds)

    client = TestClient(LiveAgentService(
        state_root=tmp_path, application=None, backend=None, mind=None, wakeful=Wakeful(),
    )._app())
    token = (tmp_path / "secrets" / "operator-console.token").read_text().strip()
    assert client.post(
        "/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token}
    ).status_code == 200

    created = client.post(
        "/operator/api/survival/hosts",
        headers={"X-Operator-Request":"1"},
        json={
            "name":"edge-one",
            "health_url":"https://edge.example.test/health",
            "control_url":"https://edge.example.test/v1/workloads",
            "reliability":"BEST_EFFORT",
            "capacity":{"cpu_count":2,"memory_gib":4},
        },
    )
    assert created.status_code == 201
    host_id = created.json()["host_id"]

    verified = client.post(
        "/operator/api/survival/hosts/" + host_id + "/verify",
        headers={"X-Operator-Request":"1"}, json={},
    )
    assert verified.status_code == 200
    assert verified.json()["deployment_capable"] is True

    planned = client.post(
        "/operator/api/survival/offload",
        headers={"X-Operator-Request":"1"},
        json={"service_id":"website-static","host_id":host_id},
    )
    assert planned.status_code == 201
    action_id = planned.json()["action_id"]

    deployed = client.post(
        "/operator/api/survival/offload/" + action_id + "/execute",
        headers={"X-Operator-Request":"1"}, json={},
    )
    assert deployed.status_code == 200
    assert deployed.json()["status"] == "DEPLOYED"

    primary = client.post(
        "/operator/api/survival/offload",
        headers={"X-Operator-Request":"1"},
        json={"service_id":"content-agent-core","host_id":host_id},
    )
    assert primary.status_code == 400
    assert "primary stateful" in primary.json()["error"]


def test_survival_need_operator_decision_http(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    from wechat_traffic_agent.survival_resources import SurvivalResourceManager

    manager = SurvivalResourceManager(tmp_path)
    manager.needs_path.parent.mkdir(parents=True, exist_ok=True)
    manager.needs_path.write_text(json.dumps([{
        "need_id":"survival-need:manual-offload",
        "domain":"SERVICE",
        "action":"OFFLOAD_NON_CORE_SERVICE",
        "status":"PENDING",
        "authorization":"OPERATOR_REQUIRED",
        "urgency":"NORMAL",
        "reason":"relieve local pressure",
        "created_at":NOW.isoformat(),
    }]))

    class Wakeful:
        def __init__(self): self.calls=[]
        def schedule_resource_followup(self, now, *, seconds=300):
            self.calls.append(seconds)

    wakeful = Wakeful()
    client = TestClient(LiveAgentService(
        state_root=tmp_path, application=None, backend=None, mind=None, wakeful=wakeful,
    )._app())
    token = (tmp_path / "secrets" / "operator-console.token").read_text().strip()
    assert client.post(
        "/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token}
    ).status_code == 200

    response = client.post(
        "/operator/api/survival/needs/survival-need:manual-offload/decision",
        headers={"X-Operator-Request":"1"}, json={"decision":"APPROVE"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    assert response.json()["followup_scheduled"] is True
    assert wakeful.calls == [5]

def test_operator_can_bootstrap_remote_host_with_one_time_ssh_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    captured = {}

    def fake_bootstrap(self, **kwargs):
        captured.update(kwargs)
        return {
            "host_id":"remote-host:test",
            "name":"vultr-edge",
            "ssh_host":"207.246.99.253",
            "ssh_port":22,
            "ssh_username":"root",
            "ssh_host_key_sha256":"SHA256:test",
            "management_transport":"SSH_KEY",
            "status":"ACTIVE",
            "deployment_capable":False,
            "receiver_status":"NOT_PROVISIONED",
            "host_instance_id":"host-instance:remote-test",
            "capacity":{"cpu_count":2,"memory_total_gib":4.0},
            "reliability":"OPERATOR_MANAGED",
            "trust_source":"OPERATOR_SSH_BOOTSTRAP",
        }

    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.SurvivalResourceManager.bootstrap_ssh_host",
        fake_bootstrap,
    )

    class Wakeful:
        def __init__(self): self.calls=[]
        def schedule_resource_followup(self, now, *, seconds=300): self.calls.append(seconds)

    wakeful=Wakeful()
    client=TestClient(LiveAgentService(
        state_root=tmp_path, application=None, backend=None, mind=None, wakeful=wakeful,
    )._app())
    token=(tmp_path/"secrets"/"operator-console.token").read_text().strip()
    assert client.post(
        "/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token}
    ).status_code==200
    secret="x"*16
    response=client.post(
        "/operator/api/survival/hosts/bootstrap",
        headers={"X-Operator-Request":"1"},
        json={"host":"207.246.99.253","port":22,"username":"root","password":secret,"name":"vultr-edge"},
    )
    assert response.status_code==201
    assert captured["password"]==secret
    body=response.json()
    assert body["status"]=="ACTIVE"
    assert body["management_transport"]=="SSH_KEY"
    assert body["followup_scheduled"] is True
    assert secret not in response.text
    assert wakeful.calls==[5]
    page=client.get("/operator/")
    assert "Hand a server to Ferro" in page.text
    assert "IP or hostname" in page.text
    assert "One-time SSH password" in page.text

def _login_operator(client: TestClient, root) -> None:
    token = (root / "secrets" / "operator-console.token").read_text().strip()
    response = client.post(
        "/operator/api/session",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert response.status_code == 200


def test_operator_page_exposes_browser_runtime_and_jev_smoke_action(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    service = LiveAgentService(
        state_root=tmp_path,
        application=None,
        backend=None,
        mind=None,
    )
    client = TestClient(service._app())
    page = client.get("/operator/")
    assert page.status_code == 200
    assert "Browser runtime" in page.text
    assert "Test Jev" in page.text
    assert "/operator/api/browser/jev/test" in page.text


def test_operator_jev_smoke_endpoint_is_authenticated_and_bounded(tmp_path, monkeypatch):
    from wechat_traffic_agent.browser_runtime import BrowserTaskResult, JevBrowserExecutor

    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")

    class FakeExecutor:
        async def run(self, **kwargs):
            assert kwargs["url"] == "https://example.com"
            assert kwargs["task_type"] == "JEV_OPERATOR_SMOKE"
            return BrowserTaskResult(
                provider="JEV",
                status="COMPLETED",
                elapsed_ms=17,
                action_count=0,
                final_url="https://example.com/",
            )

    monkeypatch.setattr(
        JevBrowserExecutor,
        "from_environment",
        classmethod(lambda cls, state_root: FakeExecutor()),
    )
    service = LiveAgentService(
        state_root=tmp_path,
        application=None,
        backend=None,
        mind=None,
    )

    client = TestClient(service._app())
    unauth = client.post(
        "/operator/api/browser/jev/test",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert unauth.status_code == 401

    _login_operator(client, tmp_path)
    no_header = client.post("/operator/api/browser/jev/test", json={})
    assert no_header.status_code == 403

    result = client.post(
        "/operator/api/browser/jev/test",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert result.status_code == 200
    assert result.json() == {
        "ok": True,
        "status": "JEV_TEST_PASSED",
        "provider": "JEV",
        "elapsed_ms": 17,
        "action_count": 0,
        "final_url": "https://example.com/",
    }
