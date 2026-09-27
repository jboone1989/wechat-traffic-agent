"""Bounded autonomous life loop for Ferro.

This layer turns drive decisions into durable goals.  It cannot execute arbitrary
commands, bypass admission, change security boundaries, or create unbounded work.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-autonomous-life.v1"
MAX_GOALS = 256
ALLOWED_TASKS = frozenset({
    "RESOURCE_REVIEW",
    "OBSERVE_WORLD",
    "REFLECT",
    "CHECK_X",
    "CONTENT_COGNITION",
    "NEURAL_PLAY",
    "NEURAL_DEVELOPMENT",
})
ORIGINS = frozenset({
    "HUMAN",
    "SURVIVAL",
    "CURIOSITY",
    "DEVELOPMENT",
    "SOCIAL",
    "OPPORTUNITY",
    "SELF_MAINTENANCE",
    "EXPERIMENT",
})


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class AutonomousLifeLoop:
    """Convert homeostatic tension into one bounded autonomous goal at a time."""

    MIN_SCORE = 0.34
    MIN_INTERVAL = timedelta(minutes=10)

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "autonomous_life.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "goals": [],
            "last_generated_at": None,
        }

    @staticmethod
    def _origin_for_drive(drive: str) -> str:
        return {
            "survival": "SURVIVAL",
            "resource": "SURVIVAL",
            "curiosity": "CURIOSITY",
            "mastery": "DEVELOPMENT",
            "social": "SOCIAL",
            "creation": "OPPORTUNITY",
            "propagation": "EXPERIMENT",
        }.get(str(drive), "EXPERIMENT")

    @staticmethod
    def _ttl(task: str) -> timedelta:
        return {
            "RESOURCE_REVIEW": timedelta(hours=2),
            "OBSERVE_WORLD": timedelta(hours=4),
            "REFLECT": timedelta(hours=8),
            "CHECK_X": timedelta(hours=2),
            "CONTENT_COGNITION": timedelta(hours=12),
            "NEURAL_PLAY": timedelta(hours=12),
            "NEURAL_DEVELOPMENT": timedelta(days=1),
        }.get(task, timedelta(hours=6))

    def _expire(self, now: datetime) -> list[dict[str, Any]]:
        goals = []
        expired: list[dict[str, Any]] = []
        for raw in self.state.get("goals") or ():
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            if row.get("status") in {"OPEN", "SCHEDULED"}:
                try:
                    expires = datetime.fromisoformat(str(row.get("expires_at")).replace("Z", "+00:00"))
                except ValueError:
                    expires = now
                if expires <= now:
                    row["status"] = "EXPIRED"
                    row["closed_at"] = now.isoformat()
                    row["close_reason"] = "TTL_EXPIRED"
                    expired.append(dict(row))
            goals.append(row)
        self.state["goals"] = goals[-MAX_GOALS:]
        return expired

    def tick(
        self,
        *,
        now: datetime,
        organism: Mapping[str, Any],
        drives: Mapping[str, Any],
        developmental_history: Mapping[str, Any],
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("autonomous life tick requires timezone-aware time")
        now = now.astimezone(timezone.utc)
        expired_goals = self._expire(now)
        expiration = {"expired_goals": expired_goals}
        last_raw = self.state.get("last_generated_at")
        if last_raw:
            try:
                last = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
            except ValueError:
                last = None
            if last is not None and now - last < self.MIN_INTERVAL:
                self.state["updated_at"] = now.isoformat()
                _write(self.path, self.state)
                return {"status": "COOLDOWN", **expiration, **self.projection()}

        decision = drives.get("last_decision") if isinstance(drives.get("last_decision"), Mapping) else {}
        winner = decision.get("winner") if isinstance(decision.get("winner"), Mapping) else {}
        task = str(winner.get("task") or "")
        drive = str(winner.get("drive") or "")
        score = float(winner.get("arbitration_score") or 0.0)
        expired_same_task = [
            row
            for row in expired_goals
            if str(row.get("task") or "") == task and task
        ]
        if expired_same_task:
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return {
                "status": "GOAL_EXPIRED",
                "goal": dict(expired_same_task[-1]),
                **expiration,
                **self.projection(),
            }
        if task == "REST":
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return {
                "status": "REST",
                "reason": winner.get("reason"),
                **expiration,
                **self.projection(),
            }
        if task not in ALLOWED_TASKS or score < self.MIN_SCORE:
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return {"status": "NO_GOAL", **expiration, **self.projection()}

        active = [
            row for row in self.state.get("goals") or ()
            if isinstance(row, Mapping)
            and row.get("status") in {"OPEN", "SCHEDULED"}
            and row.get("task") == task
        ]
        if active:
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return {
                "status": "EXISTING_GOAL",
                "goal": dict(active[-1]),
                **expiration,
                **self.projection(),
            }

        origin = self._origin_for_drive(drive)
        if origin not in ORIGINS:
            raise ValueError("invalid goal origin")
        evidence = [
            str(decision.get("decision_id") or ""),
            "organism-state:" + str(organism.get("updated_at") or ""),
        ]
        open_needs = developmental_history.get("open_developmental_needs")
        if origin == "DEVELOPMENT" and isinstance(open_needs, list) and open_needs:
            evidence.append(str(open_needs[0].get("transition_id") or ""))
        evidence = [value for value in evidence if value and not value.endswith(":")]

        goal_id = "autonomous-goal:" + sha256(
            f"{decision.get('decision_id')}|{task}|{origin}".encode()
        ).hexdigest()[:20]
        goal = {
            "goal_id": goal_id,
            "created_at": now.isoformat(),
            "expires_at": (now + self._ttl(task)).isoformat(),
            "origin": origin,
            "drive": drive,
            "task": task,
            "reason": str(winner.get("reason") or "")[:500],
            "evidence_refs": evidence,
            "success_evidence": {
                "required": True,
                "description": f"task-specific world or runtime evidence for {task}",
            },
            "resource_budget": {
                "max_attempts": 1 if task in {"CONTENT_COGNITION", "NEURAL_DEVELOPMENT"} else 2,
                "max_llm_calls": 1 if task in {"CONTENT_COGNITION", "REFLECT"} else 0,
            },
            "status": "SCHEDULED",
            "bypasses_admission": False,
        }
        goals = list(self.state.get("goals") or ())
        goals.append(goal)
        self.state.update({
            "updated_at": now.isoformat(),
            "last_generated_at": now.isoformat(),
            "goals": goals[-MAX_GOALS:],
        })
        _write(self.path, self.state)
        return {
            "status": "GOAL_SCHEDULED",
            "goal": goal,
            **expiration,
            **self.projection(),
        }

    def close_goal(
        self,
        *,
        goal_id: str,
        status: str,
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        status = str(status).upper()
        if status not in {"EXECUTED", "SATISFIED", "FAILED", "CANCELLED"}:
            raise ValueError("invalid terminal goal status")
        goals = list(self.state.get("goals") or ())
        for index, raw in enumerate(goals):
            if not isinstance(raw, Mapping) or raw.get("goal_id") != goal_id:
                continue
            row = dict(raw)
            if row.get("status") in {"EXECUTED", "SATISFIED", "FAILED", "CANCELLED", "EXPIRED"}:
                return row
            row["status"] = status
            row["closed_at"] = now.isoformat()
            row["close_evidence_ref"] = str(evidence_ref)
            goals[index] = row
            self.state["goals"] = goals[-MAX_GOALS:]
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return row
        raise KeyError("unknown autonomous goal")

    def projection(self) -> dict[str, Any]:
        goals = [dict(row) for row in self.state.get("goals") or () if isinstance(row, Mapping)]
        active = [row for row in goals if row.get("status") in {"OPEN", "SCHEDULED"}]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "goal_count": len(goals),
            "active_goal_count": len(active),
            "active_goals": active[-16:],
            "recent_goals": goals[-16:],
            "unbounded_self_generated_work": False,
            "arbitrary_command_authority": False,
        }


__all__ = ["ALLOWED_TASKS", "AutonomousLifeLoop", "ORIGINS", "SCHEMA"]
