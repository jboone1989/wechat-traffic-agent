"""Collect available capabilities and bounded behavioral context for cognition.

Reads only injected runtime projections. It does not retrieve memories,
select evidence, trigger diagnostics, or write files.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Callable


@dataclass(frozen=True)
class CognitionEnvironment:
    capabilities: list[str]
    application_capabilities: list[str]
    domain_capabilities: list[str]
    manifest_capabilities: list[str]
    capability_beliefs: list[dict[str, Any]]
    social_projection: dict[str, Any]
    relationships: list[dict[str, Any]]
    full_behavior: dict[str, Any]
    full_phenotype: dict[str, Any]
    behavior: dict[str, Any]
    phenotype: dict[str, Any]


class CognitionEnvironmentReader:
    def __init__(self, *, build_behavior: Callable, build_phenotype: Callable,
                 compact_behavior: Callable, compact_phenotype: Callable):
        self.build_behavior, self.build_phenotype = build_behavior, build_phenotype
        self.compact_behavior, self.compact_phenotype = compact_behavior, compact_phenotype

    @staticmethod
    def _inventory(application, domain_runtime):
        runtime = getattr(application, "runtime", None)
        application_caps = (
            list(runtime.available_capabilities())
            if runtime is not None and callable(getattr(runtime, "available_capabilities", None))
            else []
        )
        domain_caps = (
            list(domain_runtime.capability_inventory.usable_ids())
            if domain_runtime is not None
            and getattr(domain_runtime, "capability_inventory", None) is not None
            else []
        )
        manifest = getattr(domain_runtime, "agent_manifest", None)
        manifest_caps = list(getattr(manifest, "capabilities", ()) or ())
        available = sorted(set(
            str(item) for item in [*application_caps, *domain_caps, *manifest_caps]
            if str(item).strip()
        ))
        return available, application_caps, domain_caps, manifest_caps

    @staticmethod
    def _social_and_beliefs(wake):
        model = wake.get("self_model") if isinstance(wake.get("self_model"), Mapping) else {}
        beliefs = []
        for capability, raw in (model.get("capability_beliefs") or {}).items():
            if not isinstance(raw, Mapping):
                continue
            beliefs.append({
                "capability": str(capability),
                "confidence": raw.get("confidence"),
                "attempts": int(raw.get("attempts") or 0),
                "successes": int(raw.get("successes") or 0),
                "failures": int(raw.get("failures") or 0),
                "last_verified": raw.get("last_verified"),
                "source": raw.get("source"),
            })
        beliefs.sort(
            key=lambda row: (
                -int(row.get("attempts") or 0),
                -float(row.get("confidence") or 0.0),
                str(row.get("capability") or ""),
            )
        )
        social = wake.get("neural_social") if isinstance(wake.get("neural_social"), Mapping) else {}
        relationships = [
            dict(row) for row in list(social.get("recent_relationships") or ())
            if isinstance(row, Mapping)
        ][:8]
        return beliefs, dict(social), relationships

    def collect(self, *, application, domain_runtime, wake):
        capabilities, app_caps, domain_caps, manifest_caps = self._inventory(
            application, domain_runtime,
        )
        beliefs, social, relationships = self._social_and_beliefs(wake)
        full_behavior = self.build_behavior(wake)
        full_phenotype = self.build_phenotype(full_behavior)
        return CognitionEnvironment(
            capabilities, app_caps, domain_caps, manifest_caps,
            beliefs, social, relationships, full_behavior, full_phenotype,
            self.compact_behavior(full_behavior),
            self.compact_phenotype(full_phenotype),
        )
