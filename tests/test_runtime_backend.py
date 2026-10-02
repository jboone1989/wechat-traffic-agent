from __future__ import annotations

import asyncio
import os

import pytest

import wechat_traffic_agent.agent_runtime_chatgpt as agent_chatgpt
from wechat_traffic_agent.runtime_backend import (
    OwnerChatGPTCognitionRuntime,
    _temporary_runtime_llm_environment,
    chatgpt_only_mode,
    load_owner_cognition_runtime,
    load_public_chat_backend,
    runtime_llm_aliases,
)


def _behavioral_context():
    return {
        "phenotype_prior": {
            "schema_version": "ferro-phenotype-prior.v1",
            "stable_phenotype": {
                "personality_traits": {"independence": 0.98}
            },
            "dynamic_modulation": {"affect": {"confidence": 0.86}},
        },
        "behavioral_state": {
            "personality_traits": {"independence": 0.98},
            "affect": {"confidence": 0.86},
        },
    }


class _FakeAgentRuntimeProvider:
    provider_name = "agent-runtime-chatgpt-browser"
    model_name = "chatgpt-web"
    timeout_seconds = 90.0

    def __init__(self, *, available: bool = True):
        self.available = available

    async def complete(self, prompt, **kwargs):
        return "ok"


def _install_provider(monkeypatch, provider):
    monkeypatch.setattr(
        agent_chatgpt.AgentRuntimeChatGPTProvider,
        "from_environment",
        classmethod(lambda cls, *, fallback=None: provider),
    )


def test_runtime_llm_aliases_never_reconstruct_legacy_api_providers():
    aliases = runtime_llm_aliases({
        "VIRTUALBRAIN_LLM_API_KEY": "legacy-deepseek-secret",
        "OPENROUTER_API_KEY": "legacy-openrouter-secret",
        "NVIDIA_API_KEY": "legacy-nvidia-secret",
        "CEREBRAS_API_KEY": "legacy-cerebras-secret",
    })
    assert aliases == {}


def test_temporary_runtime_environment_does_not_create_provider_aliases(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "legacy-secret")

    with _temporary_runtime_llm_environment():
        assert "DEEPSEEK_API_KEY" not in os.environ

    assert "DEEPSEEK_API_KEY" not in os.environ


def test_chatgpt_only_mode_is_not_configurable():
    assert chatgpt_only_mode({}) is True
    assert chatgpt_only_mode({
        "OPENROUTER_API_KEY": "present",
        "NVIDIA_API_KEY": "present",
        "CEREBRAS_API_KEY": "present",
        "DEEPSEEK_API_KEY": "present",
    }) is True


def test_public_chat_uses_agent_runtime_browser_and_ignores_openai_api(monkeypatch):
    provider = _FakeAgentRuntimeProvider()
    _install_provider(monkeypatch, provider)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-be-ignored")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    backend = load_public_chat_backend()

    assert backend is not None
    assert backend.provider is provider
    assert backend.provider.provider_name == "agent-runtime-chatgpt-browser"


def test_public_chat_is_unavailable_when_agent_runtime_browser_is_not_ready(monkeypatch):
    _install_provider(monkeypatch, _FakeAgentRuntimeProvider(available=False))
    assert load_public_chat_backend() is None


def test_owner_cognition_uses_agent_runtime_browser_and_ignores_openai_api(monkeypatch):
    provider = _FakeAgentRuntimeProvider()
    _install_provider(monkeypatch, provider)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-be-ignored")

    runtime = load_owner_cognition_runtime(
        behavioral_context_provider=_behavioral_context,
    )

    assert runtime.provider is provider
    assert runtime.provider.provider_name == "agent-runtime-chatgpt-browser"


def test_owner_chatgpt_cognition_carries_always_on_phenotype():
    class Provider:
        def __init__(self):
            self.prompt = None
            self.kwargs = None

        async def complete(self, prompt, **kwargs):
            self.prompt = prompt
            self.kwargs = kwargs
            return '{"action":"WAIT"}'

    provider = Provider()
    runtime = OwnerChatGPTCognitionRuntime(
        provider,
        behavioral_context_provider=lambda: {
            "phenotype_prior": {
                "schema_version": "ferro-phenotype-prior.v1",
                "stable_phenotype": {
                    "personality_traits": {
                        "independence": 0.98,
                        "persistence": 0.74,
                    }
                },
                "dynamic_modulation": {
                    "affect": {"boredom": 0.21, "confidence": 0.86}
                },
            },
            "behavioral_state": {
                "personality_traits": {"independence": 0.98},
                "affect": {"boredom": 0.21, "confidence": 0.86},
            },
        },
    )

    result = asyncio.run(
        runtime.cognitive_probe("OWNER_AGENT_EPISODE: choose the next action", 400)
    )

    assert result == '{"action":"WAIT"}'
    assert "FERRO_ALWAYS_ON_BEHAVIOR_CONTEXT" in provider.prompt
    assert '"phenotype_prior"' in provider.prompt
    assert '"independence":0.98' in provider.prompt
    assert "OWNER_AGENT_EPISODE" in provider.prompt
    assert "always-on individual prior" in provider.kwargs["system"]
    assert "must affect every meaningful choice" in provider.kwargs["system"]
    assert provider.kwargs["reasoning_effort"] == "low"
    assert provider.kwargs["_routing_task_class"] == "owner_dialogue"


def test_owner_chatgpt_cognition_refuses_to_drop_phenotype_silently():
    class Provider:
        async def complete(self, prompt, **kwargs):
            raise AssertionError("provider must not be called without phenotype")

    runtime = OwnerChatGPTCognitionRuntime(
        Provider(),
        behavioral_context_provider=lambda: {"behavioral_state": {}},
    )

    with pytest.raises(RuntimeError, match="requires phenotype_prior"):
        asyncio.run(runtime.cognitive_probe("OWNER_AGENT_EPISODE: choose", 200))
