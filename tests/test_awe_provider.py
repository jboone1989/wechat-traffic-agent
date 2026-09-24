from __future__ import annotations

import asyncio

import pytest

from wechat_traffic_agent.awe_provider import RetryingEvaluationProvider


class RetryableError(RuntimeError):
    retryable = True


class FatalError(RuntimeError):
    retryable = False


class FlakyProvider:
    def __init__(self, failures, exc_type=RetryableError):
        self.failures = failures
        self.exc_type = exc_type
        self.calls = 0

    async def complete(self, prompt, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc_type("transient")
        return "ok"


def test_retries_only_before_success_for_retryable_provider_error():
    inner = FlakyProvider(2)
    provider = RetryingEvaluationProvider(inner, max_retries=2, backoff_seconds=0)
    assert asyncio.run(provider.complete("x")) == "ok"
    assert inner.calls == 3


def test_nonretryable_error_is_not_retried():
    inner = FlakyProvider(1, FatalError)
    provider = RetryingEvaluationProvider(inner, max_retries=4, backoff_seconds=0)
    with pytest.raises(FatalError):
        asyncio.run(provider.complete("x"))
    assert inner.calls == 1


def test_retry_budget_is_bounded():
    inner = FlakyProvider(10)
    provider = RetryingEvaluationProvider(inner, max_retries=2, backoff_seconds=0)
    with pytest.raises(RetryableError):
        asyncio.run(provider.complete("x"))
    assert inner.calls == 3


def test_cerebras_fixed_provider_adds_edge_compatible_headers(monkeypatch):
    from wechat_traffic_agent.awe_provider import build_fixed_openai_provider
    import agent_runtime.llm.openai_compatible as module

    captured = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("CEREBRAS_API_KEY", "secret-for-test")
    monkeypatch.setattr(module, "OpenAICompatibleChatProvider", FakeProvider)
    provider = build_fixed_openai_provider(
        credential_env="CEREBRAS_API_KEY",
        base_url="https://api.cerebras.ai/v1",
        model="gpt-oss-120b",
    )
    assert provider.provider is not None
    assert captured["extra_headers"] == {
        "Accept": "application/json",
        "User-Agent": "LivingRuntime-AWE/1.0",
    }


def test_non_cerebras_fixed_provider_does_not_add_edge_headers(monkeypatch):
    from wechat_traffic_agent.awe_provider import build_fixed_openai_provider
    import agent_runtime.llm.openai_compatible as module

    captured = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("NVIDIA_API_KEY", "secret-for-test")
    monkeypatch.setattr(module, "OpenAICompatibleChatProvider", FakeProvider)
    build_fixed_openai_provider(
        credential_env="NVIDIA_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
        model="mistralai/mistral-nemotron",
    )
    assert captured["extra_headers"] == {}
