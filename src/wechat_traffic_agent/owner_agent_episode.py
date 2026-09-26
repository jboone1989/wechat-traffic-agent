from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_service import _extract_json
from .livingruntime_chatgpt import (
    LivingRuntimeChatGPTPendingError,
    resume_next_durable_cognition,
)


ToolExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class OwnerAgentDecision(BaseModel):
    """One bounded tool/action choice inside an owner-goal episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[
        "INSPECT_MISSION",
        "SEARCH_WORLD",
        "CHECK_X",
        "CHECK_OUTCOMES",
        "RUN_CONTENT_PIPELINE",
        "WAIT",
    ]
    query: str | None = Field(default=None, max_length=240)
    topic: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]{1,63}$",
    )
    wait_seconds: int | None = Field(default=None, ge=60, le=21600)
    rationale: str = Field(min_length=8, max_length=700)

    @model_validator(mode="after")
    def _validate_action_arguments(self) -> "OwnerAgentDecision":
        if self.action == "SEARCH_WORLD" and not str(self.query or "").strip():
            raise ValueError("SEARCH_WORLD requires query")
        if self.action == "RUN_CONTENT_PIPELINE" and not str(self.topic or "").strip():
            raise ValueError("RUN_CONTENT_PIPELINE requires a concrete topic")
        if self.action == "WAIT" and self.wait_seconds is None:
            object.__setattr__(self, "wait_seconds", 300)
        return self


class OwnerAgentEpisode:
    """Durable, bounded owner-goal controller.

    This is the control plane above the legacy content pipeline.  The model may
    inspect/search/check reality for a few steps, then choose a terminal action.
    Existing specialist pipelines remain bounded executors rather than deciding
    what the owner goal means.
    """

    SCHEMA = "ferro-owner-agent-episode.v1"
    MAX_STEPS = 3
    MAX_AGE = timedelta(minutes=30)
    TERMINAL = frozenset({"RUN_CONTENT_PIPELINE", "WAIT"})

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "owner_agent_episode.json"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    @staticmethod
    def _compact(value: Any, *, depth: int = 0) -> Any:
        if depth >= 4:
            return str(value)[:500]
        if isinstance(value, Mapping):
            return {
                str(k): OwnerAgentEpisode._compact(v, depth=depth + 1)
                for k, v in list(value.items())[:24]
            }
        if isinstance(value, (list, tuple)):
            return [OwnerAgentEpisode._compact(v, depth=depth + 1) for v in list(value)[:12]]
        if isinstance(value, str):
            return value[:1200]
        return value

    @staticmethod
    def _episode_id(goal_id: str, created_at: datetime) -> str:
        bucket = int(created_at.timestamp()) // 1800
        digest = sha256(f"{goal_id}|{bucket}".encode()).hexdigest()[:20]
        return "owner-episode:" + digest

    def _state_for(self, owner_goal: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        goal_id = str(owner_goal.get("goal_id") or "").strip()
        current = self._read()
        created_raw = str(current.get("created_at") or "")
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            created = now - self.MAX_AGE - timedelta(seconds=1)
        resumable = (
            current.get("schema_version") == self.SCHEMA
            and str(current.get("goal_id") or "") == goal_id
            and str(current.get("status") or "") in {"RUNNING", "DECIDED"}
            and created.tzinfo is not None
            and now - created.astimezone(timezone.utc) <= self.MAX_AGE
        )
        if resumable:
            return current
        state = {
            "schema_version": self.SCHEMA,
            "episode_id": self._episode_id(goal_id, now),
            "cycle_id": "v2-owner-" + sha256(goal_id.encode()).hexdigest()[:12]
            + "-" + str(int(now.timestamp())),
            "goal_id": goal_id,
            "goal": self._compact(dict(owner_goal)),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "status": "RUNNING",
            "steps": [],
            "decision": None,
            "outcome": None,
            "cognition_request_ids": [],
            "stale_request_ids": list(dict.fromkeys([
                *list(current.get("stale_request_ids") or ()),
                *list(current.get("cognition_request_ids") or ()),
                str(current.get("pending_request_id") or ""),
            ]))[-24:],
            "pending_request_id": None,
            "pending_step_index": None,
        }
        state["stale_request_ids"] = [
            request_id
            for request_id in state["stale_request_ids"]
            if str(request_id).strip()
        ]
        self._write(state)
        return state

    def running_for(self, goal_id: str) -> bool:
        state = self._read()
        return (
            state.get("schema_version") == self.SCHEMA
            and str(state.get("goal_id") or "") == str(goal_id or "")
            and str(state.get("status") or "") == "RUNNING"
        )

    def classify_request(self, goal_id: str, request_id: str) -> str:
        """Classify one durable cognition request against the current episode.

        PENDING may advance the current owner step. STALE is an already-used
        owner request and must never be injected into a later owner/content
        cognition phase. NOT_OWNER belongs to another durable workflow.
        """
        request_id = str(request_id or "").strip()
        state = self._read()
        if (
            not request_id
            or state.get("schema_version") != self.SCHEMA
        ):
            return "NOT_OWNER"
        same_goal = str(state.get("goal_id") or "") == str(goal_id or "")
        pending = str(state.get("pending_request_id") or "").strip()
        if pending and request_id == pending:
            if same_goal and str(state.get("status") or "") == "RUNNING":
                return "PENDING"
            return "STALE"
        known = {
            str(value)
            for value in (
                *list(state.get("cognition_request_ids") or ()),
                *list(state.get("stale_request_ids") or ()),
            )
            if str(value).strip()
        }
        if request_id in known:
            return "STALE"
        return "NOT_OWNER"

    @staticmethod
    def _tool_contracts() -> list[dict[str, Any]]:
        return [
            {
                "name": "INSPECT_MISSION",
                "side_effect": "read_only",
                "use": "Inspect current mission/goal metric, trajectory and constraints.",
            },
            {
                "name": "SEARCH_WORLD",
                "side_effect": "read_only_network",
                "arguments": {"query": "specific public-evidence query"},
                "use": "Search current public evidence before choosing a real action.",
            },
            {
                "name": "CHECK_X",
                "side_effect": "read_only_network",
                "use": "Observe current X mentions/metrics through the existing bounded connector.",
            },
            {
                "name": "CHECK_OUTCOMES",
                "side_effect": "read_only_reconciliation",
                "use": "Reconcile matured world/content outcomes when that evidence matters now.",
            },
            {
                "name": "RUN_CONTENT_PIPELINE",
                "side_effect": "bounded_existing_publication_gates",
                "arguments": {"topic": "optional concrete subject"},
                "use": "Delegate evidence gathering, drafting and gated publication to the existing content executor.",
            },
            {
                "name": "WAIT",
                "side_effect": "scheduler_only",
                "arguments": {"wait_seconds": "60..21600"},
                "use": "Wait only when evidence/action is genuinely time-dependent.",
            },
        ]

    async def _decide(
        self,
        *,
        runtime: Any,
        owner_goal: Mapping[str, Any],
        mission_context: Mapping[str, Any] | None,
        state: Mapping[str, Any],
    ) -> OwnerAgentDecision:
        prompt = (
            "OWNER_AGENT_EPISODE: Act as the bounded control core for the current owner goal. "
            "Choose the next useful tool/action, not a content topic by default. "
            "Prefer reality checks when key facts are missing; do not repeat a tool whose prior result already answers the need. "
            "Neural/drive signals are advisory inputs only and never override the owner goal or authorization. "
            "RUN_CONTENT_PIPELINE is one tool among several, not the mandatory path. "
            "Return JSON only.\n"
            + json.dumps(
                {
                    "owner_goal": self._compact(dict(owner_goal)),
                    "mission_context": self._compact(dict(mission_context or {})),
                    "prior_steps": self._compact(list(state.get("steps") or [])),
                    "tools": self._tool_contracts(),
                    "remaining_steps": max(0, self.MAX_STEPS - len(list(state.get("steps") or []))),
                },
                ensure_ascii=False,
            )
            + "\nSchema: "
            + json.dumps(OwnerAgentDecision.model_json_schema(), ensure_ascii=False)
        )
        raw = await runtime.cognitive_probe(prompt, max_tokens=1400)
        try:
            return OwnerAgentDecision.model_validate_json(_extract_json(raw))
        except Exception as exc:
            repaired = await runtime.cognitive_probe(
                prompt
                + "\nRepair only the JSON/action arguments. Do not add prose outside JSON. Error: "
                + str(exc)[:800]
                + "\nPrevious untrusted response: "
                + raw[:2000],
                max_tokens=1400,
            )
            return OwnerAgentDecision.model_validate_json(_extract_json(repaired))

    async def run(
        self,
        *,
        runtime: Any,
        owner_goal: Mapping[str, Any],
        mission_context: Mapping[str, Any] | None,
        tool_executor: ToolExecutor,
        now: datetime,
        resume_request_id: str | None = None,
    ) -> dict[str, Any]:
        now = now.astimezone(timezone.utc)
        state = self._state_for(owner_goal, now)
        if str(state.get("status") or "") == "DECIDED" and isinstance(state.get("decision"), dict):
            return {
                "status": "DECIDED",
                "episode_id": state["episode_id"],
                "cycle_id": state["cycle_id"],
                "decision": dict(state["decision"]),
                "steps": list(state.get("steps") or []),
                "resumed": True,
            }

        pending_request_id = str(state.get("pending_request_id") or "").strip()
        resume_request_id = str(resume_request_id or "").strip()
        if pending_request_id and pending_request_id != resume_request_id:
            # One episode step owns at most one durable cognition request.
            # Other wakes must converge on that request instead of creating
            # duplicate model calls against the same state snapshot.
            raise LivingRuntimeChatGPTPendingError(pending_request_id)

        while len(list(state.get("steps") or [])) < self.MAX_STEPS:
            try:
                decision_resume = (
                    resume_next_durable_cognition(resume_request_id)
                    if resume_request_id
                    else nullcontext()
                )
                with decision_resume:
                    decision = await self._decide(
                        runtime=runtime,
                        owner_goal=owner_goal,
                        mission_context=mission_context,
                        state=state,
                    )
            except LivingRuntimeChatGPTPendingError as exc:
                cognition_request_ids = list(
                    dict.fromkeys([
                        *list(state.get("cognition_request_ids") or ()),
                        exc.request_id,
                    ])
                )[-24:]
                state = {
                    **state,
                    "cognition_request_ids": cognition_request_ids,
                    "pending_request_id": exc.request_id,
                    "pending_step_index": len(list(state.get("steps") or [])),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._write(state)
                raise
            # The resume pointer belongs to exactly this owner decision attempt.
            # Later episode steps must create or resume their own request.
            resume_request_id = ""
            payload = decision.model_dump(mode="json")
            if decision.action in self.TERMINAL:
                state = {
                    **state,
                    "status": "DECIDED",
                    "decision": payload,
                    "pending_request_id": None,
                    "pending_step_index": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._write(state)
                return {
                    "status": "DECIDED",
                    "episode_id": state["episode_id"],
                    "cycle_id": state["cycle_id"],
                    "decision": payload,
                    "steps": list(state.get("steps") or []),
                    "resumed": False,
                }

            result = await tool_executor(payload)
            step = {
                "index": len(list(state.get("steps") or [])),
                "decision": payload,
                "result": self._compact(result),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            state = {
                **state,
                "status": "RUNNING",
                "steps": [*list(state.get("steps") or []), step],
                "pending_request_id": None,
                "pending_step_index": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(state)

        fallback = {
            "action": "WAIT",
            "query": None,
            "topic": None,
            "wait_seconds": 300,
            "rationale": "Bounded inspection budget exhausted without a justified action; retry after fresh state.",
        }
        state = {
            **state,
            "status": "DECIDED",
            "decision": fallback,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(state)
        return {
            "status": "DECIDED",
            "episode_id": state["episode_id"],
            "cycle_id": state["cycle_id"],
            "decision": fallback,
            "steps": list(state.get("steps") or []),
            "resumed": False,
        }

    def clear_pending_request(
        self,
        request_id: str,
        *,
        error_type: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Release one failed durable request without disturbing episode state."""
        request_id = str(request_id or "").strip()
        state = self._read()
        if (
            not request_id
            or state.get("schema_version") != self.SCHEMA
            or str(state.get("pending_request_id") or "") != request_id
        ):
            return False
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        state = {
            **state,
            "pending_request_id": None,
            "pending_step_index": None,
            "last_cognition_error": {
                "request_id": request_id,
                "error_type": str(error_type or "PROVIDER_ERROR"),
                "at": stamp.isoformat(),
            },
            "updated_at": stamp.isoformat(),
        }
        self._write(state)
        return True

    def complete(self, *, outcome: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        state = self._read()
        if state.get("schema_version") != self.SCHEMA:
            return {"status": "NO_EPISODE"}
        completed = {
            **state,
            "status": "COMPLETED",
            "outcome": self._compact(dict(outcome)),
            "pending_request_id": None,
            "pending_step_index": None,
            "updated_at": now.astimezone(timezone.utc).isoformat(),
            "completed_at": now.astimezone(timezone.utc).isoformat(),
        }
        self._write(completed)
        return {
            "status": "COMPLETED",
            "episode_id": completed.get("episode_id"),
        }


__all__ = ["OwnerAgentDecision", "OwnerAgentEpisode"]
