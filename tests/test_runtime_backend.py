from __future__ import annotations

import asyncio
import os

import pytest

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


def test_runtime_llm_aliases_never_reconstruct_legacy_api_providers():
    aliases = runtime_llm_aliases({
        "VIRTUALBRAIN_LLM_API_KEY": "legacy-deepseek-secret",
        "VIRTUALBRAIN_LLM_MODEL": "deepseek-flash",
        "OPENROUTER_API_KEY": "legacy-openrouter-secret",
        "NVIDIA_API_KEY": "legacy-nvidia-secret",
        "CEREBRAS_API_KEY": "legacy-cerebras-secret",
    })
    assert aliases == {}


def test_temporary_runtime_environment_does_not_create_provider_aliases(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "legacy-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_MODEL", "legacy-model")

    with _temporary_runtime_llm_environment():
        assert "DEEPSEEK_API_KEY" not in os.environ
        assert "DEEPSEEK_MODEL" not in os.environ

    assert "DEEPSEEK_API_KEY" not in os.environ
    assert "DEEPSEEK_MODEL" not in os.environ


def test_chatgpt_mode_flag_can_report_legacy_config_without_rebuilding_aliases():
    assert chatgpt_only_mode({
        "LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED": "0",
        "LIVINGRUNTIME_CHATGPT_ALLOW_FALLBACK": "1",
        "OPENROUTER_API_KEY": "present",
        "NVIDIA_API_KEY": "present",
        "CEREBRAS_API_KEY": "present",
        "DEEPSEEK_API_KEY": "present",
    }) is False
    assert runtime_llm_aliases({
        "DEEPSEEK_API_KEY": "present",
        "VIRTUALBRAIN_LLM_API_KEY": "present",
    }) == {}


def test_public_chat_uses_livingruntime_without_openai_api_key(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))

    backend = load_public_chat_backend()

    assert backend is not None
    assert backend.provider.provider_name == "livingruntime-chatgpt"
    assert backend.provider.available is True


def test_public_chat_ignores_openai_api_credentials(
    monkeypatch, tmp_path
):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-public-chat-must-ignore")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("FERRO_PUBLIC_CHAT_OPENAI_MODEL", "gpt-5.6-luna")

    backend = load_public_chat_backend()

    assert backend is not None
    assert backend.provider.provider_name == "livingruntime-chatgpt"
    assert backend.provider.available is True
    assert "sk-public-chat-must-ignore" not in repr(backend.provider)


def test_public_chat_is_unavailable_when_livingruntime_provider_is_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")
    monkeypatch.setenv(
        "LIVINGRUNTIME_CHATGPT_COGNITION_CLI",
        str(tmp_path / "missing-cognitionctl.py"),
    )

    assert load_public_chat_backend() is None


def test_owner_cognition_uses_livingruntime_when_openai_key_missing(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))

    runtime = load_owner_cognition_runtime(
        behavioral_context_provider=_behavioral_context,
    )

    assert runtime.provider is not None
    assert runtime.provider.available is True


def test_owner_cognition_ignores_openai_api_credentials(
    monkeypatch, tmp_path
):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-api-must-be-ignored")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    runtime = load_owner_cognition_runtime(
        behavioral_context_provider=_behavioral_context,
    )

    assert runtime.provider.provider_name == "livingruntime-chatgpt"
    assert runtime.provider.available is True
    assert "sk-api-must-be-ignored" not in repr(runtime.provider)


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
