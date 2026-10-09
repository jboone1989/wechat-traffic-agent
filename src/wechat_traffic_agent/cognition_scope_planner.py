"""Owner-scoped cognition input selection without memory or model access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _clip(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:max(0, limit - 14)] + "...[truncated]"


@dataclass(frozen=True)
class CognitionScopePlan:
    missions: dict
    query: str
    intents: list
    latest_plan: dict | None


class CognitionScopePlanner:
    @staticmethod
    def _missions(owner_scope, mission_state):
        if not owner_scope:
            return mission_state
        scoped_goal_id = str(owner_scope.get("goal_id") or "")
        scoped_mission_id = str(owner_scope.get("mission_id") or "")
        active = [
            row for row in list(mission_state.get("active") or [])
            if ((scoped_goal_id and str(row.get("goal_id") or "") == scoped_goal_id)
                or (scoped_mission_id and str(row.get("mission_id") or "") == scoped_mission_id))
        ]
        active_ids = {
            str(row.get("mission_id") or "")
            for row in active if str(row.get("mission_id") or "")
        }
        return {
            "active": active,
            "recent_experiments": [
                row for row in list(mission_state.get("recent_experiments") or [])
                if str(row.get("mission_id") or "") in active_ids
            ],
        }

    @staticmethod
    def _query(owner_scope, owner_mode, prompt, missions):
        # Owner's generic orchestration instructions are not retrieval evidence.
        # Anchor owner retrieval in objectives and bound mission descriptions.
        if owner_mode:
            parts = [
                str((owner_scope or {}).get("description") or ""),
                str((owner_scope or {}).get("topic") or ""),
                *[
                    " ".join((str(row.get("description") or ""),
                              str(row.get("metric") or "")))
                    for row in missions.get("active") or []
                ],
            ]
        else:
            parts = [
                str(prompt),
                *[str(row.get("description") or "")
                  for row in missions.get("active") or []],
            ]
        return " ".join(part for part in parts if part.strip())

    @staticmethod
    def _intents(owner_scope, owner_mode, mission_state, current_intent):
        intents = list(current_intent)
        goal_ids = set()
        if owner_mode:
            goal_ids = (
                {str(owner_scope.get("goal_id") or "")}
                if owner_scope and str(owner_scope.get("goal_id") or "")
                else {str(row.get("goal_id") or "")
                      for row in mission_state.get("active") or []
                      if str(row.get("goal_id") or "")}
            )
            intents = [
                {**dict(row), "authority": "OWNER_OBJECTIVE"}
                for row in intents if str(row.get("goal_id") or "") in goal_ids
                and str(row.get("kind") or "") == "GOAL"
            ]
        return intents, goal_ids

    @staticmethod
    def _plan(projection, owner_mode, goal_ids, compact_latest_plan):
        latest = compact_latest_plan(projection.get("latest_plan"))
        if owner_mode and isinstance(latest, Mapping):
            plan_goal = latest.get("goal") if isinstance(latest.get("goal"), Mapping) else {}
            plan_id = str(latest.get("owner_goal_id") or plan_goal.get("goal_id") or "")
            if goal_ids and plan_id not in goal_ids:
                latest = None
        return latest

    @staticmethod
    def _experiments(mission_state, owner_mode):
        if not owner_mode:
            return mission_state
        experiments = [
            {**dict(row),
             "strategy": _clip(row.get("strategy"), 800),
             "expected_effect": _clip(row.get("expected_effect"), 500)}
            for row in list(mission_state.get("recent_experiments") or [])[-2:]
            if isinstance(row, Mapping)
        ]
        return {**mission_state, "recent_experiments": experiments}

    def prepare(self, *, owner_scope, owner_mode, prompt, mission_state,
                current_intent, projection, compact_latest_plan):
        missions = self._missions(owner_scope, mission_state)
        query = self._query(owner_scope, owner_mode, prompt, missions)
        intents, goals = self._intents(owner_scope, owner_mode, missions, current_intent)
        latest = self._plan(projection, owner_mode, goals, compact_latest_plan)
        return CognitionScopePlan(
            self._experiments(missions, owner_mode), query, intents, latest,
        )
