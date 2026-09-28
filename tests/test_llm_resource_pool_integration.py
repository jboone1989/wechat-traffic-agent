from __future__ import annotations

from datetime import datetime, timezone
import json
import os

import pytest

from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.llm_resources import pool_metrics, sync_llm_resource_pool
from wechat_traffic_agent.resource_economy import ContentResourceEconomy
from wechat_traffic_agent.runtime_backend import (
    _temporary_runtime_llm_environment,
    load_optional_advisory_backend,
)


NOW = datetime(2026, 9, 18, 3, 30, tzinfo=timezone.utc)


def test_sync_replaces_legacy_api_provider_pool_with_empty_chatgpt_only_manifest(
    tmp_path, monkeypatch
):
    for name in (
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "CEREBRAS_API_KEY",
        "DEEPSEEK_API_KEY",
        "VIRTUALBRAIN_LLM_API_KEY",
    ):
        monkeypatch.setenv(name, "legacy-secret")
    manifest_path = (
        tmp_path / "application" / "llm_resource_pool" / "resources.json"
    )
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "resources": [
            {"provider_id": "nvidia"},
            {"provider_id": "cerebras"},
            {"provider_id": "openrouter"},
            {"provider_id": "deepseek"},
        ]
    }), encoding="utf-8")

    result = sync_llm_resource_pool(state_root=tmp_path, now=NOW)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result["status"] == "CHATGPT_ONLY"
    assert result["resource_count"] == 0
    assert result["resource_ids"] == []
    assert payload["resources"] == []
    assert payload["provider_mode"] == "LIVINGRUNTIME_CHATGPT_ONLY"
    assert set(payload["disabled_provider_ids"]) == {
        "nvidia", "cerebras", "openrouter", "deepseek"
    }
    raw = manifest_path.read_text(encoding="utf-8")
    assert "legacy-secret" not in raw


def test_runtime_backend_uses_chatgpt_without_api_fallback(tmp_path, monkeypatch):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("print('unused')\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy-openrouter")
    monkeypatch.setenv("NVIDIA_API_KEY", "legacy-nvidia")
    monkeypatch.setenv("CEREBRAS_API_KEY", "legacy-cerebras")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-deepseek")

    backend = load_optional_advisory_backend(state_root=tmp_path)

    assert backend.provider.provider_name == "livingruntime-chatgpt"
    assert backend.provider.fallback is None


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


def test_chatgpt_only_resource_observation_never_calls_deepseek_balance(
    tmp_path, monkeypatch
):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("print('unused')\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-used")

    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)

    def forbidden(_now):
        raise AssertionError("DeepSeek balance observer must not run")

    monkeypatch.setattr(economy.observer, "observe", forbidden)
    result = economy.observe(NOW)
    policy = json.loads(
        (tmp_path / "application" / "llm_resource_pool" / "policy.json").read_text()
    )

    assert result["status"] == "CHATGPT_ONLY"
    assert result["balance_sensor"] == "NOT_APPLICABLE"
    assert result["llm_routing_policy"]["routing_mode"] == "CHATGPT_ONLY"
    assert result["llm_routing_policy"]["max_failovers"] == 0
    assert policy["preferred_lifecycles"] == []
    assert policy["chatgpt_available"] is True


def test_pool_metrics_report_no_active_api_routes_after_sync(tmp_path):
    sync_llm_resource_pool(state_root=tmp_path, now=NOW)
    metrics = pool_metrics(tmp_path)
    assert metrics["llm_pool_resource_count"] == 0.0
    assert metrics["llm_pool_free_resource_count"] == 0.0
    assert metrics["llm_pool_paid_resource_count"] == 0.0
    assert metrics["llm_pool_windfall_resource_count"] == 0.0


def test_content_backend_adds_bounded_default_duration_without_overriding_explicit_budget():
    import asyncio
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
    asyncio.run(backend.run(
        AgentRequest(
            mode=AgentRunMode.ADVISORY,
            task="bounded content cognition",
            context={},
        )
    ))
    asyncio.run(backend.run(
        AgentRequest(
            mode=AgentRunMode.ADVISORY,
            task="explicit owner/live budget",
            context={},
            max_duration_seconds=20,
        )
    ))

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
