"""Project durable neural continuity and schedule newly due feedback.

This feature observes already-updated neural state and settlement due sets.
It never generates content, changes owner authority, or starts a worker.
"""
from __future__ import annotations

from datetime import timedelta


class NeuralContinuityCoordinator:
    def __init__(self, *, core, cortex, individuality, motor, personality,
                 social, dream, resource_regulator, organism_state,
                 drive_system, developmental_history, autonomous_life,
                 self_model, world_learning, active_goal_count,
                 ensure_goal_wakes, planned_wake, organism_closure):
        self.core, self.cortex, self.individuality = core, cortex, individuality
        self.motor, self.personality, self.social, self.dream = (
            motor, personality, social, dream,
        )
        self.regulator, self.organism_state = resource_regulator, organism_state
        self.drive_system, self.history = drive_system, developmental_history
        self.autonomous_life, self.self_model = autonomous_life, self_model
        self.world_learning = world_learning
        self.active_goal_count, self.ensure_goal_wakes = active_goal_count, ensure_goal_wakes
        self.planned_wake, self.organism_closure = planned_wake, organism_closure

    def _observe(self, now, projection):
        snapshot = self.core.snapshot()
        cortex = self.cortex.observe(
            neural_graph=dict(snapshot.neural_state),
            motor=self.motor.projection(),
            personality=self.personality.projection(),
            observed_at=now,
        )
        self.individuality.observe(
            now=now, personality=self.personality.projection(),
            social_state=dict(self.social.state),
            motor_state=dict(self.motor.state),
            dream_state=dict(self.dream.state),
            cortex=cortex, neural_graph=dict(snapshot.neural_state),
        )
        self.organism_closure(
            now=now, snapshot=snapshot, neural_life=projection,
            personality=self.personality.projection(), cortex=cortex,
            continuity=self.cortex.projection(),
            resource_pressure=self.regulator.pressure(now),
            organism_state=self.organism_state, drive_system=self.drive_system,
            developmental_history=self.history,
            autonomous_life=self.autonomous_life, self_model=self.self_model,
            world_learning=self.world_learning,
            active_user_goal_count=self.active_goal_count(),
        )

    def _due(self, now, previous_signature):
        signature = tuple(
            (str(row.get("action_id") or ""), str(row.get("window") or ""),
             str(row.get("due_at") or ""))
            for row in self.world_learning.due(now)
        )
        if signature and signature != previous_signature:
            if not any(
                wake.task == "CHECK_OUTCOMES" and wake.at <= now + timedelta(minutes=5)
                for wake in self.core.snapshot().pending_plans
            ):
                self.core.schedule(self.planned_wake(
                    wake_id="content:CHECK_OUTCOMES", at=now + timedelta(seconds=10),
                    reason="settle newly matured delayed world-learning outcomes",
                    task="CHECK_OUTCOMES", preemptible=False,
                ))
        return signature

    def refresh(self, *, now, projection, owner_control_active, previous_signature):
        self._observe(now, projection)
        if not owner_control_active:
            self.ensure_goal_wakes(now)
        signature = self._due(now, previous_signature)
        persist = getattr(self.core, "_persist", None)
        if callable(persist):
            persist()
        return signature
