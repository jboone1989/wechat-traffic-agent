"""Advisory backend wiring for the persistent Content Agent.

The deployed Content Agent already shares LLM credentials with VirtualBrain.
Agent Runtime currently names the same DeepSeek credential/model variables
differently, so this adapter provides a process-local compatibility seam
without persisting or logging secret values.
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
    """Return missing Agent Runtime variables derivable from existing VB config."""
    env = os.environ if environ is None else environ
    aliases: dict[str, str] = {}
    pairs = (
        ("DEEPSEEK_API_KEY", "VIRTUALBRAIN_LLM_API_KEY"),
        ("DEEPSEEK_MODEL", "VIRTUALBRAIN_LLM_MODEL"),
    )
    for target, source in pairs:
        if str(env.get(target) or "").strip():
            continue
        value = str(env.get(source) or "").strip()
        if value:
            aliases[target] = value
    return aliases


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
    """True when LivingRuntime ChatGPT is enabled and API/free-model fallback is forbidden."""
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
    """Load Ferro's advisory backend.

    ChatGPT-only mode is fail-closed: API/free-model providers are not even
    constructed. If ChatGPT is temporarily unavailable, the cognition request
    fails boundedly and can be retried without silently changing the model.
    """
    from agent_runtime.backends import LegacyProviderBackend
    from agent_runtime.llm import LLMProviderError, load_llm_provider
    from .livingruntime_chatgpt import LivingRuntimeChatGPTProvider

    if chatgpt_only_mode():
        provider = LivingRuntimeChatGPTProvider.from_environment(fallback=None)
    else:
        provider = None
        with _temporary_runtime_llm_environment(state_root):
            try:
                provider = load_llm_provider()
            except LLMProviderError as exc:
                if "not configured" not in str(exc).lower():
                    raise
        chatgpt_provider = LivingRuntimeChatGPTProvider.from_environment(
            fallback=provider
        )
        if chatgpt_provider.enabled:
            provider = chatgpt_provider
        elif provider is None:
            return None

    advisory_timeout = _content_advisory_timeout_seconds()
    if isinstance(provider, LivingRuntimeChatGPTProvider):
        advisory_timeout = max(
            advisory_timeout,
            provider.timeout_seconds,
        )
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(provider, Path(state_root))
    legacy = LegacyProviderBackend(provider)
    return ContentBoundedLegacyProviderBackend(
        legacy,
        default_duration_seconds=advisory_timeout,
    )


def load_public_chat_backend(*, state_root: Path | None = None):
    """Load an always-on synchronous backend for the public web conversation.

    Ferro's internal/owner cognition may intentionally be CHATGPT_ONLY and
    durably suspend until a ChatGPT session claims the request. Public HTTP chat
    cannot use that suspension protocol: a visitor expects an immediate request/
    response. Keep the authority surfaces separate and route only public chat
    through the already-provisioned API resource pool.
    """
    if state_root is None:
        return None
    from agent_runtime.backends import LegacyProviderBackend
    from agent_runtime.llm import ResourcePoolProvider, load_resource_pool_manifest
    from .llm_audit import audited_provider

    root = Path(state_root)
    manifest_path = root / "application" / "llm_resource_pool" / "resources.json"
    if not manifest_path.exists():
        return None
    try:
        resources = load_resource_pool_manifest(manifest_path)
    except (OSError, ValueError, TypeError):
        return None
    if not resources:
        return None
    # Do not inherit the CHATGPT_ONLY internal-cognition policy. Public chat has
    # its own bounded resource health/decision state and uses the pool defaults:
    # synchronous providers, quality floor, provider diversity and failover.
    provider = ResourcePoolProvider(
        resources,
        policy_path=None,
        state_dir=manifest_path.parent / "public-chat-runtime",
        manifest_path=manifest_path,
    )
    provider = audited_provider(provider, root)
    return ContentBoundedLegacyProviderBackend(
        LegacyProviderBackend(provider),
        default_duration_seconds=_content_advisory_timeout_seconds(),
    )


__all__ = [
    "ContentBoundedLegacyProviderBackend",
    "_content_advisory_timeout_seconds",
    "load_optional_advisory_backend",
    "load_public_chat_backend",
    "runtime_llm_aliases",
    "chatgpt_only_mode",
]
