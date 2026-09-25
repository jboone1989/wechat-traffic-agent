from __future__ import annotations

from datetime import datetime, timezone
import json
import os

import pytest

from agent_runtime.llm.resource_pool import ResourcePoolProvider
from agent_runtime.resource_discovery import (
    ModelTrial,
    ProviderLead,
    ProviderVerification,
    ResourceScout,
)
from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.llm_resources import pool_metrics, sync_llm_resource_pool
from wechat_traffic_agent.resource_economy import (
    BalanceObservation,
    ContentResourceEconomy,
)
from wechat_traffic_agent.runtime_backend import (
    _temporary_runtime_llm_environment,
    load_optional_advisory_backend,
)


NOW = datetime(2026, 9, 18, 3, 30, tzinfo=timezone.utc)


def _seed_openrouter(state_root):
    scout = ResourceScout(state_root / "application", lambda *a, **k: {"results": []})
    lead = ProviderLead(
        "lead:o",
        "op:o",
        "OpenRouter",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/keys",
        "openrouter.ai",
        NOW.isoformat(),
        "receipt:o",
    )
    verification = ProviderVerification(
        "lead:o",
        "VERIFIED_RECURRING_FREE",
        NOW.isoformat(),
        free_status="RECURRING_FREE",
        rate_limit_evidence=True,
    )
    scout._write_provider_lead(lead)
    scout._write_provider_verification(verification)
    for i, (status, quality, error) in enumerate((
        ("COMPLETED", 1.0, ""),
        ("COMPLETED", 0.5, ""),
        ("FAILED", None, "RuntimeError"),
    )):
        scout.record_model_trial(ModelTrial(
            trial_id=f"trial:o:{i}",
            opportunity_id="lead:o",
            task_class="simple_public_text",
            status=status,
            quality_score=quality,
            latency_ms=1000.0 + i,
            observed_cost=0.0 if status == "COMPLETED" else None,
            evidence_refs=(f"receipt:o:{i}",),
            provider_lead_id="lead:o",
            provider_label="OpenRouter",
            model_id="openrouter/free",
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code=error,
        ))
    return scout


def test_sync_builds_free_plus_stable_reserve_without_persisting_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "paid-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_MODEL", "deepseek-flash")
    _seed_openrouter(tmp_path)

    result = sync_llm_resource_pool(state_root=tmp_path, now=NOW)
    manifest_path = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    payload = json.loads(manifest_path.read_text())
    resources = payload["resources"]

    assert result["free_count"] == 1
    assert result["paid_count"] == 1
    free = next(row for row in resources if row["pricing_class"] == "FREE")
    reserve = next(row for row in resources if row["pricing_class"] == "PAID")
    assert free["provider_id"] == "openrouter"
    assert free["model"] == "openrouter/free"
    assert free["quality_score"] == 0.75
    assert free["success_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert reserve["provider_id"] == "deepseek"
    assert reserve["reserve"] is True
    raw = manifest_path.read_text()
    assert "free-secret" not in raw
    assert "paid-secret" not in raw


def test_sync_carries_operator_quota_and_expiry_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY_QUOTA_REMAINING", "123")
    monkeypatch.setenv("OPENROUTER_API_KEY_QUOTA_LIMIT", "500")
    monkeypatch.setenv("OPENROUTER_API_KEY_RESET_AT", "2026-09-19T00:00:00+00:00")
    monkeypatch.setenv("OPENROUTER_API_KEY_EXPIRES_AT", "2026-10-01T00:00:00+00:00")
    _seed_openrouter(tmp_path)

    sync_llm_resource_pool(state_root=tmp_path, now=NOW)
    payload = json.loads(
        (tmp_path / "application" / "llm_resource_pool" / "resources.json").read_text()
    )
    free = next(row for row in payload["resources"] if row["pricing_class"] == "FREE")
    assert free["quota_remaining"] == 123.0
    assert free["quota_limit"] == 500.0
    assert free["quota_reset_at"] == "2026-09-19T00:00:00+00:00"
    assert free["expires_at"] == "2026-10-01T00:00:00+00:00"


def test_sync_suppresses_runtime_invalid_model_until_newer_success_evidence(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-secret")
    scout = _seed_openrouter(tmp_path)

    initial = sync_llm_resource_pool(state_root=tmp_path, now=NOW)
    assert initial["free_count"] == 1
    manifest_path = (
        tmp_path / "application" / "llm_resource_pool" / "resources.json"
    )
    resource_id = next(
        row["resource_id"]
        for row in json.loads(manifest_path.read_text())["resources"]
        if row["pricing_class"] == "FREE"
    )

    runtime_dir = (
        tmp_path / "application" / "llm_resource_pool" / "runtime"
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_failure_at = NOW.replace(minute=40)
    (runtime_dir / "health.json").write_text(
        json.dumps({
            "resources": {
                resource_id: {
                    "successes": 20,
                    "failures": 1,
                    "consecutive_failures": 1,
                    "last_error_code": "HTTP_404",
                    "last_failure_at": runtime_failure_at.isoformat(),
                }
            }
        }),
        encoding="utf-8",
    )

    suppressed = sync_llm_resource_pool(
        state_root=tmp_path, now=NOW.replace(minute=45)
    )
    assert suppressed["free_count"] == 0
    assert suppressed["suppressed_runtime_invalid_count"] == 1
    assert suppressed["suppressed_runtime_invalid_resource_ids"] == [resource_id]

    recovered_at = NOW.replace(minute=50)
    scout.record_model_trial(ModelTrial(
        trial_id="trial:o:recovered",
        opportunity_id="lead:o",
        task_class="simple_public_text",
        status="COMPLETED",
        quality_score=1.0,
        latency_ms=800.0,
        observed_cost=0.0,
        evidence_refs=("receipt:o:recovered",),
        provider_lead_id="lead:o",
        provider_label="OpenRouter",
        model_id="openrouter/free",
        started_at=recovered_at.isoformat(),
        completed_at=recovered_at.isoformat(),
        error_code="",
    ))

    restored = sync_llm_resource_pool(
        state_root=tmp_path, now=NOW.replace(minute=55)
    )
    assert restored["free_count"] == 1
    assert restored["suppressed_runtime_invalid_count"] == 0


def test_runtime_backend_loads_resource_pool_from_content_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "paid-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_MODEL", "deepseek-flash")
    _seed_openrouter(tmp_path)
    sync_llm_resource_pool(state_root=tmp_path, now=NOW)

    backend = load_optional_advisory_backend(state_root=tmp_path)

    assert isinstance(backend.provider, ResourcePoolProvider)
    assert {row.pricing_class for row in backend.provider.resources} == {"FREE", "PAID"}
    assert "AGENT_LLM_RESOURCE_POOL_PATH" not in os.environ


def test_runtime_pool_paths_are_process_local(tmp_path, monkeypatch):
    for name in (
        "AGENT_LLM_RESOURCE_POOL_PATH",
        "AGENT_LLM_ROUTING_POLICY_PATH",
        "AGENT_LLM_ROUTING_STATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    with _temporary_runtime_llm_environment(tmp_path):
        pool = tmp_path / "application" / "llm_resource_pool"
        assert os.environ["AGENT_LLM_RESOURCE_POOL_PATH"] == str(pool / "resources.json")
        assert os.environ["AGENT_LLM_ROUTING_POLICY_PATH"] == str(pool / "policy.json")
        assert os.environ["AGENT_LLM_ROUTING_STATE_DIR"] == str(pool / "runtime")

    assert "AGENT_LLM_RESOURCE_POOL_PATH" not in os.environ
    assert "AGENT_LLM_ROUTING_POLICY_PATH" not in os.environ
    assert "AGENT_LLM_ROUTING_STATE_DIR" not in os.environ


def test_virtualbrain_resource_pressure_persists_routing_policy(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(
        economy.observer,
        "observe",
        lambda now: BalanceObservation(
            observed_at=now.isoformat(),
            is_available=True,
            currency="CNY",
            total_balance=20.0,
            granted_balance=0.0,
            topped_up_balance=20.0,
            evidence_ref="deepseek-balance:sha256:" + "a" * 64,
        ),
    )

    result = economy.observe(NOW)
    policy_path = tmp_path / "application" / "llm_resource_pool" / "policy.json"
    policy = json.loads(policy_path.read_text())

    assert result["llm_routing_policy"]["pressure"] == "CONSERVE"
    assert policy["routing_mode"] == "FREE_FIRST_RESERVE_PAID"
    assert policy["prefer_free"] is True
    assert policy["preferred_lifecycles"] == [
        "TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID"
    ]
    assert policy["max_failovers"] == 2
    assert policy["mind_tick"] == mind.session.tick
    assert policy["paid_available_funds"] == 20.0
    assert policy["paid_reserve_floor"] == 20.0
    assert policy["evidence_refs"]


def test_pool_metrics_feed_route_outcomes_back_to_vb_observation(tmp_path):
    pool = tmp_path / "application" / "llm_resource_pool"
    runtime = pool / "runtime" / "decisions"
    runtime.mkdir(parents=True)
    (pool / "resources.json").write_text(json.dumps({
        "resources": [
            {"resource_id": "free", "pricing_class": "FREE"},
            {"resource_id": "paid", "pricing_class": "PAID"},
        ]
    }))
    (runtime / "1.json").write_text(json.dumps({
        "selected_resource_id": "free",
        "attempted_resource_ids": ["free"],
        "result": "COMPLETED",
    }))
    (runtime / "2.json").write_text(json.dumps({
        "selected_resource_id": "paid",
        "attempted_resource_ids": ["free", "paid"],
        "result": "COMPLETED",
    }))

    metrics = pool_metrics(tmp_path)
    assert metrics["llm_pool_free_resource_count"] == 1.0
    assert metrics["llm_pool_paid_resource_count"] == 1.0
    assert metrics["llm_pool_recent_free_selections"] == 1.0
    assert metrics["llm_pool_recent_paid_selections"] == 1.0
    assert metrics["llm_pool_recent_failovers"] == 1.0


@pytest.mark.asyncio
async def test_content_backend_adds_bounded_default_duration_without_overriding_explicit_budget():
    from agent_runtime.backends import AgentRequest, AgentResult, AgentRunMode
    from wechat_traffic_agent.runtime_backend import ContentBoundedLegacyProviderBackend

    class Inner:
        backend_id = "inner"
        capabilities = object()
        provider = object()

        def __init__(self):
            self.requests = []

        async def run(self, request):
            self.requests.append(request)
            return AgentResult(final_response="ok")

    inner = Inner()
    backend = ContentBoundedLegacyProviderBackend(
        inner, default_duration_seconds=45
    )
    await backend.run(
        AgentRequest(
            mode=AgentRunMode.ADVISORY,
            task="bounded content cognition",
            context={},
        )
    )
    await backend.run(
        AgentRequest(
            mode=AgentRunMode.ADVISORY,
            task="explicit owner/live budget",
            context={},
            max_duration_seconds=20,
        )
    )

    assert inner.requests[0].max_duration_seconds == 45
    assert inner.requests[1].max_duration_seconds == 20


def test_content_advisory_timeout_env_is_bounded(monkeypatch):
    from wechat_traffic_agent.runtime_backend import _content_advisory_timeout_seconds

    monkeypatch.delenv("CONTENT_LLM_ADVISORY_TIMEOUT_SECONDS", raising=False)
    assert _content_advisory_timeout_seconds() == 45.0
    monkeypatch.setenv("CONTENT_LLM_ADVISORY_TIMEOUT_SECONDS", "30")
    assert _content_advisory_timeout_seconds() == 30.0
    monkeypatch.setenv("CONTENT_LLM_ADVISORY_TIMEOUT_SECONDS", "5")
    with pytest.raises(ValueError, match="10..900"):
        _content_advisory_timeout_seconds()
