from datetime import datetime
import os
from pathlib import Path

from agent_runtime.llm.resource_pool import LLMResourceSpec

from wechat_traffic_agent.runtime_backend import (
    _public_chat_resource_pool,
    _temporary_runtime_llm_environment,
    runtime_llm_aliases,
)


def test_runtime_llm_aliases_reuse_existing_virtualbrain_config():
    aliases = runtime_llm_aliases({
        "VIRTUALBRAIN_LLM_API_KEY": "shared-secret",
        "VIRTUALBRAIN_LLM_MODEL": "deepseek-flash",
    })
    assert aliases == {
        "DEEPSEEK_API_KEY": "shared-secret",
        "DEEPSEEK_MODEL": "deepseek-flash",
    }


def test_runtime_llm_aliases_never_override_native_runtime_config():
    aliases = runtime_llm_aliases({
        "DEEPSEEK_API_KEY": "runtime-secret",
        "DEEPSEEK_MODEL": "runtime-model",
        "VIRTUALBRAIN_LLM_API_KEY": "shared-secret",
        "VIRTUALBRAIN_LLM_MODEL": "shared-model",
    })
    assert aliases == {}


def test_runtime_llm_aliases_are_process_local(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "shared-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_MODEL", "deepseek-flash")

    with _temporary_runtime_llm_environment():
        assert os.environ["DEEPSEEK_API_KEY"] == "shared-secret"
        assert os.environ["DEEPSEEK_MODEL"] == "deepseek-flash"

    assert "DEEPSEEK_API_KEY" not in os.environ
    assert "DEEPSEEK_MODEL" not in os.environ


def _public_resource(resource_id: str = "free") -> LLMResourceSpec:
    return LLMResourceSpec(
        resource_id=resource_id,
        provider_id="test-provider",
        provider_label="Test Provider",
        model="test-model",
        credential_env="TEST_PUBLIC_LLM_KEY",
        protocol="openai_chat",
        base_url="https://example.invalid/v1",
        lifecycle="RECURRING_FREE",
        pricing_class="FREE",
        quality_score=1.0,
        success_rate=1.0,
        task_classes=("general_text", "simple_public_text"),
    )


def test_public_chat_transient_failure_uses_short_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PUBLIC_LLM_KEY", "present")
    manifest = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"resources":[]}', encoding="utf-8")
    spec = _public_resource()
    provider = _public_chat_resource_pool([spec], manifest_path=manifest)

    provider._record_failure(spec, "REQUEST_FAILED")

    health = provider._health[spec.resource_id]
    failed = datetime.fromisoformat(health.last_failure_at)
    cooldown = datetime.fromisoformat(health.cooldown_until)
    assert 0 < (cooldown - failed).total_seconds() <= 21


def test_public_chat_hard_failure_keeps_long_runtime_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PUBLIC_LLM_KEY", "present")
    manifest = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"resources":[]}', encoding="utf-8")
    spec = _public_resource()
    provider = _public_chat_resource_pool([spec], manifest_path=manifest)

    provider._record_failure(spec, "HTTP_410")

    health = provider._health[spec.resource_id]
    failed = datetime.fromisoformat(health.last_failure_at)
    cooldown = datetime.fromisoformat(health.cooldown_until)
    assert (cooldown - failed).total_seconds() >= 5 * 3600
