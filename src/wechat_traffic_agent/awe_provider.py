"""Evaluation-only fixed provider construction for AWE.

AWE comparisons must keep the underlying model route fixed across contestants.
This module bypasses production resource-pool switching while preserving bounded,
transparent retries for provider errors that explicitly declare themselves retryable.
No credential value is persisted or logged.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


class RetryingEvaluationProvider:
    def __init__(
        self,
        provider: Any,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        self.provider = provider
        self.max_retries = max(0, min(int(max_retries), 4))
        self.backoff_seconds = max(0.0, min(float(backoff_seconds), 5.0))

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        last: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self.provider.complete(prompt, **kwargs)
            except Exception as exc:
                last = exc
                if not bool(getattr(exc, "retryable", False)):
                    raise
                if attempt >= self.max_retries:
                    raise
                if self.backoff_seconds:
                    await asyncio.sleep(self.backoff_seconds * (2 ** attempt))
        assert last is not None
        raise last


def build_fixed_openai_provider(
    *,
    credential_env: str,
    base_url: str,
    model: str,
    max_retries: int = 2,
    timeout_seconds: float = 45.0,
) -> RetryingEvaluationProvider:
    from agent_runtime.llm.openai_compatible import OpenAICompatibleChatProvider

    env_name = str(credential_env or "").strip()
    if not env_name:
        raise ValueError("fixed AWE provider requires credential env name")
    api_key = str(os.environ.get(env_name) or "").strip()
    if not api_key:
        raise RuntimeError(f"fixed AWE provider credential unavailable: {env_name}")
    url = str(base_url or "").strip()
    model_id = str(model or "").strip()
    if not url or not model_id:
        raise ValueError("fixed AWE provider requires base URL and model")

    extra_headers = (
        {
            "Accept": "application/json",
            "User-Agent": "LivingRuntime-AWE/1.0",
        }
        if env_name == "CEREBRAS_API_KEY"
        else {}
    )

    return RetryingEvaluationProvider(
        OpenAICompatibleChatProvider(
            api_key=api_key,
            model=model_id,
            base_url=url,
            timeout_seconds=max(1.0, float(timeout_seconds)),
            extra_headers=extra_headers,
            include_usage=False,
        ),
        max_retries=max_retries,
    )


__all__ = ["RetryingEvaluationProvider", "build_fixed_openai_provider"]
