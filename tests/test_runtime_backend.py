import os

from wechat_traffic_agent.runtime_backend import (
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
