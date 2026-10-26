"""Independent per-task scheduling on top of Agent Runtime's file plan store."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.wakeful import PlannedWake
from agent_runtime.wakeful.plan_store import FilePlanStore


class IndependentTaskScheduler:
    def __init__(self, state_root: Path) -> None:
        self.store = FilePlanStore(Path(state_root) / "application" / "task_plans")

    @staticmethod
    def _wake_id(task: str) -> str:
        return "content:" + str(task).strip().upper()

    def schedule(
        self,
        task: str,
        at: datetime,
        *,
        reason: str | None = None,
        relevant_refs=(),
        wake_id: str | None = None,
        preemptible: bool = True,
    ) -> dict:
        task = str(task).strip().upper()
        at = at.astimezone(timezone.utc)
        wake = PlannedWake(
            wake_id=wake_id or self._wake_id(task),
            at=at,
            reason=reason or task.lower().replace("_", " "),
            task=task,
            relevant_refs=tuple(str(x) for x in relevant_refs if str(x)),
            preemptible=preemptible,
        )
        existing = self.store.get(wake.wake_id)
        if existing is not None:
            # A deadline and an event may target the same task. Keep both
            # causes even when the earlier deadline wins the execution time.
            wake = PlannedWake(
                wake_id=wake.wake_id,
                at=min(existing.at, wake.at),
                reason=existing.reason if existing.at <= wake.at else wake.reason,
                task=task,
                relevant_refs=tuple(dict.fromkeys(
                    (*existing.relevant_refs, *wake.relevant_refs)
                )),
                cancel_conditions=tuple(dict.fromkeys(
                    (*existing.cancel_conditions, *wake.cancel_conditions)
                )),
                preemptible=existing.preemptible and wake.preemptible,
            )
            if wake == existing:
                return {
                    "status": "EARLIER_ALREADY_SCHEDULED",
                    "task": task,
                    "at": existing.at.isoformat(),
                    "wake_id": existing.wake_id,
                }
        self.store.schedule(wake)
        return {
            "status": "SCHEDULED",
            "task": task,
            "at": wake.at.isoformat(),
            "wake_id": wake.wake_id,
        }

    def cancel(self, task: str, *, wake_id: str | None = None) -> bool:
        return self.store.cancel(wake_id or self._wake_id(task))

    def pending(self, task: str | None = None):
        rows = self.store.list()
        if task is None:
            return rows
        task = str(task).strip().upper()
        return tuple(row for row in rows if row.task == task)

    def claim_due(self, task: str, now: datetime) -> PlannedWake | None:
        task = str(task).strip().upper()
        now = now.astimezone(timezone.utc)
        rows = [row for row in self.store.list() if row.task == task and row.at <= now]
        if not rows:
            return None
        wake = min(rows, key=lambda row: (row.at, row.wake_id))
        if not self.store.cancel(wake.wake_id):
            return None
        return wake


__all__ = ["IndependentTaskScheduler"]
