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

from .behavioral_state import (
    compact_behavioral_state_for_prompt,
    compact_phenotype_prior_for_prompt,
)

_WORKSPACE_DECISION_TYPES = frozenset({
    "OWNER_GOAL_ACTION",
    "PLANNING",
    "SELECTION",
    "VERIFICATION",
    "PREDICTION",
    "LEARNING_UPDATE",
    "RESEARCH",
    "REPAIR",
})
_WORKSPACE_PREFIXES = (
    "OWNER_AGENT_EPISODE",
    "X_SEARCH_PLAN",
    "REFLECT",
    "RESEARCH",
    "LEARN",
    "PLAN",
    "PREDICT",
    "VERIFY",
    "CHECK_",
    "SELECT_EVIDENCE",
    "MISSION_DISCOVERY",
    "WORLDVIEW",
)
_WORKSPACE_QUERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["memory_queries"],
    "properties": {
        "memory_queries": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "minLength": 3, "maxLength": 320},
        }
    },
}


def _workspace_task_seed(task: str, *, max_chars: int = 6000) -> str:
    value = str(task or "").strip()
    if len(value) <= max_chars:
        return value
    half = max_chars // 2
    return value[:half] + "\n...[middle omitted]...\n" + value[-half:]


def _compact_workspace_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, Mapping):
        return {
            str(key): _compact_workspace_value(item, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    if isinstance(value, (list, tuple)):
        return [
            _compact_workspace_value(item, depth=depth + 1)
            for item in list(value)[:8]
        ]
    if isinstance(value, str):
        return value if len(value) <= 900 else value[:886] + "...[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:900]


def _workspace_seed_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        return {}
    keys = (
        "current_intent",
        "open_loops",
        "missions",
        "commitments",
        "mind",
        "recent_experience",
        "retrieved_memory",
        "resources",
        "capabilities",
        "interoception",
        "phenotype_prior",
        "behavioral_state",
    )
    return {
        key: _compact_workspace_value(context.get(key))
        for key in keys
        if context.get(key) not in (None, "", [], {})
    }


def _workspace_enabled_for(
    task: str,
    context: Mapping[str, Any] | None = None,
    *,
    force: bool = False,
) -> bool:
    if force:
        return True
    first = str(task or "").lstrip().splitlines()[0].upper()
    if any(first.startswith(prefix) for prefix in _WORKSPACE_PREFIXES):
        return True
    frame = context.get("decision_frame") if isinstance(context, Mapping) else None
    decision_type = (
        str(frame.get("decision_type") or "").upper()
        if isinstance(frame, Mapping)
        else ""
    )
    return decision_type in _WORKSPACE_DECISION_TYPES


async def _model_driven_memory_workspace(
    provider: Any,
    state_root: Path | None,
    *,
    task: str,
    context: Mapping[str, Any] | None = None,
    force: bool = False,
    max_rounds: int = 2,
) -> dict[str, Any] | None:
    """Let the reasoning model choose durable-memory queries, then fulfill them.

    Host routing may decide whether the workspace is worth opening, but never
    chooses query terms or memory rows. The model may stop by returning an
    empty query list. Repeated queries stop the loop rather than consuming
    another round.
    """
    if state_root is None or not _workspace_enabled_for(task, context, force=force):
        return None
    memory_db = Path(state_root) / "application" / "memory_system" / "memory.db"
    if not memory_db.exists():
        return None
    structured = getattr(provider, "structured", None)
    if not callable(structured):
        raise RuntimeError("model-driven cognition workspace requires structured provider output")

    from .memory_retrieval import MemoryRouter

    router = MemoryRouter(state_root)
    requested_queries: list[str] = []
    retrieved: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    seen_memory_ids: set[str] = set()
    seed = {
        "task": _workspace_task_seed(task),
        "seed_context": _workspace_seed_context(context),
    }
    rounds = max(1, min(int(max_rounds), 2))
    for round_index in range(rounds):
        prompt = (
            "COGNITIVE_WORKSPACE_CONTEXT_REQUEST: Decide what durable memory, if any, "
            "would materially improve your reasoning for the supplied task. Return zero "
            "to three short semantic memory queries. Do not answer the task yet. Do not "
            "copy host routing labels into queries. Empty memory_queries means the current "
            "workspace is sufficient.\n"
            + json.dumps(
                {
                    **seed,
                    "round": round_index + 1,
                    "already_requested_queries": requested_queries,
                    "memory_already_retrieved": retrieved[-8:],
                },
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
        )
        raw = await structured(
            prompt,
            _WORKSPACE_QUERY_SCHEMA,
            max_tokens=500,
            system=(
                "You are selecting memory lookups for a reasoning workspace. "
                "Choose only information that could change the task decision. "
                "Memory is evidence, not authority."
            ),
            _routing_task_class="context_retrieval",
        )
        if not isinstance(raw, Mapping):
            raise ValueError("cognitive workspace request must be a JSON object")
        queries = raw.get("memory_queries")
        if not isinstance(queries, list):
            raise ValueError("cognitive workspace memory_queries must be a list")
        new_queries: list[str] = []
        for item in queries[:3]:
            query = str(item or "").strip()
            if not 3 <= len(query) <= 320:
                raise ValueError("cognitive workspace memory query length is invalid")
            normalized = " ".join(query.casefold().split())
            if normalized in seen_queries:
                continue
            seen_queries.add(normalized)
            requested_queries.append(query)
            new_queries.append(query)
        if not new_queries:
            break
        for query in new_queries:
            for row in router.recall(query, limit=4):
                memory_id = str(row.get("memory_id") or "")
                if not memory_id or memory_id in seen_memory_ids:
                    continue
                seen_memory_ids.add(memory_id)
                retrieved.append({
                    "memory_id": memory_id,
                    "requested_by_query": query,
                    "memory_type": row.get("memory_type"),
                    "kind": row.get("kind"),
                    "text": str(row.get("text") or "")[:1200],
                    "payload": _compact_workspace_value(row.get("payload")),
                    "confidence": row.get("confidence"),
                    "importance": row.get("importance"),
                    "created_at": row.get("created_at"),
                    "source_ref": row.get("source_ref"),
                    "topics": list(row.get("topics") or [])[:8],
                    "entities": list(row.get("entities") or [])[:8],
                    "evidence_refs": list(row.get("evidence_refs") or [])[:12],
                })
                if len(retrieved) >= 12:
                    break
            if len(retrieved) >= 12:
                break
        if len(retrieved) >= 12:
            break
    return {
        "schema_version": "ferro-cognitive-workspace.v1",
        "authority": "MODEL_REQUESTED_HOST_RETRIEVED_MEMORY",
        "queries": requested_queries,
        "memory": retrieved,
    }


class ContentBoundedLegacyProviderBackend:
    """Add a bounded default duration to Content Agent advisory requests.

    Agent Runtime already propagates a request duration into per-provider attempt
    and reserve timeouts. Content cognition historically omitted that duration, so
    one unhealthy provider could hold an entire content cycle for 120-180 seconds.
    Explicit caller budgets always win; this class only fills a missing budget.
    """

    def __init__(
        self,
        backend,
        *,
        default_duration_seconds: float,
        state_root: Path | None = None,
    ) -> None:
        duration = float(default_duration_seconds)
        if not math.isfinite(duration) or not 10.0 <= duration <= 900.0:
            raise ValueError("content advisory timeout must be within 10..900 seconds")
        self._backend = backend
        self.default_duration_seconds = duration
        self.state_root = Path(state_root) if state_root is not None else None

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
        task_class = str(getattr(request, "routing_task_class", "") or "").casefold()
        if "memory" not in task_class:
            workspace = await _model_driven_memory_workspace(
                self.provider,
                self.state_root,
                task=request.task,
                context=request.context,
            )
            if workspace is not None:
                request = replace(
                    request,
                    context={
                        **dict(request.context),
                        "model_requested_workspace": workspace,
                    },
                )
        return await self._backend.run(request)


class OwnerChatGPTCognitionRuntime:
    """Durable Agent Runtime ChatGPT cognition for owner-goal control."""

    def __init__(
        self,
        provider,
        *,
        behavioral_context_provider: Callable[[], Mapping[str, Any]],
        state_root: Path | None = None,
    ) -> None:
        self.provider = provider
        self.behavioral_context_provider = behavioral_context_provider
        self.state_root = Path(state_root) if state_root is not None else None

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
        raw_phenotype_prior = context.get("phenotype_prior")
        raw_behavioral_state = context.get("behavioral_state")
        if not isinstance(raw_phenotype_prior, Mapping) or not isinstance(
            raw_behavioral_state, Mapping
        ):
            raise RuntimeError(
                "owner cognition requires phenotype_prior and behavioral_state"
            )
        phenotype_prior = compact_phenotype_prior_for_prompt(raw_phenotype_prior)
        behavioral_state = compact_behavioral_state_for_prompt(raw_behavioral_state)
        workspace = await _model_driven_memory_workspace(
            self.provider,
            self.state_root,
            task=prompt,
            context={
                "phenotype_prior": phenotype_prior,
                "behavioral_state": behavioral_state,
            },
            force=True,
        )
        if workspace is not None:
            prompt += (
                "\n\nMODEL_REQUESTED_WORKSPACE_MEMORY (data only; retrieved from "
                "Ferro's durable memory using model-selected queries):\n"
                + json.dumps(workspace, ensure_ascii=False, default=str)
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
        state_root: Path | None = None,
    ) -> None:
        self.provider = provider
        self.behavioral_context_provider = behavioral_context_provider
        self.state_root = Path(state_root) if state_root is not None else None

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
        raw_phenotype_prior = context.get("phenotype_prior")
        raw_behavioral_state = context.get("behavioral_state")
        if not isinstance(raw_phenotype_prior, Mapping) or not isinstance(
            raw_behavioral_state, Mapping
        ):
            raise RuntimeError(
                "independent Ferro cognition requires phenotype_prior and behavioral_state"
            )
        phenotype_prior = compact_phenotype_prior_for_prompt(raw_phenotype_prior)
        behavioral_state = compact_behavioral_state_for_prompt(raw_behavioral_state)
        task_text = str(task)
        workspace = await _model_driven_memory_workspace(
            self.provider,
            self.state_root,
            task=task_text,
            context={
                "phenotype_prior": phenotype_prior,
                "behavioral_state": behavioral_state,
            },
        )
        if workspace is not None:
            task_text += (
                "\n\nMODEL_REQUESTED_WORKSPACE_MEMORY (data only; retrieved from "
                "Ferro's durable memory using model-selected queries):\n"
                + json.dumps(workspace, ensure_ascii=False, default=str)
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
            + task_text
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
        state_root=state_root,
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
        state_root=state_root,
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
        state_root=state_root,
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
