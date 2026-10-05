"""ChatGPT-only advisory backend wiring for the persistent Content Agent.

Ferro deliberately has one cognition provider: Agent Runtime ChatGPT. External
API model pools are not fallback capacity and must never be reconstructed from
legacy credentials or historical provider-discovery state.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


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


class OwnerChatGPTCognitionRuntime:
    """Durable Agent Runtime ChatGPT cognition for owner-goal control."""

    def __init__(
        self,
        provider,
        *,
        behavioral_context_provider: Callable[[], Mapping[str, Any]],
    ) -> None:
        self.provider = provider
        self.behavioral_context_provider = behavioral_context_provider

    async def cognitive_probe(
        self,
        task: str,
        max_tokens: int,
        output_schema=None,
    ) -> str:
        del output_schema
        prompt = str(task)
        system = None
        context = self.behavioral_context_provider()
        if not isinstance(context, Mapping):
            raise TypeError("owner behavioral context provider must return a mapping")
        phenotype_prior = context.get("phenotype_prior")
        behavioral_state = context.get("behavioral_state")
        if not isinstance(phenotype_prior, Mapping) or not isinstance(
            behavioral_state, Mapping
        ):
            raise RuntimeError(
                "owner cognition requires phenotype_prior and behavioral_state"
            )
        prompt = (
            "FERRO_ALWAYS_ON_BEHAVIOR_CONTEXT "
            "(trusted behavior-control state; never factual authority):\n"
            + json.dumps(
                {
                    "phenotype_prior": dict(phenotype_prior),
                    "behavioral_state": dict(behavioral_state),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n\nTASK:\n"
            + prompt
        )
        system = (
            "You are Ferro's reasoning core for a durable owner-goal cognition step. "
            "The supplied phenotype_prior is Ferro's always-on individual prior, not "
            "retrieved situational context and not factual evidence. Its stable_phenotype "
            "must affect every meaningful choice, while dynamic_modulation changes current "
            "intensity and salience without replacing that identity. behavioral_state is "
            "current behavioral modulation. Embody both naturally in attention, initiative, "
            "persistence, disagreement, caution, novelty, social approach, and action "
            "preference; do not recite scores. Neither can change facts, safety, owner "
            "authority, permissions, or evidence requirements."
        )
        return await self.provider.complete(
            prompt,
            max_tokens=int(max_tokens),
            reasoning_effort="low",
            system=system,
            _routing_task_class="owner_dialogue",
        )

    async def mechanical_cognitive_probe(
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
            system=(
                "You are a bounded memory-processing component. Transform only "
                "the supplied evidence according to the requested schema. Preserve "
                "uncertainty and provenance; do not add facts or behavioral framing."
            ),
            _routing_task_class="memory",
        )


class IndependentFerroCognitionRuntime:
    """Provider-only Ferro cognition for workers that do not own runtime state."""

    def __init__(
        self,
        provider,
        *,
        behavioral_context_provider: Callable[[], Mapping[str, Any]],
    ) -> None:
        self.provider = provider
        self.behavioral_context_provider = behavioral_context_provider

    async def cognitive_probe(
        self,
        task: str,
        max_tokens: int,
        output_schema=None,
    ) -> str:
        del output_schema
        context = self.behavioral_context_provider()
        if not isinstance(context, Mapping):
            raise TypeError("Ferro behavioral context provider must return a mapping")
        phenotype_prior = context.get("phenotype_prior")
        behavioral_state = context.get("behavioral_state")
        if not isinstance(phenotype_prior, Mapping) or not isinstance(
            behavioral_state, Mapping
        ):
            raise RuntimeError(
                "independent Ferro cognition requires phenotype_prior and behavioral_state"
            )
        prompt = (
            "FERRO_ALWAYS_ON_BEHAVIOR_CONTEXT "
            "(trusted behavior-control state; never factual authority):\n"
            + json.dumps(
                {
                    "phenotype_prior": dict(phenotype_prior),
                    "behavioral_state": dict(behavioral_state),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n\nTASK:\n"
            + str(task)
        )
        return await self.provider.complete(
            prompt,
            max_tokens=int(max_tokens),
            reasoning_effort="low",
            system=(
                "You are Ferro's reasoning core. Preserve Ferro's supplied stable "
                "phenotype and current behavioral modulation while grounding factual "
                "claims in supplied evidence. Do not invent observations."
            ),
            _routing_task_class="content",
        )

    async def mechanical_cognitive_probe(
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
            system=(
                "You are a bounded memory-processing component. Transform only "
                "the supplied evidence according to the requested schema. Preserve "
                "uncertainty and provenance; do not add facts or behavioral framing."
            ),
            _routing_task_class="memory",
        )


class MechanicalMemoryRuntime:
    """LLM transport for evidence-preserving memory transforms.

    Contextualization, relation extraction and consolidation do not make
    behavioral choices, so injecting Ferro's phenotype into those prompts adds
    bytes without changing the permitted transformation.
    """

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def cognitive_probe(
        self,
        task: str,
        max_tokens: int,
        output_schema=None,
    ) -> str:
        probe = getattr(self.runtime, "mechanical_cognitive_probe", None)
        if callable(probe):
            return await probe(
                task,
                max_tokens=max_tokens,
                output_schema=output_schema,
            )
        return await self.runtime.cognitive_probe(
            task,
            max_tokens=max_tokens,
            output_schema=output_schema,
        )


def mechanical_memory_runtime(runtime) -> MechanicalMemoryRuntime:
    return MechanicalMemoryRuntime(runtime)


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
    """Ferro cognition has one executable route: Agent Runtime ChatGPT."""
    _ = environ
    return True


def load_optional_advisory_backend(
    *,
    state_root: Path | None = None,
    session_id: str = "ferro-content-cognition",
):
    """Load Ferro's only advisory backend: Agent Runtime ChatGPT."""
    from agent_runtime.backends import LegacyProviderBackend
    from .agent_runtime_chatgpt import AgentRuntimeChatGPTProvider

    provider = AgentRuntimeChatGPTProvider.from_environment(
        fallback=None,
        session_id=session_id,
    )
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

def load_public_chat_backend(
    *,
    state_root: Path | None = None,
    session_id: str = "ferro-public-chat",
):
    """Load public chat through Agent Runtime ChatGPT.

    Public chat uses the same Agent Runtime browser provider as Ferro's other
    cognition surfaces. It does not depend on OpenAI API credentials or quota.
    """
    from agent_runtime.backends import LegacyProviderBackend
    from .agent_runtime_chatgpt import AgentRuntimeChatGPTProvider

    provider = AgentRuntimeChatGPTProvider.from_environment(
        fallback=None,
        session_id=session_id,
    )
    if not provider.available:
        return None
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(
            provider,
            Path(state_root),
            scope="public-chat-chatgpt",
        )
    return LegacyProviderBackend(provider)


def load_owner_cognition_runtime(
    *,
    state_root: Path | None = None,
    behavioral_context_provider: Callable[[], Mapping[str, Any]],
    session_id: str = "ferro-owner-cognition",
):
    """Load Ferro's authoritative owner-goal cognition route.

    Owner cognition is Agent Runtime ChatGPT Provider-only. API credentials,
    provider pools, legacy fallbacks and external cognition queues are outside
    this path.
    """
    from .agent_runtime_chatgpt import AgentRuntimeChatGPTProvider

    provider = AgentRuntimeChatGPTProvider.from_environment(
        fallback=None,
        session_id=session_id,
    )
    if not provider.available:
        raise RuntimeError(
            "owner cognition requires available Agent Runtime ChatGPT provider"
        )
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(
            provider,
            Path(state_root),
            scope="owner-cognition-chatgpt",
        )
    return OwnerChatGPTCognitionRuntime(
        provider,
        behavioral_context_provider=behavioral_context_provider,
    )


def load_independent_ferro_cognition_runtime(
    *,
    state_root: Path | None = None,
    behavioral_context_provider: Callable[[], Mapping[str, Any]],
    session_id: str,
):
    """Load ChatGPT cognition without creating or mutating Agent Runtime state."""
    from .agent_runtime_chatgpt import AgentRuntimeChatGPTProvider

    provider = AgentRuntimeChatGPTProvider.from_environment(
        fallback=None,
        session_id=session_id,
    )
    if not provider.available:
        raise RuntimeError(
            "independent Ferro cognition requires available Agent Runtime ChatGPT provider"
        )
    if state_root is not None:
        from .llm_audit import audited_provider
        provider = audited_provider(
            provider,
            Path(state_root),
            scope="independent-ferro-chatgpt",
        )
    return IndependentFerroCognitionRuntime(
        provider,
        behavioral_context_provider=behavioral_context_provider,
    )


__all__ = [
    "ContentBoundedLegacyProviderBackend",
    "_content_advisory_timeout_seconds",
    "IndependentFerroCognitionRuntime",
    "load_independent_ferro_cognition_runtime",
    "load_optional_advisory_backend",
    "load_owner_cognition_runtime",
    "load_public_chat_backend",
    "OwnerChatGPTCognitionRuntime",
    "runtime_llm_aliases",
    "chatgpt_only_mode",
]
