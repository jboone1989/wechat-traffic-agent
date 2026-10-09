"""Bounded neural action permission, personality refresh, and motor scheduling.

The owner-goal gate suppresses neural external control, not sensing.
Dependencies are injected ports, not the entire wakeful runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta


class NeuralAuthorityCoordinator:
    def __init__(self, *, core, life, brain_cortex, personality, brain_lab,
                 social, motor, regulator, planned_wake, x_enabled: bool):
        self.core, self.life = core, life
        self.brain_cortex, self.personality = brain_cortex, personality
        self.brain_lab, self.social = brain_lab, social
        self.motor, self.regulator, self.planned_wake = motor, regulator, planned_wake
        self.x_enabled = x_enabled

    def _recommend(self, now, projection, owner_control_active):
        recommendation = self.life.authority_recommendation(now)
        if recommendation is not None and not owner_control_active:
            task = str(recommendation["task"])
            if task == "CHECK_X" and not self.x_enabled:
                task = "CONTENT_COGNITION"
                recommendation = {**recommendation, "task": task}
            existing = [
                wake for wake in self.core.snapshot().pending_plans
                if wake.task == task and wake.at <= now + timedelta(minutes=15)
            ]
            if not existing:
                self.core.schedule(self.planned_wake(
                    wake_id=f"neural:{str(recommendation['intention']).lower()}",
                    at=now + timedelta(seconds=5),
                    reason=str(recommendation["reason"]),
                    task=task,
                    relevant_refs=(f"neural-intention:{recommendation['intention']}",),
                    preemptible=True,
                ))
                self.life.mark_authority_action(now, recommendation)
                projection = self.life.projection()
        return projection

    def _motor(self, now, projection, owner_control_active):
        scoreboard = self.brain_cortex.projection().get("scoreboard") or {}
        executed = sum(int((row or {}).get("executed") or 0) for row in scoreboard.values())
        neural_executed = int((scoreboard.get("NEURAL") or {}).get("executed") or 0)
        authority_fraction = (neural_executed / executed) if executed else 0.5
        self.personality.refresh(
            now=now,
            neural_life=projection,
            brain_lab=self.brain_lab.projection(),
            social=self.social.projection(),
            neural_authority_fraction=authority_fraction,
        )
        intention = (projection.get("intention") or {}).get("name")
        pressure = self.regulator.pressure(now)
        if owner_control_active:
            motor = {
                "status": "INHIBITED_OWNER_GOAL_CONTROL",
                "task": None,
            }
        else:
            motor = (
                self.motor.select(neural_life=projection, now=now)
                if intention != "REST" and pressure not in {"CONSERVE", "CRITICAL", "UNKNOWN"}
                else {"status": "INHIBITED", "task": None}
            )
        motor_task = str(motor.get("task") or "")
        if motor_task:
            target_at = now + timedelta(seconds=5)
            existing = next(
                (
                    wake for wake in self.core.snapshot().pending_plans
                    if wake.wake_id == f"motor:{motor.get('decision_id')}"
                ),
                None,
            )
            if existing is None:
                self.core.schedule(self.planned_wake(
                    wake_id=f"motor:{motor.get('decision_id')}",
                    at=target_at,
                    reason=f"{motor.get('authority') or 'NEURAL_MOTOR'} selected {motor_task} via {motor.get('motor_id') or 'motor unit'}",
                    task=motor_task,
                    relevant_refs=(
                        str(motor.get("decision_id") or ""),
                        str(motor.get("motor_id") or ""),
                    ),
                    preemptible=True,
                ))

    def refresh(self, now, projection, owner_control_active):
        projection = self._recommend(now, projection, owner_control_active)
        self._motor(now, projection, owner_control_active)
        return projection
