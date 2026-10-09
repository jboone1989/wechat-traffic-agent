"""Pure modulation rules from normalized neural/personality state.

No memory, file I/O, runtime authority or model invocation. The resulting
directives, realization targets and expression profile are hints, not decisions.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any


def high(source: Mapping[str, float], key: str, threshold: float) -> bool:
    return float(source.get(key, 0.0)) >= threshold


class BehavioralModulationRules:
    @staticmethod
    def _affect(affect):
        directives: list[str] = []
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

        return directives

    @staticmethod
    def _traits(traits):
        directives: list[str] = []
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

        return directives

    @staticmethod
    def _drives(dominant_drive, conflict):
        directives: list[str] = []
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

        return directives

    @staticmethod
    def _relationship(relation):
        directives: list[str] = []
        if relation:
            if float(relation.get("familiarity", 0.0)) >= 0.55:
                directives.append(
                    "Treat this interlocutor as familiar: avoid repetitive self-introduction and preserve conversational continuity."
                )
            if float(relation.get("approach_bias", 0.0)) >= 0.62:
                directives.append(
                    "Show more willingness to continue, elaborate, or invite the next exchange with this interlocutor."
                )

        return directives

    @staticmethod
    def _realization_base(affect, traits, realize):
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

    @staticmethod
    def _realization_social(dominant_drive, relation, realize):
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


    @staticmethod
    def _expression_profile(affect, traits, drives):
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

        return expression_profile, salient_expression_profile

    def compile(self, *, affect, traits, drives, conflict, relation, dominant_drive):
        directives = list(dict.fromkeys([
            *self._affect(affect),
            *self._traits(traits),
            *self._drives(dominant_drive, conflict),
            *self._relationship(relation),
        ]))[:16]
        candidates = []
        def realize(source, strength, text):
            candidates.append({
                "source": source,
                "strength": round(max(0.0, min(1.0, float(strength))), 6),
                "realization": text,
            })
        self._realization_base(affect, traits, realize)
        self._realization_social(dominant_drive, relation, realize)
        candidates.sort(key=lambda row: (-float(row["strength"]), str(row["source"])))
        profile, salient = self._expression_profile(affect, traits, drives)
        return directives, candidates[:4], profile, salient
