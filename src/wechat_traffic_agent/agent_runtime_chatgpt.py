from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator

from agent_runtime.llm import ChatGPTBrowserProvider, ChatGPTBrowserProviderError


_ROUTING_TASK_CLASS: ContextVar[str] = ContextVar(
    "agent_runtime_chatgpt_routing_task_class",
    default="",
)


@contextmanager
def cognition_routing_task_class(task_class: str):
    normalized = str(task_class or "").strip()
    if not normalized:
        raise ValueError("cognition routing task class is required")
    token = _ROUTING_TASK_CLASS.set(normalized)
    try:
        yield
    finally:
        _ROUTING_TASK_CLASS.reset(token)


class AgentRuntimeChatGPTProviderError(RuntimeError):
    pass


class AgentRuntimeChatGPTProvider:
    """Ferro adapter over Agent Runtime's single ChatGPT browser provider."""

    provider_name = "agent-runtime-chatgpt-browser"
    model_name = "chatgpt-web"

    def __init__(
        self,
        *,
        provider: ChatGPTBrowserProvider,
        fallback: Any | None = None,
    ) -> None:
        if fallback is not None:
            raise ValueError("Ferro ChatGPT fallback providers are not supported")
        self._provider = provider
        self.timeout_seconds = float(provider.response_timeout_seconds)
        self.last_provenance: dict[str, Any] | None = None

    @classmethod
    def from_environment(
        cls,
        *,
        fallback: Any | None = None,
    ) -> "AgentRuntimeChatGPTProvider":
        if fallback is not None:
            raise ValueError("Ferro ChatGPT fallback providers are not supported")
        try:
            provider = ChatGPTBrowserProvider.from_environment()
        except (ChatGPTBrowserProviderError, OSError, ValueError) as exc:
            raise AgentRuntimeChatGPTProviderError(str(exc)) from exc
        return cls(provider=provider)

    @property
    def available(self) -> bool:
        readiness = self._provider.browser.readiness()
        return str(readiness.get("status") or "") == "READY"

    def _kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        result = dict(kwargs)
        task_class = _ROUTING_TASK_CLASS.get().strip()
        if task_class and not str(result.get("_routing_task_class") or "").strip():
            result["_routing_task_class"] = task_class
        return result

    def _record_provenance(self) -> None:
        metadata = dict(self._provider.last_metadata or {})
        self.last_provenance = {
            "provider": metadata.get("provider") or self.provider_name,
            "model": metadata.get("model") or self.model_name,
            "url": metadata.get("url"),
            "assistant_message_count": metadata.get("assistant_message_count"),
        }

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        try:
            text = await self._provider.complete(prompt, **self._kwargs(kwargs))
        except (ChatGPTBrowserProviderError, OSError, RuntimeError, ValueError) as exc:
            raise AgentRuntimeChatGPTProviderError(str(exc)) from exc
        self._record_provenance()
        return text

    async def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            value = await self._provider.structured(
                prompt,
                schema,
                **self._kwargs(kwargs),
            )
        except (ChatGPTBrowserProviderError, OSError, RuntimeError, ValueError) as exc:
            raise AgentRuntimeChatGPTProviderError(str(exc)) from exc
        self._record_provenance()
        return value

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        yield await self.complete(prompt, **kwargs)


__all__ = [
    "cognition_routing_task_class",
    "AgentRuntimeChatGPTProvider",
    "AgentRuntimeChatGPTProviderError",
]
