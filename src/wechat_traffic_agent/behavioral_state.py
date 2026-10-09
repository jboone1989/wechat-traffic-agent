"""Compact behavioral modulation derived from Ferro's persisted neural state.

This module turns affect, drives, learned neural personality and psychodynamic
state into one bounded control envelope.  It does not invent subjective
experience and it never grants authority; it only modulates how existing goals,
facts and permissions are expressed and pursued.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_AFFECT_KEYS = (
    "arousal",
    "curiosity",
    "frustration",
    "confidence",
    "boredom",
    "social_need",
    "resource_hunger",
)
_DRIVE_KEYS = ("survival", "reproduction", "evolution")
_TRAIT_KEYS = (
    "novelty_seeking",
    "persistence",
    "sociality",
    "caution",
    "independence",
    "playfulness",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, score)), 6)


def _scores(source: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, float]:
    rows: dict[str, float] = {}
    for key in keys:
        value = _score(source.get(key))
        if value is not None:
            rows[key] = value
    return rows


def _dominant(values: Mapping[str, float]) -> dict[str, Any] | None:
    if not values:
        return None
    name, value = max(values.items(), key=lambda row: (row[1], row[0]))
    return {"name": name, "value": value}


def _relationship_summary(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    row = _mapping(value)
    if not row:
        return None
    result: dict[str, Any] = {}
    for key in ("familiarity", "valence", "approach_bias", "trust", "interest"):
        score = _score(row.get(key))
        if score is not None:
            result[key] = score
    interactions = row.get("interactions", row.get("interaction_count"))
    try:
        if interactions is not None:
            result["interactions"] = max(0, int(interactions))
    except (TypeError, ValueError):
        pass
    return result or None


def build_behavioral_state(
    wakeful_state: Mapping[str, Any] | None,
    *,
    relationship: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a small, stable behavior-control envelope for cognition and speech."""
    wake = _mapping(wakeful_state)
    life = _mapping(wake.get("neural_life"))
    affect = _scores(_mapping(life.get("affect")), _AFFECT_KEYS)
    drives = _scores(_mapping(life.get("drives")), _DRIVE_KEYS)
    intention_raw = _mapping(life.get("intention"))
    intention = {
        "name": str(intention_raw.get("name") or "").strip(),
        "strength": _score(intention_raw.get("strength")),
    }
    if not intention["name"] and intention["strength"] is None:
        intention = {}

    personality = _mapping(wake.get("personality"))
    traits = _scores(_mapping(personality.get("traits")), _TRAIT_KEYS)
    psyche = _mapping(wake.get("psychodynamics"))
    psych_id = _mapping(psyche.get("id"))
    superego = _mapping(psyche.get("superego"))
    conflict = _score(psyche.get("conflict"))
    psychodynamics = {
        "dominant_impulse": str(psych_id.get("dominant_impulse") or "").strip(),
        "conflict": conflict,
        "continuity_pressure": _score(superego.get("continuity_pressure")),
        "growth_commitment": _score(superego.get("growth_commitment")),
    }
    psychodynamics = {
        key: value for key, value in psychodynamics.items()
        if value not in (None, "")
    }

    relation = _relationship_summary(relationship)
    dominant_drive = _dominant(drives)
    from .behavioral_modulation_rules import BehavioralModulationRules
    (directives, realization_targets, expression_profile,
     salient_expression_profile) = BehavioralModulationRules().compile(
        affect=affect, traits=traits, drives=drives, conflict=conflict,
        relation=relation, dominant_drive=dominant_drive,
    )

    return {
        "schema_version": "ferro-behavioral-state.v3",
        "source": "persisted neural life + learned neural personality + psychodynamics",
        "affect": affect,
        "drives": drives,
        "intention": intention,
        "personality_traits": traits,
        "psychodynamics": psychodynamics,
        "relationship": relation,
        "dominant_affect": _dominant(affect),
        "dominant_drive": dominant_drive,
        "behavioral_directives": directives,
        "realization_targets": realization_targets,
        "expression_profile": expression_profile,
        "salient_expression_profile": salient_expression_profile,
        "control_semantics": (
            "These are current behavioral inputs, not decorative telemetry. "
            "They should materially modulate tone, initiative, attention, disagreement, "
            "topic emphasis, persistence, social warmth, and action preference while never "
            "changing facts, safety boundaries, or authorization. Expression-profile values "
            "are relative intensities around a 0.5 neutral baseline; the strongest relevant "
            "deviations should be behaviorally observable rather than averaged back into "
            "generic assistant style. When an active affect target is present, stable "
            "personality alone must not satisfy the modulation: that affect should also "
            "change the manner, tempo, tolerance, or salience of the response."
        ),
        "expression_policy": (
            "Embody the state naturally; do not recite scores unless asked. "
            "Functional affect may be expressed in first person when salient, but do not claim "
            "human subjective experience or biological emotion."
        ),
    }


def build_phenotype_prior(
    behavioral_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Split always-on individual identity from momentary behavioral modulation."""
    state = _mapping(behavioral_state)
    stable = {
        "personality_traits": dict(_mapping(state.get("personality_traits"))),
        "psychodynamics": dict(_mapping(state.get("psychodynamics"))),
    }
    dynamic = {
        "affect": dict(_mapping(state.get("affect"))),
        "drives": dict(_mapping(state.get("drives"))),
        "intention": dict(_mapping(state.get("intention"))),
        "dominant_affect": state.get("dominant_affect"),
        "dominant_drive": state.get("dominant_drive"),
    }
    expression = {
        "expression_profile": dict(_mapping(state.get("expression_profile"))),
        "salient_expression_profile": [
            dict(row)
            for row in list(state.get("salient_expression_profile") or [])
            if isinstance(row, Mapping)
        ][:6],
        "realization_targets": [
            dict(row)
            for row in list(state.get("realization_targets") or [])
            if isinstance(row, Mapping)
        ][:4],
        "behavioral_directives": [
            str(row)
            for row in list(state.get("behavioral_directives") or [])
            if str(row)
        ][:16],
    }
    return {
        "schema_version": "ferro-phenotype-prior.v1",
        "stable_phenotype": stable,
        "dynamic_modulation": dynamic,
        "expression": expression,
        "retrieval_policy": {
            "stable_phenotype": "ALWAYS_ON_NEVER_RETRIEVAL_CONDITIONED",
            "dynamic_modulation": "ALWAYS_ON_REFRESH_EACH_COGNITION",
            "situational_context": "DECISION_CONDITIONED_RETRIEVAL",
        },
        "control_semantics": (
            "The stable phenotype is part of Ferro's persistent individual identity and "
            "must influence every cognition with meaningful behavioral choice. Dynamic "
            "modulation changes current intensity and salience without replacing that "
            "individual identity. Neither may change facts, safety, or authorization."
        ),
    }


def compact_behavioral_state_for_prompt(
    behavioral_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project full behavioral state into a small model-control envelope."""
    state = _mapping(behavioral_state)
    return {
        "schema_version": "ferro-behavioral-prompt.v1",
        "affect": dict(_mapping(state.get("affect"))),
        "drives": dict(_mapping(state.get("drives"))),
        "intention": dict(_mapping(state.get("intention"))),
        "personality_traits": dict(_mapping(state.get("personality_traits"))),
        "psychodynamics": dict(_mapping(state.get("psychodynamics"))),
        "relationship": state.get("relationship"),
        "dominant_affect": state.get("dominant_affect"),
        "dominant_drive": state.get("dominant_drive"),
        "realization_targets": [
            {
                "source": str(row.get("source") or ""),
                "strength": row.get("strength"),
                "realization": str(row.get("realization") or ""),
            }
            for row in list(state.get("realization_targets") or [])[:4]
            if isinstance(row, Mapping)
        ],
        "behavioral_directives": [
            str(row)
            for row in list(state.get("behavioral_directives") or [])[:6]
            if str(row)
        ],
    }


def compact_phenotype_prior_for_prompt(
    phenotype_prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Keep stable identity and live modulation without expression duplication."""
    prior = _mapping(phenotype_prior)
    return {
        "schema_version": "ferro-phenotype-prompt.v1",
        "stable_phenotype": dict(_mapping(prior.get("stable_phenotype"))),
        "dynamic_modulation": dict(_mapping(prior.get("dynamic_modulation"))),
        "retrieval_policy": dict(_mapping(prior.get("retrieval_policy"))),
    }


__all__ = [
    "build_behavioral_state",
    "build_phenotype_prior",
    "compact_behavioral_state_for_prompt",
    "compact_phenotype_prior_for_prompt",
]
