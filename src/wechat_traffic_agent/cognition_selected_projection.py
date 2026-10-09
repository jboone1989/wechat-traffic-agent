"""Pure projection of ranked decision facts into the cognition prompt schema.

This layer does no retrieval, model calls, file IO or capability diagnosis.
All selected fields originate in the same bounded DecisionFactSelector result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _clip(value: Any, limit: int = 1200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit - 14] + "...[truncated]"


@dataclass(frozen=True)
class SelectedCognitionSections:
    current_intent: list
    missions: dict
    mind: dict
    experience: list
    memory: list
    resources: dict
    capabilities: list
    capability_beliefs: list
    relationships: list
    commitments: list
    operational: dict
    neural_life: dict


def _mind_sections(decision_context, projection):
    selected = (
        decision_context.get("selected")
        if isinstance(decision_context.get("selected"), Mapping)
        else {}
    )

    selected_current_intent = [
        dict(row)
        for row in list(selected.get("goal") or [])
        if isinstance(row, Mapping)
    ]
    selected_missions = [
        dict(row)
        for row in list(selected.get("mission") or [])
        if isinstance(row, Mapping) and row.get("mission_id")
    ]
    selected_experiments = [
        dict(row)
        for row in list(selected.get("experiment") or [])
        if isinstance(row, Mapping)
    ]
    selected_mission_state = {
        "active": selected_missions,
        "recent_experiments": selected_experiments,
    }
    selected_plan = next(
        (
            dict(row)
            for row in list(selected.get("plan") or [])
            if isinstance(row, Mapping)
        ),
        None,
    )
    selected_strategy_topics = [
        str(row.get("topic") or "")
        for row in list(selected.get("strategy") or [])
        if isinstance(row, Mapping) and str(row.get("topic") or "")
    ]
    mind_section = {
        "mind_schema": projection.get("mind_schema"),
        "mind_tick": projection.get("mind_tick"),
        "interests": [
            dict(row)
            for row in list(selected.get("interest") or [])
            if isinstance(row, Mapping)
        ],
        "open_inquiries": [
            dict(row)
            for row in list(selected.get("inquiry") or [])
            if isinstance(row, Mapping)
        ],
        "beliefs": [
            dict(row)
            for row in list(selected.get("belief") or [])
            if isinstance(row, Mapping)
        ],
        "recent_belief_changes": [
            dict(row)
            for row in list(selected.get("belief_change") or [])
            if isinstance(row, Mapping)
        ],
        "latest_plan": selected_plan,
        "strategy_learning": {
            "outcome_count": int(
                ((projection.get("strategy_learning") or {}).get("outcome_count") or 0)
            ),
            "topics": selected_strategy_topics,
        },
        "learning_boundary": _clip(projection.get("learning_boundary"), 1200),
    }
    recent_experience = [
        dict(row)
        for row in list(selected.get("experience") or [])
        if isinstance(row, Mapping)
    ]
    retrieved_memory = [
        dict(row)
        for row in list(selected.get("memory") or [])
        if isinstance(row, Mapping)
    ]
    return selected, selected_current_intent, selected_mission_state, mind_section, recent_experience, retrieved_memory

def _environment_sections(selected):
    selected_resources = next(
        (
            dict(row)
            for row in list(selected.get("resource") or [])
            if isinstance(row, Mapping)
        ),
        {},
    )
    selected_capabilities = [
        str(row)
        for row in list(selected.get("capability") or [])
        if str(row)
    ]
    selected_capability_beliefs = [
        dict(row)
        for row in list(selected.get("capability_belief") or [])
        if isinstance(row, Mapping)
    ]
    selected_relationships = [
        dict(row)
        for row in list(selected.get("relationship") or [])
        if isinstance(row, Mapping)
    ]
    selected_commitments = [
        dict(row)
        for row in list(selected.get("commitment") or [])
        if isinstance(row, Mapping)
    ]
    selected_operational = next(
        (
            dict(row)
            for row in list(selected.get("operational") or [])
            if isinstance(row, Mapping)
        ),
        {},
    )
    selected_internal = [
        dict(row)
        for row in list(selected.get("internal") or [])
        if isinstance(row, Mapping)
    ]
    selected_neural_life: dict[str, Any] = {}
    for row in selected_internal:
        selected_neural_life.update(row)

    return (selected_resources, selected_capabilities, selected_capability_beliefs,
            selected_relationships, selected_commitments, selected_operational,
            selected_neural_life)


def project_selected_cognition(decision_context, projection):
    (selected, intents, missions, mind, experience, memory) = _mind_sections(
        decision_context, projection,
    )
    resources, available, beliefs, relationships, commitments, operational, neural = (
        _environment_sections(selected)
    )
    return SelectedCognitionSections(
        intents, missions, mind, experience, memory, resources,
        available, beliefs, relationships, commitments, operational, neural,
    )
