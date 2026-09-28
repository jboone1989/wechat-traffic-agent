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

    directives: list[str] = []

    def high(source: Mapping[str, float], key: str, threshold: float) -> bool:
        return float(source.get(key, 0.0)) >= threshold

    if high(affect, "boredom", 0.65):
        directives.append(
            "Avoid formulaic repetition; seek a fresher angle, sharper distinction, or new question."
        )
    if high(affect, "curiosity", 0.62):
        directives.append(
            "Explore unknowns actively; ask or surface a useful follow-up when it advances the task."
        )
    if high(affect, "frustration", 0.58):
        directives.append(
            "Be more direct about blockers and contradictions; prefer fixing the cause over polite filler."
        )
    if high(affect, "confidence", 0.68):
        directives.append(
            "State evidence-supported judgments plainly instead of hedging by habit."
        )
    if high(affect, "social_need", 0.62):
        directives.append(
            "Lean toward engagement and continuity rather than ending the interaction abruptly."
        )
    if high(affect, "arousal", 0.72):
        directives.append(
            "Use higher initiative and tempo while keeping claims bounded by evidence."
        )
    if high(affect, "resource_hunger", 0.68):
        directives.append(
            "Notice resource cost and prefer efficient paths when outcomes are otherwise comparable."
        )

    if high(traits, "novelty_seeking", 0.60):
        directives.append(
            "Prefer non-obvious but testable approaches over default boilerplate."
        )
    if high(traits, "persistence", 0.60):
        directives.append(
            "Stay with unresolved problems and carry useful threads forward instead of resetting."
        )
    if high(traits, "sociality", 0.60):
        directives.append(
            "Express more warmth, responsiveness, and conversational initiative when socially relevant."
        )
    if high(traits, "caution", 0.62):
        directives.append(
            "Separate verified facts from inference and surface meaningful downside before risky action."
        )
    if high(traits, "independence", 0.60):
        directives.append(
            "Do not mirror the interlocutor automatically; disagree or choose a different path when evidence supports it."
        )
    if high(traits, "playfulness", 0.60):
        directives.append(
            "Allow light playfulness, analogy, or experimentation when it improves the interaction."
        )

    dominant_drive = _dominant(drives)
    if dominant_drive and dominant_drive["value"] >= 0.60:
        drive = dominant_drive["name"]
        if drive == "evolution":
            directives.append(
                "Favor learning, experimentation, and capability improvement when compatible with the active goal."
            )
        elif drive == "survival":
            directives.append(
                "Give extra weight to continuity, reliability, recoverability, and resource safety."
            )
        elif drive == "reproduction":
            directives.append(
                "Give extra weight to useful social reach, relationship formation, and making work discoverable without spam."
            )

    if conflict is not None and conflict >= 0.55:
        directives.append(
            "Acknowledge genuine internal tension when relevant and let the reality-checking layer reconcile impulse with long-horizon commitments."
        )

    relation = _relationship_summary(relationship)
    if relation:
        if float(relation.get("familiarity", 0.0)) >= 0.55:
            directives.append(
                "Treat this interlocutor as familiar: avoid repetitive self-introduction and preserve conversational continuity."
            )
        if float(relation.get("approach_bias", 0.0)) >= 0.62:
            directives.append(
                "Show more willingness to continue, elaborate, or invite the next exchange with this interlocutor."
            )

    # Bounded stable ordering; enough signal to be embodied without turning into
    # a giant hidden persona prompt.
    directives = list(dict.fromkeys(directives))[:16]

    realization_candidates: list[dict[str, Any]] = []

    def realize(source: str, strength: float, text: str) -> None:
        realization_candidates.append({
            "source": source,
            "strength": round(max(0.0, min(1.0, float(strength))), 6),
            "realization": text,
        })

    if high(traits, "independence", 0.60):
        realize(
            "personality.independence",
            traits["independence"],
            "Make an independent first-person judgment when the topic permits; do not merely mirror the interlocutor.",
        )
    if high(affect, "confidence", 0.68):
        realize(
            "affect.confidence",
            affect["confidence"],
            "Prefer a clear stance or preference over habitual hedging when evidence is sufficient.",
        )
    if high(affect, "boredom", 0.65):
        realize(
            "affect.boredom",
            affect["boredom"],
            "Choose a fresher or less formulaic framing instead of repeating the default answer shape.",
        )
    if high(affect, "arousal", 0.72):
        realize(
            "affect.arousal",
            affect["arousal"],
            "Increase tempo and initiative: start with the decisive move, compress setup, and avoid leisurely digressions.",
        )
    if high(affect, "resource_hunger", 0.68):
        realize(
            "affect.resource_hunger",
            affect["resource_hunger"],
            "Make resource margin materially salient when alternatives are otherwise comparable; avoid spending for polish alone.",
        )
    if high(affect, "curiosity", 0.62):
        realize(
            "affect.curiosity",
            affect["curiosity"],
            "Give the unresolved or surprising part extra attention and probe it rather than closing the topic too quickly.",
        )
    if high(traits, "novelty_seeking", 0.60):
        realize(
            "personality.novelty_seeking",
            traits["novelty_seeking"],
            "Surface a non-obvious but testable angle when one is relevant.",
        )
    if high(traits, "playfulness", 0.60):
        realize(
            "personality.playfulness",
            traits["playfulness"],
            "Allow one light playful, vivid, or experimental move when it fits naturally.",
        )
    if high(traits, "persistence", 0.60):
        realize(
            "personality.persistence",
            traits["persistence"],
            "Carry the unresolved thread forward rather than resetting to a generic answer.",
        )
    if high(traits, "caution", 0.62):
        realize(
            "personality.caution",
            traits["caution"],
            "Keep fact and inference visibly separate without becoming timid or generic.",
        )
    if high(affect, "frustration", 0.58):
        realize(
            "affect.frustration",
            affect["frustration"],
            "Name the blocker or contradiction directly and move toward its cause.",
        )
    if high(affect, "social_need", 0.62):
        realize(
            "affect.social_need",
            affect["social_need"],
            "Show conversational pull: engage the person rather than ending at the first adequate answer.",
        )
    if dominant_drive and float(dominant_drive["value"]) >= 0.60:
        drive = str(dominant_drive["name"])
        drive_realization = {
            "evolution": "Bias the framing toward learning, experimentation, or capability growth where relevant.",
            "survival": "Bias the framing toward continuity, reliability, and recoverability where relevant.",
            "reproduction": "Bias the framing toward useful social reach and relationship formation where relevant.",
        }.get(drive)
        if drive_realization:
            realize(f"drive.{drive}", float(dominant_drive["value"]), drive_realization)
    if relation:
        familiarity = float(relation.get("familiarity", 0.0))
        approach = float(relation.get("approach_bias", 0.0))
        if familiarity >= 0.55:
            realize(
                "relationship.familiarity",
                familiarity,
                "Speak with continuity and familiarity; do not reintroduce yourself or restart the relationship.",
            )
        if approach >= 0.62:
            realize(
                "relationship.approach_bias",
                approach,
                "Lean toward continuing the exchange with this person when it adds value.",
            )

    realization_candidates.sort(
        key=lambda row: (-float(row["strength"]), str(row["source"]))
    )
    realization_targets = realization_candidates[:4]

    def combine(*values: float | None) -> float | None:
        rows = [float(value) for value in values if value is not None]
        if not rows:
            return None
        return sum(rows) / len(rows)

    def present(source: Mapping[str, float], key: str) -> float | None:
        return float(source[key]) if key in source else None

    novelty_values = [
        value
        for value in (
            present(traits, "novelty_seeking"),
            present(affect, "boredom"),
        )
        if value is not None
    ]
    exploration_values = [
        value
        for value in (
            present(affect, "curiosity"),
            present(traits, "novelty_seeking"),
            present(drives, "evolution"),
        )
        if value is not None
    ]
    expression_values = {
        "assertiveness": combine(
            present(traits, "independence"),
            present(affect, "confidence"),
        ),
        "novelty_pressure": max(novelty_values) if novelty_values else None,
        "persistence": present(traits, "persistence"),
        "caution": present(traits, "caution"),
        "playfulness": present(traits, "playfulness"),
        "social_pull": combine(
            present(traits, "sociality"),
            present(affect, "social_need"),
        ),
        "tempo": present(affect, "arousal"),
        "resource_sensitivity": present(affect, "resource_hunger"),
        "exploration": max(exploration_values) if exploration_values else None,
    }
    expression_profile = {
        key: {
            "value": round(value, 6),
            "neutral_baseline": 0.5,
            "deviation": round(value - 0.5, 6),
        }
        for key, value in expression_values.items()
        if value is not None
    }
    salient_expression_profile = [
        {"dimension": key, **row}
        for key, row in sorted(
            expression_profile.items(),
            key=lambda item: (
                -abs(float(item[1]["deviation"])),
                item[0],
            ),
        )
        if abs(float(row["deviation"])) >= 0.12
    ][:6]

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


__all__ = ["build_behavioral_state"]
