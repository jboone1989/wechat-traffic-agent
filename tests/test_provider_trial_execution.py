from datetime import datetime, timezone
import json

from agent_runtime.resource_discovery import ProviderLead, ProviderVerification
from wechat_traffic_agent.human_assistance import HumanAssistanceService
from wechat_traffic_agent.resource_scout import ContentResourceScout

NOW = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)


class Runtime:
    def invoke(self, name, payload):
        return {"results": []}


class FakeResponse:
    def __init__(self, content):
        self._payload = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _ready_scout(tmp_path, monkeypatch):
    scout = ContentResourceScout(state_root=tmp_path, capability_runtime=Runtime())
    scout.scout._write_provider_lead(ProviderLead(
        "provider-lead:nvidia", "resource-opportunity:nvidia", "NVIDIA NIM",
        "NVIDIA_API_KEY", "https://build.nvidia.com/", "nvidia.com", NOW.isoformat(), "receipt:lead",
        evidence_refs=("receipt:lead",), status="VERIFIED_RECURRING_FREE",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "provider-lead:nvidia", "VERIFIED_RECURRING_FREE", NOW.isoformat(),
        free_status="RECURRING_FREE", rate_limit_evidence=True,
        official_urls=("https://build.nvidia.com/",), evidence_refs=("receipt:official",),
    ))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-secret-key")
    return scout


def _fake_post(calls):
    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        prompt = json["messages"][0]["content"]
        if "Bitcoin gained 5%" in prompt:
            return FakeResponse("POSITIVE")
        if "SOL moved from $180" in prompt:
            return FakeResponse('{"asset":"SOL","change_pct":5,"direction":"up"}')
        return FakeResponse("BTC rose 3% today.")
    return post

def test_nvidia_trial_executes_three_cases_and_persists_selection(tmp_path, monkeypatch):
    scout = _ready_scout(tmp_path, monkeypatch)
    plans = scout.plan_trials(now=NOW)
    assert plans["plans"][0]["status"] == "READY_TO_TRIAL"
    calls = []
    monkeypatch.setattr("wechat_traffic_agent.provider_trials.httpx.post", _fake_post(calls))
    result = scout.execute_ready_trials(now=NOW, trial_plans=plans)
    assert result["created_count"] == 3
    assert result["completed_count"] == 3
    assert result["failed_count"] == 0
    assert len(calls) == 3
    assert result["selection"]["status"] == "SELECTED_EMPIRICALLY"
    assert result["selection"]["provider_label"] == "NVIDIA NIM"
    assert len(scout.scout.model_trials()) == 3
    assert all(row.observed_cost == 0.0 for row in scout.scout.model_trials())

    for path in tmp_path.rglob("*.json"):
        assert "test-secret-key" not in path.read_text()


def test_nvidia_trial_is_idempotent_and_does_not_spend_again(tmp_path, monkeypatch):
    scout = _ready_scout(tmp_path, monkeypatch)
    plans = scout.plan_trials(now=NOW)
    calls = []
    monkeypatch.setattr("wechat_traffic_agent.provider_trials.httpx.post", _fake_post(calls))
    first = scout.execute_ready_trials(now=NOW, trial_plans=plans)
    second = scout.execute_ready_trials(now=NOW, trial_plans=plans)
    assert first["created_count"] == 3
    assert second["status"] == "ALREADY_EVALUATED"
    assert second["created_count"] == 0
    assert len(calls) == 3


def test_human_request_can_be_fulfilled_by_verified_trial_evidence(tmp_path):
    service = HumanAssistanceService(state_root=tmp_path)
    row = service.request(
        kind="MODEL_RESOURCE_ACCESS",
        summary="Need provider access",
        requested_action="Configure NVIDIA_API_KEY securely",
        now=NOW,
        details={"credential_env": "NVIDIA_API_KEY"},
    )
    resolved = service.fulfill_matching(
        kind="MODEL_RESOURCE_ACCESS", detail_key="credential_env",
        detail_value="NVIDIA_API_KEY", now=NOW,
        evidence_refs=("provider-trial-plan:x", "model-trial:y"),
    )
    assert [item["request_id"] for item in resolved] == [row["request_id"]]
    saved = json.loads(next((tmp_path / "application" / "human_assistance").glob("*.json")).read_text())
    assert saved["resolution_status"] == "FULFILLED"
    assert saved["resolution_evidence_refs"] == ["provider-trial-plan:x", "model-trial:y"]
