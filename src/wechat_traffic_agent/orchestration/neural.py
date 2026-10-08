"""Wakeful neural task logic; the host injects runner components."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

class NeuralTaskMethods:
    """Neural capability handlers for the shared wakeful host."""

    def _fast_decision(
        self,
        now: datetime,
        *,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Run L1 and schedule exactly the bounded action it selected."""
        if self.wakeful is None:
            raise RuntimeError("FAST_DECISION requires a live wakeful runtime")
        from wechat_traffic_agent.fast_decision import FastDecisionError

        decide = getattr(self.wakeful, "fast_decide_next_action", None)
        schedule = getattr(self.wakeful, "schedule_fast_decision_action", None)
        if not callable(decide) or not callable(schedule):
            raise RuntimeError("wakeful runtime does not expose FAST_DECISION")
        try:
            decision = decide(
                now=now,
                relevant_refs=relevant_refs,
            )
            action = schedule(decision, now=now)
        except FastDecisionError as exc:
            repair = getattr(self.wakeful, "schedule_self_repair_followup", None)
            repair_result = (
                repair(now, seconds=0)
                if callable(repair)
                else None
            )
            return {
                "task": "FAST_DECISION",
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "self_repair": repair_result,
            }
        return {
            "task": "FAST_DECISION",
            "status": "OK",
            "decision": decision,
            "action": action,
        }

    def _neural_interoception(self, now: datetime) -> dict[str, Any]:
        fault_projection = (
            self.interoceptive_faults.projection(now=now)
            if getattr(self, "interoceptive_faults", None) is not None
            else {}
        )
        projection = self.wakeful.refresh_neural_life(now)
        return {
            "task": "NEURAL_INTEROCEPTION",
            "status": "OK" if projection.get("status") != "DISABLED" else "DISABLED",
            "neural_life": projection,
            "fault_interoception": self._compact_interoception(fault_projection),
        }

    def _neural_play(self, now: datetime) -> dict[str, Any]:
        try:
            return self._neural_play_impl(now)
        except Exception as exc:  # noqa: BLE001
            brain_lab = getattr(self.wakeful, "brain_lab", None)
            substrate = getattr(brain_lab, "substrate", None)
            recorder = getattr(substrate, "record_error", None)
            if callable(recorder):
                try:
                    recorder(exc, now)
                except Exception:
                    pass
            return {
                "task": "NEURAL_PLAY",
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "failure_isolated": True,
                "service_continuity_preserved": True,
                "active_game_stage": "FAILED_ISOLATED",
            }

    def _neural_play_impl(self, now: datetime) -> dict[str, Any]:
        # Keep one normal embodied session for organ-handoff semantics, then use
        # the already-admitted resource window for a bounded headless arena.
        result = self.wakeful.run_brain_lab(now, steps=12)
        arena = None
        graduation = None
        competence_graduation = None
        open_world_graduation = None
        adversarial_open_world_graduation = None
        active_stage = "FOUNDATION_GAMES_V1"
        brain_lab = getattr(self.wakeful, "brain_lab", None)
        owner_goal = self._active_owner_goal_context(now=now)
        owner_commitment_timeslice = owner_goal is not None

        def run_arena(**kwargs):
            if owner_commitment_timeslice:
                return {
                    "status": "DEFERRED_ACCELERATION_FOR_OWNER_COMMITMENT",
                    "owner_goal_id": str(owner_goal.get("goal_id") or ""),
                    "bounded_embodied_steps": 12,
                }
            return brain_lab.run_accelerated_arena(**kwargs)

        if brain_lab is not None and hasattr(brain_lab, "run_accelerated_arena"):
            graduation = brain_lab.game_intelligence.graduation_status()
            competence_graduation = brain_lab.foundation_competence()
            if not competence_graduation.get("graduated"):
                active_stage = "FOUNDATION_SKILL_REMEDIATION"
                deficits = tuple(
                    str(value).upper()
                    for value in competence_graduation.get("remediation_environments") or ()
                )
                arena = run_arena(
                    environments=deficits or brain_lab.ENVIRONMENTS,
                    sessions_per_environment=6,
                    base_steps=40,
                    now=now,
                )
            elif graduation.get("graduated"):
                harder = brain_lab.open_world_curriculum_environments(limit=4)
                if harder:
                    open_world_graduation = (
                        brain_lab.game_intelligence.open_world_graduation_status(harder)
                    )
                    if not open_world_graduation.get("graduated"):
                        active_stage = "HARDER_OPEN_WORLD_CURRICULUM"
                        arena = run_arena(
                            environments=harder,
                            sessions_per_environment=3,
                            base_steps=40,
                            now=now,
                        )
                    else:
                        adversarial = brain_lab.adversarial_open_world_environments(
                            limit=4
                        )
                        active_stage = "LONG_HORIZON_ADVERSARIAL_OPEN_WORLD"
                        adversarial_open_world_graduation = (
                            brain_lab.game_intelligence.adversarial_open_world_graduation_status(
                                adversarial
                            )
                        )
                        if adversarial_open_world_graduation.get("graduated"):
                            active_stage = "OPEN_ENDED_SELF_CHALLENGE"
                        arena = run_arena(
                            environments=adversarial,
                            sessions_per_environment=(
                                1
                                if adversarial_open_world_graduation.get("graduated")
                                else 3
                            ),
                            base_steps=64,
                            now=now,
                        )
                else:
                    active_stage = "FOUNDATION_MAINTENANCE_WAITING_FOR_OPEN_WORLD"
                    arena = run_arena(
                        sessions_per_environment=1,
                        base_steps=24,
                        now=now,
                    )
            else:
                arena = run_arena(
                    sessions_per_environment=6,
                    base_steps=32,
                    now=now,
                )
        return {
            "task": "NEURAL_PLAY",
            **result,
            "accelerated_arena": arena,
            "graduation": graduation,
            "competence_graduation": competence_graduation,
            "open_world_graduation": open_world_graduation,
            "adversarial_open_world_graduation": adversarial_open_world_graduation,
            "active_game_stage": active_stage,
            "owner_commitment_timeslice": owner_commitment_timeslice,
        }

    def _neural_dream(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_neural_dream(now)
        return {
            "task": "NEURAL_DREAM",
            **result,
        }

    def _neural_transfer(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_neural_transfer(now, training_steps=12)
        return {
            "task": "NEURAL_TRANSFER",
            **result,
        }

    def _neural_development(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_development_trial(now, steps=12)
        return {
            "task": "NEURAL_DEVELOPMENT",
            **result,
        }

    def _neural_development_transfer(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_development_transfer(now, training_steps=12)
        return {
            "task": "NEURAL_DEVELOPMENT_TRANSFER",
            **result,
        }

    def _neural_evolution_consolidation(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_evolution_consolidation(now, steps=24)
        return {
            "task": "NEURAL_EVOLUTION_CONSOLIDATION",
            **result,
        }
