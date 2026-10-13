import asyncio

import pytest

from wechat_traffic_agent.agent_runtime_chatgpt import (
    AgentRuntimeChatGPTProvider, AgentRuntimeChatGPTProviderError, ApiModelCognitionProvider,
    cognition_provider_mode,
)


class _FakeApi:
    model = "glm-5.3-flash"
    timeout_seconds = 60.0

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def complete(self, prompt, **kwargs):
        self.calls.append(("complete", prompt, kwargs))
        if self.fail:
            raise RuntimeError("LLM provider HTTP error 429")
        return "ok"

    async def structured(self, prompt, schema, **kwargs):
        self.calls.append(("structured", prompt, kwargs))
        return {"action": "HOLD"}


def _api_env(monkeypatch, fake):
    monkeypatch.setenv("FERRO_COGNITION_PROVIDER", "api")
    from agent_runtime.llm import deepseek
    monkeypatch.setattr(deepseek.DeepSeekAnthropicProvider, "from_environment", classmethod(lambda cls: fake))


def test_default_mode_stays_chatgpt(monkeypatch):
    monkeypatch.delenv("FERRO_COGNITION_PROVIDER", raising=False)
    assert cognition_provider_mode() == "chatgpt"


def test_api_mode_replaces_every_chatgpt_construction(monkeypatch):
    fake = _FakeApi()
    _api_env(monkeypatch, fake)
    provider = AgentRuntimeChatGPTProvider.from_environment(fallback=None, session_id="ferro-owner-cognition")
    assert isinstance(provider, ApiModelCognitionProvider)
    assert provider.available is True
    assert provider.model_name == "glm-5.3-flash"


def test_api_provider_drops_browser_only_kwargs_and_sets_output_budget(monkeypatch):
    fake = _FakeApi()
    _api_env(monkeypatch, fake)
    provider = AgentRuntimeChatGPTProvider.from_environment()
    assert asyncio.run(provider.complete("p", _continue_cognition_episode=True,
                                         _routing_task_class="owner", system="s")) == "ok"
    assert asyncio.run(provider.structured("p", {"type": "object"})) == {"action": "HOLD"}
    kwargs = fake.calls[0][2]
    assert kwargs == {"system": "s", "max_tokens": 4096}
    assert provider.last_provenance["provider"] == "api-anthropic-compatible"


def test_api_errors_surface_as_cognition_provider_errors(monkeypatch):
    _api_env(monkeypatch, _FakeApi(fail=True))
    provider = AgentRuntimeChatGPTProvider.from_environment()
    with pytest.raises(AgentRuntimeChatGPTProviderError, match="429"):
        asyncio.run(provider.complete("p"))


def test_missing_api_credentials_fail_at_construction(monkeypatch):
    monkeypatch.setenv("FERRO_COGNITION_PROVIDER", "api")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(AgentRuntimeChatGPTProviderError, match="DEEPSEEK_API_KEY"):
        AgentRuntimeChatGPTProvider.from_environment()
