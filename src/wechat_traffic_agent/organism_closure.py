"""Composition root for the artificial-organism closure.

Keeps WakefulRuntime thin: existing neural/cortex machinery remains authoritative,
while this coordinator composes unified body state, drives, autonomous goals,
developmental history, delayed learning and the evidence-bound self model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def refresh_organism_closure(
    *,
    now: datetime,
    snapshot: Any,
    neural_life: Mapping[str, Any],
    personality: Mapping[str, Any],
    cortex: Mapping[str, Any],
    continuity: Mapping[str, Any],
    resource_pressure: str,
    organism_state: Any,
    drive_system: Any,
    developmental_history: Any,
    autonomous_life: Any,
    self_model: Any,
    world_learning: Any,
    active_user_goal_count: int = 0,
) -> dict[str, Any]:
    organism = organism_state.refresh(
        now=now,
        neural_life=neural_life,
        resource_pressure=resource_pressure,
        pending_plan_count=len(snapshot.pending_plans),
        last_cognition_at=snapshot.last_cognition_at,
        last_action_at=snapshot.last_action_at,
    )
    drives = drive_system.refresh(
        now=now,
        organism=organism,
        neural_life=neural_life,
        personality=personality,
    )
    development = developmental_history.projection()
    life = autonomous_life.tick(
        now=now,
        organism=organism,
        drives=drives,
        developmental_history=development,
    )
    goal = life.get("goal") if isinstance(life, Mapping) else None
    if isinstance(goal, Mapping) and life.get("status") == "GOAL_SCHEDULED":
        decision = drives.get("last_decision") if isinstance(drives.get("last_decision"), Mapping) else {}
        prior_milestones = development.get("milestones") if isinstance(development.get("milestones"), Mapping) else {}
        developmental_history.record(
            kind=(
                "FIRST_INDEPENDENT_GOAL"
                if "FIRST_INDEPENDENT_GOAL" not in prior_milestones
                else "INDEPENDENT_GOAL_CREATED"
            ),
            domain="autonomous_agency",
            outcome=0.0,
            evidence_refs=(
                str(goal.get("goal_id") or ""),
                str(decision.get("decision_id") or ""),
            ),
            now=now,
            metadata={"task": goal.get("task"), "origin": goal.get("origin")},
        )
        development = developmental_history.projection()

    self_projection = self_model.refresh(
        now=now,
        organism=organism,
        personality=personality,
        development=development,
        autonomous_life=autonomous_life.projection(),
        cortex=cortex,
        continuity=continuity,
        active_user_goal_count=active_user_goal_count,
    )
    return {
        "organism_state": organism,
        "drives": drives,
        "development": development,
        "autonomous_life": autonomous_life.projection(),
        "self_model": self_projection,
        "world_learning": world_learning.projection(),
        "goal": dict(goal) if isinstance(goal, Mapping) else None,
        "life_status": life.get("status") if isinstance(life, Mapping) else None,
        "world_learning_due": bool(world_learning.due(now)),
    }


__all__ = ["refresh_organism_closure"]
