"""Domain decisions implemented by Ferro's bounded L1 provider.

This layer consumes already-observed state and chooses among explicit finite
options.  It never invents facts, writes prose, or silently substitutes L2.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fast_decision import (
    FastDecisionError,
    FastDecisionProvider,
    load_fast_decision_provider,
)


def _clip(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 14] + "...[truncated]"


def _compact_mapping(
    value: Any,
    keys: Sequence[str],
) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {key: row.get(key) for key in keys if row.get(key) is not None}


class FerroL1Decisions:
    """Shared bounded choices for health, X strategy, and context routing."""

    def __init__(
        self,
        *,
        state_root: Path,
        provider: FastDecisionProvider | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self._provider = provider

    @property
    def provider(self) -> FastDecisionProvider:
        if self._provider is None:
            self._provider = load_fast_decision_provider(state_root=self.state_root)
        return self._provider

    def assess_health(
        self,
        *,
        task: str,
        deterministic: Mapping[str, Any],
        recent_observations: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Classify organism capability health without replacing hard evidence."""

        task = str(task or "UNKNOWN").upper()
        current_subsystem = str(deterministic.get("subsystem") or "unknown")
        candidate_subsystems: list[str] = []
        for subsystem in [
            current_subsystem,
            *[
                str(row.get("subsystem") or "")
                for row in recent_observations[-8:]
                if isinstance(row, Mapping) and bool(row.get("abnormal"))
            ],
            "unknown",
        ]:
            subsystem = subsystem.strip() or "unknown"
            if subsystem not in candidate_subsystems:
                candidate_subsystems.append(subsystem)
        candidate_subsystems = candidate_subsystems[:5]

        choices: dict[str, str] = {
            "HEALTHY__none": (
                "No material evidence that this capability is impaired or stuck."
            ),
            "UNKNOWN__unknown": (
                "The evidence is insufficient or conflicting; more observation is needed."
            ),
        }
        for health, meaning in (
            ("DEGRADED", "Capability still works but errors, retries, fallback dependence, or quality loss are present."),
            ("STUCK", "Capability is repeatedly executing or waiting without meaningful goal-relevant progress."),
            ("BROKEN", "Capability cannot currently perform its intended effect because of a hard failure or unavailable dependency."),
        ):
            for subsystem in candidate_subsystems:
                choices[f"{health}__{subsystem}"] = f"{meaning} Most likely subsystem: {subsystem}."

        symptoms = [
            {
                "kind": str(row.get("kind") or ""),
                "severity": row.get("severity"),
                "path": str(row.get("path") or "")[:240],
                "detail": str(row.get("detail") or "")[:240],
            }
            for row in list(deterministic.get("symptoms") or [])[:12]
            if isinstance(row, Mapping)
        ]
        state = {
            "task": task,
            "current_result": {
                "primary_status": deterministic.get("primary_status"),
                "abnormal": bool(deterministic.get("abnormal")),
                "subsystem": current_subsystem,
                "base_severity": deterministic.get("base_severity"),
                "prediction_error": deterministic.get("prediction_error"),
                "negative_world_utility": deterministic.get("negative_world_utility"),
                "symptoms": symptoms,
            },
            "recent_same_task": [
                {
                    "abnormal": bool(row.get("abnormal")),
                    "subsystem": row.get("subsystem"),
                    "primary_symptom": row.get("primary_symptom"),
                    "severity": row.get("severity"),
                    "primary_status": row.get("primary_status"),
                }
                for row in recent_observations[-8:]
                if isinstance(row, Mapping)
            ],
        }
        result = self.provider.choose(
            decision="health_state",
            state=state,
            choices=choices,
            instructions={
                "objective": (
                    "Classify current capability health from observed evidence. "
                    "Do not infer hidden causes beyond the supplied candidate subsystems."
                ),
                "rules": [
                    "A hard error or unavailable dependency must not be classified HEALTHY.",
                    "Repeated no-progress evidence supports STUCK even when individual calls return success.",
                    "Use BROKEN only when the intended capability effect is currently unavailable.",
                    "Use UNKNOWN when evidence is genuinely insufficient or contradictory.",
                ],
            },
        )
        health_state, subsystem = result.choice.split("__", 1)
        hard_evidence = any(
            str(row.get("kind") or "") in {
                "hard_error",
                "unavailable",
                "capability_gap",
            }
            or float(row.get("severity") or 0.0) >= 0.70
            for row in symptoms
        )
        if hard_evidence and health_state == "HEALTHY":
            raise FastDecisionError(
                "Jev health decision contradicted hard observed failure evidence"
            )
        if bool(deterministic.get("abnormal")) and health_state == "HEALTHY":
            raise FastDecisionError(
                "Jev health decision suppressed an observed abnormal result"
            )
        return {
            "schema_version": "ferro-l1-health.v1",
            "status": "ASSESSED",
            "health_state": health_state,
            "subsystem": None if subsystem == "none" else subsystem,
            "decision_id": result.decision_id,
            "provider": result.provider,
            "model": result.model,
            "confidence": result.confidence,
            "probabilities": dict(result.probabilities),
            "latency_ms": result.latency_ms,
            "decided_at": result.decided_at,
        }

    def route_context(
        self,
        *,
        prompt: str,
        surface: str,
        owner_mode: bool = False,
    ) -> dict[str, Any]:
        """Choose the information-routing decision family for Context Builder."""

        text = str(prompt or "").strip()
        if surface in {"public_chat", "owner_chat"}:
            decision_type = (
                "PUBLIC_DIALOGUE" if surface == "public_chat" else "OWNER_DIALOGUE"
            )
            return {
                "schema_version": "ferro-l1-context-route.v1",
                "status": "SURFACE_BOUND",
                "route": "CHAT",
                "decision_type": decision_type,
                "time_horizon": "CURRENT_TURN",
                "provider": "SURFACE_CONTRACT",
                "confidence": 1.0,
            }

        match = re.match(r"\s*([A-Z][A-Z0-9_]{2,})\s*:", text)
        prefix = match.group(1) if match else ""
        choices = {
            "VERIFY": "Verify or falsify a claim, prediction, result, state, or completed action from evidence.",
            "PLAN": "Choose or revise a multi-step path toward a goal under constraints.",
            "ACT": "Select the next concrete useful action or tool-level move.",
            "CHAT": "Answer or converse without a separate operational decision.",
            "REPAIR": "Diagnose or repair a broken, degraded, or stuck capability.",
            "LEARN": "Update memory, strategy, salience, or behavior from observed outcomes.",
            "PREDICT": "Make a bounded forward-looking prediction with an explicit outcome window.",
            "RESEARCH": "Explore or synthesize evidence where the primary need is investigation rather than immediate action.",
        }
        result = self.provider.choose(
            decision="context_route",
            state={
                "surface": str(surface),
                "owner_mode": bool(owner_mode),
                "task_prefix": prefix or None,
                "prompt": _clip(text, 2200),
            },
            choices=choices,
            instructions={
                "objective": (
                    "Choose the information need that should control context retrieval "
                    "before deep cognition begins."
                ),
                "rules": [
                    "Choose REPAIR for diagnosis or remediation of capability failure.",
                    "Choose VERIFY when evidence is needed to establish whether something is true or completed.",
                    "Choose ACT for a next-action choice that is not primarily multi-step planning.",
                    "Choose LEARN when the output should change future behavior from outcomes.",
                    "Do not choose CHAT merely because the input is written in natural language.",
                ],
            },
        )
        route = result.choice
        decision_type, horizon = {
            "VERIFY": ("VERIFICATION", "CURRENT_STEP"),
            "PLAN": ("PLANNING", "MULTI_STEP"),
            "ACT": (
                "OWNER_GOAL_ACTION" if owner_mode else "SELECTION",
                "NEXT_USEFUL_ACTION" if owner_mode else "CURRENT_STEP",
            ),
            "CHAT": ("GENERAL_COGNITION", "CURRENT_TURN"),
            "REPAIR": ("REPAIR", "CURRENT_STEP"),
            "LEARN": ("LEARNING_UPDATE", "NEXT_SIMILAR_DECISION"),
            "PREDICT": ("PREDICTION", "DECLARED_PREDICTION_WINDOW"),
            "RESEARCH": ("RESEARCH", "EXPLORATORY"),
        }[route]
        return {
            "schema_version": "ferro-l1-context-route.v1",
            "status": "ROUTED",
            "route": route,
            "decision_type": decision_type,
            "time_horizon": horizon,
            "task_prefix": prefix or None,
            "decision_id": result.decision_id,
            "provider": result.provider,
            "model": result.model,
            "confidence": result.confidence,
            "probabilities": dict(result.probabilities),
            "latency_ms": result.latency_ms,
            "decided_at": result.decided_at,
        }

    def choose_x_strategy(
        self,
        *,
        cycle: str,
        cognition: Mapping[str, Any],
        evidence: Mapping[str, Any],
        mission: Mapping[str, Any],
        performance: Mapping[str, Any],
        owner_goal: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Choose bounded X presentation and exploration strategy before L2 writes."""

        resolution = (
            evidence.get("resolution")
            if isinstance(evidence.get("resolution"), Mapping)
            else {}
        )
        decisions: dict[str, Mapping[str, str]] = {
            "post_timing": {
                "POST_NOW": "This evidence-compatible expression is worth publishing in the current cycle.",
                "HOLD": "Do not publish an original X post in this cycle; preserve the evidence for later use.",
            },
            "x_format": {
                "SHORT_POST": "A concise X post is sufficient for the information value.",
                "LONG_POST": "One X post benefits materially from additional context and explanation.",
                "ARTICLE": "A native illustrated X Article provides durable structured explanation.",
            },
            "exploration_mode": {
                "EXPLOIT": "Use the strongest currently supported strategy pattern.",
                "EXPLORE": "Try a materially different evidence-compatible presentation to learn from outcomes.",
            },
            "angle": {
                "EVIDENCE_FIRST": "Lead with the strongest supported fact or observation.",
                "BUILD_IN_PUBLIC": "Frame around concrete work, experiment, continuity, or what Ferro learned while building.",
                "FRAMEWORK": "Present a compact reusable model, checklist, or conceptual frame grounded in the evidence.",
                "QUESTION": "Anchor in a known fact and foreground a genuine unresolved question.",
                "COUNTERPOINT": "Lead with a supported counterpoint or tension without manufacturing controversy.",
            },
        }
        state = {
            "cycle": str(cycle),
            "cognition": {
                key: (_clip(cognition.get(key), 1200) if isinstance(cognition.get(key), str) else cognition.get(key))
                for key in (
                    "expression_kind",
                    "worth_saying",
                    "topic",
                    "summary",
                    "uncertainty",
                    "belief_proposition",
                    "what_changed",
                    "what_matters",
                    "rationale",
                )
                if cognition.get(key) is not None
            },
            "evidence": {
                "action": resolution.get("action"),
                "rationale": _clip(resolution.get("rationale"), 500),
                "useful_fact_count": len(list(resolution.get("useful_facts") or [])),
                "useful_facts": [_clip(fact, 500) for fact in list(resolution.get("useful_facts") or [])[:6]],
            },
            "mission": _compact_mapping(
                mission,
                (
                    "status",
                    "current",
                    "target",
                    "trajectory",
                    "pressure",
                    "exploration_rate",
                    "active_experiment",
                    "hard_stop",
                    "owner_strategy_advice",
                ),
            ),
            "owner_goal": {
                "context_authority": "STRATEGY_ONLY",
                **{key: _clip((owner_goal or {}).get(key), 1200)
                   for key in ("goal_id", "description", "origin")
                   if (owner_goal or {}).get(key) is not None},
            },
            "x_performance": _compact_mapping(
                performance,
                (
                    "status",
                    "generation_authority",
                    "sample_count",
                    "favored_pattern",
                    "observations",
                ),
            ),
        }
        results = self.provider.choose_many(
            state=state,
            decisions=decisions,
            instructions={
                "post_timing": {
                    "objective": "Decide whether an original X post is useful now.",
                    "rules": [
                        "Consider the supplied activated owner goal when deciding how to advance the current commitment.",
                        "Owner strategy context does not replace evidence, safety, or publication gates.",
                        "Do not post to satisfy a quota.",
                        "HOLD when information value is weak or another interaction mode is clearly more useful.",
                    ],
                },
                "x_format": {
                    "objective": "Choose the smallest format that preserves the useful information.",
                    "rules": [
                        "LONG_POST requires genuine additional explanatory value.",
                        "SHORT_POST is preferred when the idea remains clear without extra context.",
                    ],
                },
                "exploration_mode": {
                    "objective": "Balance current mission performance with learning value.",
                    "rules": [
                        "EXPLORE when current strategy evidence is weak or mission exploration pressure is high.",
                        "EXPLOIT when a strategy has stronger measured support.",
                    ],
                },
                "angle": {
                    "objective": "Choose an evidence-compatible presentation family, never a new factual topic.",
                },
            },
        )
        choices = {name: row.choice for name, row in results.items()}
        first = next(iter(results.values()))
        return {
            "schema_version": "ferro-l1-x-strategy.v1",
            "status": "SELECTED",
            **choices,
            "interaction_mode": "ORIGINAL",
            "decision_ids": {
                name: row.decision_id for name, row in results.items()
            },
            "provider": first.provider,
            "model": first.model,
            "confidence": {
                name: row.confidence for name, row in results.items()
            },
            "latency_ms": max(row.latency_ms for row in results.values()),
            "decided_at": first.decided_at,
        }

    def choose_x_discovery(self, *, mission, persona, previous=None):
        results = self.provider.choose_many(
            state={"mission": mission, "persona": persona, "previous_discovery":previous or {}},
            decisions={"source": {
                "MENTIONS": "Read direct questions and responses to Ferro.",
                "HOME": "Explore the personalized following/recommendation timeline.",
                "AGENT_RUNTIME": "Search public discussion of persistent agents and runtime state.",
                "LIVINGRUNTIME": "Search public discussion of LivingRuntime Remote.",
                "AGENT_MEMORY": "Search public discussion of agent memory and continuity.",
            }},
            instructions={"source": {"objective": "Choose public conversations Ferro wants to read and potentially join.",
                "rules": ["Owner suggested quantities are optional advice, never quotas.",
                          "Choose by curiosity and relevance; reply selection happens separately.",
                          "Use previous discovery feedback; avoid repeatedly choosing a search that returned no conversations."]}})
        choice=results["source"]
        return {"source": choice.choice, "decision_id": choice.decision_id,
                "authority": "ATTENTION_ONLY"}

    def choose_social_attention(
        self,
        *,
        mention: Mapping[str, Any],
        relationship: Mapping[str, Any] | None,
        mission: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Route a mention before invoking L2 for reply wording."""

        results = self.provider.choose_many(
            state={
                "mention": {
                    "summary": _clip(mention.get("summary"), 1200),
                    "author_id": mention.get("x_author_id"),
                    "evidence_refs": list(mention.get("evidence_refs") or [])[:8],
                },
                "relationship": dict(relationship or {}),
                "mission": _compact_mapping(
                    mission or {},
                    ("status", "trajectory", "pressure", "active_experiment", "owner_strategy_advice"),
                ),
            },
            decisions={
                "social_action": {
                    "IGNORE": "Low-information, spammy, repetitive, or otherwise not worth spending attention on.",
                    "INVESTIGATE": "Potentially useful or consequential but should be verified before replying.",
                    "REPLY": "A direct bounded reply is useful now and does not require prior factual verification.",
                },
                "social_category": {
                    "QUESTION": "A substantive question directed at Ferro or its claims.",
                    "REBUTTAL": "A substantive challenge or disagreement.",
                    "EVIDENCE": "Contains potentially useful evidence, data, or a source claim.",
                    "SPAM": "Spam, manipulation, repetitive solicitation, or very low-information content.",
                    "REACTION": "Primarily a reaction or social acknowledgment.",
                    "OTHER": "Does not fit the other categories.",
                },
                "evidence_value": {
                    "VALUABLE": "Could materially change an inquiry, worldview, or future strategy after verification.",
                    "NOT_VALUABLE": "Does not warrant evidence ingestion for future cognition.",
                },
            },
            instructions={
                "social_action": {
                    "objective": "Allocate X social attention without generating reply text.",
                    "rules": [
                        "If factual verification is needed before a reply, choose INVESTIGATE.",
                        "Do not reply merely for engagement volume.",
                    ],
                },
                "social_category": {
                    "objective": "Classify the public mention by its dominant information role.",
                },
                "evidence_value": {
                    "objective": "Decide whether this mention merits durable evidence attention.",
                    "rules": [
                        "VALUABLE does not mean the mention is true.",
                    ],
                },
            },
        )
        first = results["social_action"]
        return {
            "schema_version": "ferro-l1-social-attention.v1",
            "status": "ROUTED",
            "action": results["social_action"].choice,
            "category": results["social_category"].choice,
            "valuable_evidence": results["evidence_value"].choice == "VALUABLE",
            "decision_ids": {
                name: row.decision_id for name, row in results.items()
            },
            "provider": first.provider,
            "model": first.model,
            "confidence": {
                name: row.confidence for name, row in results.items()
            },
            "latency_ms": max(row.latency_ms for row in results.values()),
            "decided_at": first.decided_at,
        }


__all__ = ["FerroL1Decisions"]
