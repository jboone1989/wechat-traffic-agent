"""Advisory backend wiring for the persistent Content Agent.

The deployed Content Agent already shares LLM credentials with VirtualBrain.
Agent Runtime currently names the same DeepSeek credential/model variables
differently, so this adapter provides a process-local compatibility seam
without persisting or logging secret values.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator, Mapping


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
def _temporary_runtime_llm_environment() -> Iterator[None]:
    inserted = runtime_llm_aliases()
    for key, value in inserted.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key in inserted:
            os.environ.pop(key, None)


def load_optional_advisory_backend():
    """Load Runtime's advisory backend, or return None when credentials are absent."""
    from agent_runtime.backends import LegacyProviderBackend
    from agent_runtime.llm import LLMProviderError, load_llm_provider

    with _temporary_runtime_llm_environment():
        try:
            provider = load_llm_provider()
        except LLMProviderError as exc:
            if "not configured" in str(exc).lower():
                return None
            raise
    return LegacyProviderBackend(provider)


__all__ = ["load_optional_advisory_backend", "runtime_llm_aliases"]
