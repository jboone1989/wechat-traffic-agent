from __future__ import annotations

import os

from wechat_traffic_agent.runtime_backend import (
    _temporary_runtime_llm_environment,
    chatgpt_only_mode,
    load_public_chat_backend,
    runtime_llm_aliases,
)


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


def test_public_chat_has_no_separate_api_provider_backend(tmp_path):
    assert load_public_chat_backend(state_root=tmp_path) is None
