"""ChatGPT-only advisory backend wiring for the persistent Content Agent.

Ferro deliberately has one cognition provider: LivingRuntime ChatGPT. External
API model pools are not fallback capacity and must never be reconstructed from
legacy credentials or historical provider-discovery state.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import math
import os
from pathlib import Path
from typing import Iterator, Mapping


class ContentBoundedLegacyProviderBackend:
    """Add a bounded default duration to Content Agent advisory requests.

    Agent Runtime already propagates a request duration into per-provider attempt
    and reserve timeouts. Content cognition historically omitted that duration, so
    one unhealthy provider could hold an entire content cycle for 120-180 seconds.
    Explicit caller budgets always win; this class only fills a missing budget.
    """

    def __init__(self, backend, *, default_duration_seconds: float) -> None:
        duration = float(default_duration_seconds)
        if not math.isfinite(duration) or not 10.0 <= duration <= 900.0:
            raise ValueError("content advisory timeout must be within 10..900 seconds")
        self._backend = backend
        self.default_duration_seconds = duration

    @property
    def backend_id(self):
        return self._backend.backend_id

    @property
    def capabilities(self):
        return self._backend.capabilities

    @property
    def provider(self):
        return self._backend.provider

    async def run(self, request):
        if request.max_duration_seconds is None:
            request = replace(
                request, max_duration_seconds=self.default_duration_seconds
            )
        return await self._backend.run(request)


class OwnerOpenAICognitionRuntime:
    """Direct synchronous OpenAI cognition for durable owner-goal control."""

    def __init__(self, provider) -> None:
        self.provider = provider

    async def cognitive_probe(
        self,
        task: str,
        max_tokens: int,
        output_schema=None,
    ) -> str:
        del output_schema
        return await self.provider.complete(
            str(task),
            max_tokens=int(max_tokens),
            reasoning_effort="low",
        )


def _content_advisory_timeout_seconds(environ: Mapping[str, str] | None = None) -> float:
    env = os.environ if environ is None else environ
    raw = str(env.get("CONTENT_LLM_ADVISORY_TIMEOUT_SECONDS") or "45").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("CONTENT_LLM_ADVISORY_TIMEOUT_SECONDS must be numeric") from exc
    if not math.isfinite(value) or not 10.0 <= value <= 900.0:
        raise ValueError("CONTENT_LLM_ADVISORY_TIMEOUT_SECONDS must be within 10..900")
    return value


def runtime_llm_aliases(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Legacy API-provider aliases are intentionally disabled."""
    del environ
    return {}


@contextmanager
def _temporary_runtime_llm_environment(state_root: Path | None = None) -> Iterator[None]:
    inserted = runtime_llm_aliases()
    if state_root is not None:
        pool = Path(state_root) / "application" / "llm_resource_pool"
        paths = {
            "AGENT_LLM_RESOURCE_POOL_PATH": pool / "resources.json",
            "AGENT_LLM_ROUTING_POLICY_PATH": pool / "policy.json",
            "AGENT_LLM_ROUTING_STATE_DIR": pool / "runtime",
        }
        for key, path in paths.items():
            if not str(os.environ.get(key) or "").strip():
                inserted[key] = str(path)
    for key, value in inserted.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key in inserted:
            os.environ.pop(key, None)


def chatgpt_only_mode(environ: Mapping[str, str] | None = None) -> bool:
    """Report the configured ChatGPT-exclusive operating mode.

    Executable cognition remains ChatGPT-only regardless of this helper: the
    main/public backends and resource manifest no longer construct API-provider
    routes. This flag is retained for non-routing resource-economy simulations
    and historical tests.
    """
    env = os.environ if environ is None else environ
    raw_enabled = str(env.get("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED") or "").strip()
    if raw_enabled:
        enabled = raw_enabled.lower() in {"1", "true", "yes", "on"}
    else:
        enabled = (Path.home() / ".livingruntime" / "ferro-chatgpt-provider.enabled").is_file()
    raw_fallback = str(env.get("LIVINGRUNTIME_CHATGPT_ALLOW_FALLBACK") or "").strip()
    allow_fallback = raw_fallback.lower() in {"1", "true", "yes", "on"}
    return enabled and not allow_fallback


def load_optional_advisory_backend(*, state_root: Path | None = None):
    """Load Ferro's only advisory backend: LivingRuntime ChatGPT."""
    from agent_runtime.backends import LegacyProviderBackend
    from .livingruntime_chatgpt import LivingRuntimeChatGPTProvider

    provider = LivingRuntimeChatGPTProvider.from_environment(fallback=None)
    advisory_timeout = max(
        _content_advisory_timeout_seconds(),
        provider.timeout_seconds,
    )
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(provider, Path(state_root))
    return ContentBoundedLegacyProviderBackend(
        LegacyProviderBackend(provider),
        default_duration_seconds=advisory_timeout,
    )

def load_public_chat_backend(*, state_root: Path | None = None):
    """Load synchronous OpenAI for public HTTP chat only."""
    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    from agent_runtime.backends import LegacyProviderBackend
    from .openai_public_chat import OpenAIResponsesProvider

    model = "gpt-5.6-sol"
    base_url = str(
        os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).strip().rstrip("/")
    provider = OpenAIResponsesProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=18.0,
        reasoning_effort="none",
    )
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(
            provider,
            Path(state_root),
            scope="public-chat-openai",
        )
    return LegacyProviderBackend(provider)


def load_owner_cognition_runtime(*, state_root: Path | None = None):
    """Load the single authoritative owner-goal cognition route.

    Owner goals are persistent server-side commitments and cannot depend on a
    transient ChatGPT UI session being attached. There is no provider fallback:
    missing credentials or request failures are explicit errors.
    """
    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for owner cognition")

    from .openai_public_chat import OpenAIResponsesProvider

    model = "gpt-5.6-sol"
    base_url = str(
        os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).strip().rstrip("/")
    provider = OpenAIResponsesProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=30.0,
        reasoning_effort="low",
    )
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(
            provider,
            Path(state_root),
            scope="owner-cognition-openai",
        )
    return OwnerOpenAICognitionRuntime(provider)


__all__ = [
    "ContentBoundedLegacyProviderBackend",
    "_content_advisory_timeout_seconds",
    "load_optional_advisory_backend",
    "load_owner_cognition_runtime",
    "load_public_chat_backend",
    "OwnerOpenAICognitionRuntime",
    "runtime_llm_aliases",
    "chatgpt_only_mode",
]
