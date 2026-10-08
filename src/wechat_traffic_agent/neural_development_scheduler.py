"""Bounded developmental scheduling without hidden access to the wakeful runtime.

The scheduler may enqueue tasks through the injected core, but it cannot
change owner authority or invent developmental evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class NeuralDevelopmentScheduler:
    def __init__(self, *, core, agency, transfer, evolution, planned_wake):
        self.core, self.agency = core, agency
        self.transfer, self.evolution = transfer, evolution
        self.planned_wake = planned_wake

    def _world(self, now: datetime, owner_control_active: bool):
        candidate_world = self.agency.next_candidate_world()
        architecture_revalidation_world = (
            self.transfer.architecture_revalidation_candidate(
                now=now
            )
        )
        if (
            not owner_control_active
            and architecture_revalidation_world is not None
            and self.transfer.pending(now=now) is None
        ):
            target_at = now + timedelta(seconds=10)
            if not any(
                wake.task == "NEURAL_DEVELOPMENT_TRANSFER"
                and wake.at <= target_at
                for wake in self.core.snapshot().pending_plans
            ):
                self.core.schedule(self.planned_wake(
                    wake_id="content:NEURAL_DEVELOPMENT_TRANSFER",
                    at=target_at,
                    reason=(
                        "revalidate selected developmental architecture against "
                        f"baseline on {architecture_revalidation_world.get('world_id')}"
                    ),
                    task="NEURAL_DEVELOPMENT_TRANSFER",
                    relevant_refs=(
                        str(architecture_revalidation_world.get("need_id") or ""),
                        str(architecture_revalidation_world.get("proposal_id") or ""),
                        str(architecture_revalidation_world.get("world_id") or ""),
                        "architecture-post-selection-revalidation",
                    ),
                    preemptible=False,
                ))
        elif (
            not owner_control_active
            and candidate_world is not None
            and int(candidate_world.get("trial_count") or 0) == 0
        ):
            target_at = now + timedelta(seconds=10)
            if not any(
                wake.task == "NEURAL_DEVELOPMENT" and wake.at <= target_at
                for wake in self.core.snapshot().pending_plans
            ):
                self.core.schedule(self.planned_wake(
                    wake_id="content:NEURAL_DEVELOPMENT",
                    at=target_at,
                    reason=f"test Ferro-born world {candidate_world.get('world_id')} for {candidate_world.get('need_id')}",
                    task="NEURAL_DEVELOPMENT",
                    relevant_refs=(
                        str(candidate_world.get("need_id") or ""),
                        str(candidate_world.get("proposal_id") or ""),
                        str(candidate_world.get("world_id") or ""),
                    ),
                    preemptible=True,
                ))
        elif (
            not owner_control_active
            and candidate_world is not None
            and self.transfer.pending() is None
        ):
            evaluation = (
                self.transfer.projection()
                .get("worlds", {})
                .get(str(candidate_world.get("world_id") or ""), {})
            )
            if not bool(evaluation.get("causal_claim_ready")):
                target_at = now + timedelta(seconds=10)
                if not any(
                    wake.task == "NEURAL_DEVELOPMENT_TRANSFER" and wake.at <= target_at
                    for wake in self.core.snapshot().pending_plans
                ):
                    self.core.schedule(self.planned_wake(
                        wake_id="content:NEURAL_DEVELOPMENT_TRANSFER",
                        at=target_at,
                        reason=(
                            f"prospectively test real-world transfer of "
                            f"{candidate_world.get('world_id')}"
                        ),
                        task="NEURAL_DEVELOPMENT_TRANSFER",
                        relevant_refs=(
                            str(candidate_world.get("need_id") or ""),
                            str(candidate_world.get("proposal_id") or ""),
                            str(candidate_world.get("world_id") or ""),
                        ),
                        preemptible=False,
                    ))


    def _consolidation(self, now: datetime, owner_control_active: bool):
        evolution = self.evolution.refresh(now=now)
        consolidation = self.evolution.next_consolidation()
        if consolidation is not None and not owner_control_active:
            target_at = now + timedelta(seconds=10)
            if not any(
                wake.task == "NEURAL_EVOLUTION_CONSOLIDATION"
                and wake.at <= target_at
                for wake in self.core.snapshot().pending_plans
            ):
                self.core.schedule(self.planned_wake(
                    wake_id="content:NEURAL_EVOLUTION_CONSOLIDATION",
                    at=target_at,
                    reason=(
                        f"consolidate adopted Ferro-born world "
                        f"{consolidation.get('world_id')} until real neural emergence "
                        "or bounded budget exhaustion"
                    ),
                    task="NEURAL_EVOLUTION_CONSOLIDATION",
                    relevant_refs=(
                        str(consolidation.get("cycle_id") or ""),
                        str(consolidation.get("need_id") or ""),
                        str(consolidation.get("proposal_id") or ""),
                        str(consolidation.get("world_id") or ""),
                    ),
                    preemptible=True,
                ))


    def refresh(self, now: datetime, owner_control_active: bool):
        self._world(now, owner_control_active)
        self._consolidation(now, owner_control_active)
