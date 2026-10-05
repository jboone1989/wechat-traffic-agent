from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_service import _extract_json


ToolExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class OwnerAgentDecision(BaseModel):
    """One bounded tool/action choice inside an owner-goal episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[
        "INSPECT_MISSION",
        "SEARCH_WORLD",
        "CHECK_X",
        "ENGAGE_X",
        "CHECK_OUTCOMES",
        "RUN_CONTENT_PIPELINE",
        "WAIT",
    ]
    publication_surface: Literal["AUTO", "WEBSITE", "X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"] = "AUTO"
    query: str | None = Field(default=None, max_length=240)
    topic: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]{1,63}$",
    )
    wait_seconds: int | None = Field(default=None, ge=60, le=21600)
    # The provider already bounds the response. A valid action must not be
    # rejected because its explanatory text crosses an arbitrary UI budget.
    rationale: str = Field(min_length=8)
    wait_for: str | None = None
    next_action: Literal["INSPECT_MISSION", "SEARCH_WORLD", "CHECK_X", "ENGAGE_X", "CHECK_OUTCOMES", "RUN_CONTENT_PIPELINE"] | None = None

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
        self.episodes_dir = self.state_root / "application" / "owner_agent_episodes"

    def _goal_path(self, goal_id: str) -> Path:
        digest = sha256(str(goal_id or "").strip().encode()).hexdigest()[:24]
        return self.episodes_dir / f"{digest}.json"

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _read(self, goal_id: str | None = None) -> dict[str, Any]:
        if goal_id is None:
            return self._read_path(self.path)
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return {}
        return self._read_path(self._goal_path(goal_id))

    def _write(self, value: Mapping[str, Any]) -> None:
        payload = dict(value)
        targets = [self.path]
        goal_id = str(payload.get("goal_id") or "").strip()
        if goal_id:
            targets.append(self._goal_path(goal_id))
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, target)

    def _all_states(self) -> list[dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        try:
            paths = sorted(self.episodes_dir.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            state = self._read_path(path)
            if state:
                states[str(state.get("goal_id") or path.name)] = state
        return list(states.values())

    def projection_for(self, goal_id: str) -> dict[str, Any]:
        return self._read(goal_id)

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
    def _decision_mission_context(mission: Mapping[str, Any]) -> dict[str, Any]:
        """Project only facts needed to choose the next owner-goal action."""
        roadmap = mission.get("roadmap") if isinstance(mission.get("roadmap"), Mapping) else {}
        x_execution = (
            mission.get("x_execution")
            if isinstance(mission.get("x_execution"), Mapping)
            else {}
        )
        x_status = (
            x_execution.get("status")
            if isinstance(x_execution.get("status"), Mapping)
            else {}
        )
        return {
            key: mission.get(key)
            for key in (
                "mission_id",
                "goal_id",
                "description",
                "metric",
                "baseline",
                "target",
                "current",
                "trajectory",
                "pressure",
                "deadline",
                "constraints",
            )
            if mission.get(key) is not None
        } | {
            "roadmap": {
                "roadmap_id": roadmap.get("roadmap_id"),
                "version": roadmap.get("version"),
                "steps": [
                    {
                        key: row.get(key)
                        for key in ("step_id", "title", "status")
                        if row.get(key) is not None
                    }
                    for row in list(roadmap.get("steps") or ())[:4]
                    if isinstance(row, Mapping)
                ],
            },
            "x_execution": {
                "configured": x_execution.get("configured"),
                "transport": x_execution.get("transport"),
                "selection_authority": x_execution.get("selection_authority"),
                "available_actions": list(x_execution.get("available_actions") or ()),
                "profile": x_execution.get("profile"),
                "status": {
                    key: x_status.get(key)
                    for key in ("status", "last_error", "last_success_at")
                    if x_status.get(key) is not None
                },
                "recent_publication_receipts": list(
                    x_execution.get("recent_publication_receipts") or ()
                )[-3:],
            },
        }

    @staticmethod
    def _decision_mind_context(current_mind: Mapping[str, Any]) -> dict[str, Any]:
        """Expose topic/learning state, not the full ContentMind projection."""
        return {
            "mind_tick": current_mind.get("mind_tick"),
            "interests": [
                {
                    key: row.get(key)
                    for key in ("name", "salience", "preference", "familiarity")
                    if row.get(key) is not None
                }
                for row in list(current_mind.get("interests") or ())[:6]
                if isinstance(row, Mapping)
            ],
            "open_inquiries": [
                {
                    key: row.get(key)
                    for key in ("inquiry_id", "question", "origin", "status")
                    if row.get(key) is not None
                }
                for row in list(current_mind.get("open_inquiries") or ())[:6]
                if isinstance(row, Mapping)
            ],
            "beliefs": [
                {
                    key: row.get(key)
                    for key in ("proposition", "stance", "confidence")
                    if row.get(key) is not None
                }
                for row in list(current_mind.get("beliefs") or ())[:4]
                if isinstance(row, Mapping)
            ],
            "resource_awareness": (
                (current_mind.get("resource_awareness") or {}).get("current")
                if isinstance(current_mind.get("resource_awareness"), Mapping)
                else None
            ),
            "strategy_learning": {
                "outcome_count": (
                    (current_mind.get("strategy_learning") or {}).get("outcome_count")
                    if isinstance(current_mind.get("strategy_learning"), Mapping)
                    else 0
                ),
                "topics": list(
                    ((current_mind.get("strategy_learning") or {}).get("topics") or ())
                    if isinstance(current_mind.get("strategy_learning"), Mapping)
                    else ()
                )[-8:],
            },
        }

    @staticmethod
    def _decision_previous_episode(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        decision = value.get("decision") if isinstance(value.get("decision"), Mapping) else {}
        outcome = value.get("outcome") if isinstance(value.get("outcome"), Mapping) else {}
        return {
            "episode_id": value.get("episode_id"),
            "status": value.get("status"),
            "updated_at": value.get("updated_at"),
            "decision": {
                key: decision.get(key)
                for key in ("action", "topic", "publication_surface", "wait_for", "next_action")
                if decision.get(key) is not None
            },
            "outcome": {
                key: outcome.get(key)
                for key in ("status", "error_type", "message")
                if outcome.get(key) is not None
            },
        }

    @staticmethod
    def _decision_steps(value: Any) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for row in list(value or ())[-3:]:
            if not isinstance(row, Mapping):
                continue
            decision = row.get("decision") if isinstance(row.get("decision"), Mapping) else {}
            result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
            items = [
                {
                    key: item.get(key)
                    for key in ("evidence_id", "title", "url")
                    if item.get(key) is not None
                }
                for item in list(result.get("items") or ())[:3]
                if isinstance(item, Mapping)
            ]
            projected.append({
                "index": row.get("index"),
                "decision": {
                    key: decision.get(key)
                    for key in ("action", "query", "topic", "publication_surface")
                    if decision.get(key) is not None
                },
                "result": {
                    key: result.get(key)
                    for key in ("status", "query", "new_mentions", "error_type", "message")
                    if result.get(key) is not None
                } | ({"items": items} if items else {}),
            })
        return projected

    @staticmethod
    def _decision_memory_context(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "node_id": row.get("node_id"),
                "type": row.get("type"),
                "status": row.get("status"),
                "topics": list(row.get("topics") or ())[:3],
                "text": str(row.get("text") or "")[:320],
            }
            for row in rows[:3]
            if isinstance(row, Mapping)
        ]

    @staticmethod
    def _episode_id(goal_id: str, created_at: datetime) -> str:
        bucket = int(created_at.timestamp()) // 1800
        digest = sha256(f"{goal_id}|{bucket}".encode()).hexdigest()[:20]
        return "owner-episode:" + digest

    def _state_for(self, owner_goal: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        goal_id = str(owner_goal.get("goal_id") or "").strip()
        commitment_key = str(
            owner_goal.get("owner_proposal_id")
            or owner_goal.get("commitment_id")
            or goal_id
        ).strip()
        current = self._read(goal_id)
        created_raw = str(current.get("created_at") or "")
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            created = now - self.MAX_AGE - timedelta(seconds=1)
        resumable = (
            current.get("schema_version") == self.SCHEMA
            and str(current.get("goal_id") or "") == goal_id
            and str(
                current.get("commitment_key")
                or current.get("goal_id")
                or ""
            )
            == commitment_key
            and str(current.get("status") or "") in {"RUNNING", "DECIDED"}
            and created.tzinfo is not None
            and now - created.astimezone(timezone.utc) <= self.MAX_AGE
        )
        if resumable:
            return current
        previous_episode = None
        wait_streak = 0
        if current and str(current.get("commitment_key") or current.get("goal_id") or "") == commitment_key:
            previous_episode = {
                key: current.get(key) for key in
                ("episode_id", "cycle_id", "status", "decision", "outcome", "steps", "updated_at", "cognition_blocker")
                if current.get(key) is not None
            }
            if (current.get("outcome") or {}).get("status") == "WAIT_SCHEDULED":
                wait_streak = int(current.get("consecutive_waits") or 0) + 1
            # Keep the complete prior receipt before replacing the active projection.
            archive = self.episodes_dir / "history" / sha256(goal_id.encode()).hexdigest()[:24]
            archive.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(current, ensure_ascii=False, sort_keys=True)
            receipt = archive / (sha256(serialized.encode()).hexdigest() + ".json")
            if not receipt.exists():
                receipt.write_text(serialized + "\n", encoding="utf-8")
        state = {
            "schema_version": self.SCHEMA,
            "episode_id": self._episode_id(goal_id, now),
            "cycle_id": "v2-owner-" + sha256(goal_id.encode()).hexdigest()[:12]
            + "-" + str(int(now.timestamp())),
            "goal_id": goal_id,
            "commitment_key": commitment_key,
            "goal": self._compact(dict(owner_goal)),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "status": "RUNNING",
            "steps": [],
            "decision": None,
            "outcome": None,
            "previous_episode": previous_episode,
            "consecutive_waits": wait_streak,
        }
        self._write(state)
        return state

    def running_for(self, goal_id: str) -> bool:
        state = self._read(goal_id)
        return (
            state.get("schema_version") == self.SCHEMA
            and str(state.get("goal_id") or "") == str(goal_id or "")
            and str(state.get("status") or "") == "RUNNING"
        )

    def cognition_blocker_for(
        self,
        goal_id: str,
        *,
        now: datetime,
    ) -> dict[str, Any] | None:
        state = self._read(goal_id)
        blocker = (
            state.get("cognition_blocker")
            if isinstance(state.get("cognition_blocker"), dict)
            else None
        )
        if (
            state.get("schema_version") != self.SCHEMA
            or str(state.get("goal_id") or "") != str(goal_id or "")
            or str(state.get("status") or "") != "RUNNING"
            or blocker is None
        ):
            return None
        retry_raw = str(blocker.get("retry_at") or "")
        try:
            retry_at = datetime.fromisoformat(retry_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if retry_at.tzinfo is None:
            return None
        if now.astimezone(timezone.utc) >= retry_at.astimezone(timezone.utc):
            return None
        return dict(blocker)

    def mark_cognition_blocked(
        self,
        *,
        goal_id: str,
        error_type: str,
        message: str,
        retry_at: datetime,
        now: datetime,
    ) -> dict[str, Any]:
        state = self._read(goal_id)
        if (
            state.get("schema_version") != self.SCHEMA
            or str(state.get("goal_id") or "") != str(goal_id or "")
        ):
            return {"status": "NO_EPISODE"}
        stamp = now.astimezone(timezone.utc)
        retry_at = retry_at.astimezone(timezone.utc)
        blocker = {
            "error_type": str(error_type or "PROVIDER_ERROR"),
            "message": str(message or "")[:300],
            "blocked_at": stamp.isoformat(),
            "retry_at": retry_at.isoformat(),
            "retryable": True,
        }
        state = {
            **state,
            "status": "RUNNING",
            "cognition_blocker": blocker,
            "last_cognition_error": blocker,
            "updated_at": stamp.isoformat(),
        }
        self._write(state)
        return {
            "status": "OWNER_COGNITION_PROVIDER_UNAVAILABLE",
            **blocker,
        }


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
                "name": "ENGAGE_X",
                "side_effect": "bounded_existing_x_reply_gates",
                "use": "Discover actual public discussions and let Ferro choose useful replies through authenticated Jev pages. Return verified reply receipts or explicit no-reply/blocker feedback. No engagement quota and no forced reply.",
            },
            {
                "name": "CHECK_OUTCOMES",
                "side_effect": "read_only_reconciliation",
                "use": "Reconcile matured world/content outcomes when that evidence matters now.",
            },
            {
                "name": "RUN_CONTENT_PIPELINE",
                "side_effect": "bounded_existing_publication_gates",
                "arguments": {"topic": "concrete subject", "publication_surface": "AUTO, WEBSITE, X_SHORT_POST, X_LONG_POST, or X_ARTICLE"},
                "use": "Gather evidence and cognition, then publish on the requested surface through existing safety and publication gates. All X surfaces proceed directly to an original X draft without requiring a website article. Native X_ARTICLE includes an original illustrated cover.",
            },
            {
                "name": "WAIT",
                "side_effect": "scheduler_only",
                "arguments": {"wait_seconds": "60..21600"},
                "use": "Wait only when evidence/action is genuinely time-dependent.",
            },
        ]

    def _related_memory(
        self,
        *,
        owner_goal: Mapping[str, Any],
        mission_context: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        from .memory_service import MemoryService

        context = dict(mission_context or {})
        mind = context.get("current_mind") if isinstance(context.get("current_mind"), Mapping) else {}
        interests = list(mind.get("interests") or [])[:6]
        inquiries = list(mind.get("open_inquiries") or [])[:6]
        query = " ".join(
            part
            for part in [
                str(owner_goal.get("description") or "").strip(),
                str(owner_goal.get("parent_goal_description") or "").strip(),
                *(str(row.get("name") or "").strip() for row in interests if isinstance(row, Mapping)),
                *(str(row.get("question") or "").strip() for row in inquiries if isinstance(row, Mapping)),
            ]
            if part
        )
        if not query:
            return []
        return MemoryService(self.state_root).search(query, limit=6, node_types=("memory",))

    async def _decide(
        self,
        *,
        runtime: Any,
        owner_goal: Mapping[str, Any],
        mission_context: Mapping[str, Any] | None,
        related_memory: list[dict[str, Any]],
        state: Mapping[str, Any],
    ) -> OwnerAgentDecision:
        from .editorial_policy import EDITORIAL_SCOPE_INSTRUCTION
        from .x_social import XHistoryLedger
        from .learning_research import LEARNING_INSTRUCTION, read_research_state, research_trajectory, parked_inquiries

        mission = dict(mission_context or {})
        current_mind = mission.get("current_mind") if isinstance(mission.get("current_mind"), Mapping) else {}
        recent_posts = [
            {key: row.get(key) for key in ("recorded_at", "expression_kind", "text", "x_url")}
            for row in XHistoryLedger(self.state_root).recent(limit=100)
            if row.get("event_type") == "EXPRESSION" and row.get("x_post_id") and row.get("x_url")
        ][-4:]
        prompt = (
            "OWNER_AGENT_EPISODE: Act as the bounded control core for the current owner goal. "
            + EDITORIAL_SCOPE_INSTRUCTION + " "
            + LEARNING_INSTRUCTION + " "
            "Choose the next useful tool/action, not a content topic by default. "
            "Prefer reality checks when key facts are missing; do not repeat a tool whose prior result already answers the need. "
            "Treat a specific activated owner proposal as the immediate commitment under its broader parent goal. "
            "A commitment is not progressing merely because it was analyzed: prefer one permitted bounded world-changing action or a concrete blocker receipt. "
            "Use WAIT only when no currently permitted tool can materially advance the commitment or when the next evidence is genuinely time-dependent. "
            "Neural/drive signals are advisory inputs only and never override the owner goal or authorization. "
            "RUN_CONTENT_PIPELINE is one tool among several, not the mandatory path. "
            "When the owner commitment explicitly requires an original X post and a real POSTED receipt, choose X_SHORT_POST for a concise thought, X_LONG_POST for useful additional context, or X_ARTICLE for a structured illustrated native Article. "
            "AUTO is for autonomous content selection; WEBSITE is for a website article. Choosing an X surface is an execution request, never permission to bypass evidence, X safety, or publication authority. "
            "Current x_execution transport/capability evidence supersedes old roadmap/API assumptions; X actions use Jev pages only. "
            "Previous optional 20/200 advice is not a quota. When owner_goal.daily_posting_contract is present, the user's newer temporary minimum is mandatory until explicitly disabled. "
            "For that daily contract, research freely within cryptocurrency, AI and finance, then choose an original X_SHORT_POST, X_LONG_POST or X_ARTICLE; do not choose WEBSITE or AUTO instead. "
            "Use research_trajectory to recognize the same unanswered question across different topic names. "
            "After repeated unfinished investigations, seek a specific new artifact or a useful adjacent question. "
            "A parked inquiry needs its resume_when evidence condition; continue learning elsewhere while it is unavailable. "
            "Explanation, worked cases and comparisons can be useful without proving a price forecast. "
            "Do not make an exact benchmark number or a finished calculation a prerequisite for RUN_CONTENT_PIPELINE. "
            "A source-supported account of why a method works, how alternatives differ, or what an intermediate inference rules out "
            "can be worth sharing now; let research and expression retain those steps and their limits. "
            "Once a primary source supports a substantive question, read and investigate it in RUN_CONTENT_PIPELINE "
            "rather than repeating searches solely to find a final result. "
            "If one angle has no admissible evidence, use SEARCH_WORLD for a new question instead of repeating the same empty query or ending the daily goal. "
            "The minimum is a required result, not permission to invent facts or post repeated claims. "
            "Prioritize a sustainable cadence of useful original posts; replies are secondary. Use recent_original_posts and current_time to notice long publication gaps. "
            "Select the pace and next review yourself, and use WAIT with a concrete next_action rather than silently abandoning posting. A failed draft is not a published post. "
            "Evidence can support both a short post and a later illustrated explanation when each adds useful supported value. Choose format by evidence quality, not a required fresh belief change. "
            "A no-reply decision applies to those observed posts; consider another discovery source, research, or useful original content rather than treating it as a goal-wide stop. "
            "Keep your experiment's measurement integrity, but distinguish waiting for its outcome from waiting on every useful goal action. "
            "Use previous_episode, consecutive_waits and real publication/reply receipts to avoid repeated inspection or indefinite WAIT. "
            "For WAIT name the concrete blocker or time-dependent evidence in wait_for and the useful next_action to revisit; do not claim goal progress from scheduling a wait. "
            "Return JSON only.\n"
            + json.dumps(
                {
                    "owner_goal": self._compact(dict(owner_goal)),
                    "mission_context": self._decision_mission_context(mission),
                    "current_mind": self._decision_mind_context(current_mind),
                    "prior_steps": self._decision_steps(state.get("steps")),
                    "previous_episode": self._decision_previous_episode(
                        state.get("previous_episode")
                    ),
                    "consecutive_waits": int(state.get("consecutive_waits") or 0),
                    "current_time": datetime.now(timezone.utc).isoformat(),
                    "recent_original_posts": recent_posts,
                    "research_trajectory": research_trajectory(read_research_state(self.state_root)),
                    "parked_inquiries": parked_inquiries(read_research_state(self.state_root)),
                    "related_memory": self._decision_memory_context(related_memory),
                    "tools": self._tool_contracts(),
                    "remaining_steps": max(0, self.MAX_STEPS - len(list(state.get("steps") or []))),
                },
                ensure_ascii=False,
            )
            + "\nSchema: "
            + json.dumps(OwnerAgentDecision.model_json_schema(), ensure_ascii=False)
        )
        raw = await runtime.cognitive_probe(prompt, max_tokens=1400)
        return OwnerAgentDecision.model_validate_json(_extract_json(raw))

    async def run(
        self,
        *,
        runtime: Any,
        owner_goal: Mapping[str, Any],
        mission_context: Mapping[str, Any] | None,
        tool_executor: ToolExecutor,
        now: datetime,
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

        related_memory = self._related_memory(
            owner_goal=owner_goal,
            mission_context=mission_context,
        )

        while len(list(state.get("steps") or [])) < self.MAX_STEPS:
            decision = await self._decide(
                runtime=runtime,
                owner_goal=owner_goal,
                mission_context=mission_context,
                related_memory=related_memory,
                state=state,
            )
            payload = decision.model_dump(mode="json")
            if decision.action in self.TERMINAL:
                state = {
                    **state,
                    "status": "DECIDED",
                    "decision": payload,
                    "cognition_blocker": None,
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
                "cognition_blocker": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(state)

        state = {
            **state,
            "status": "UNRESOLVED_ACTION_REQUIRED",
            "decision": None,
            "unresolved_reason": (
                "Owner control exhausted its bounded inspection budget without "
                "choosing a permitted world-changing action."
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(state)
        return {
            "status": "UNRESOLVED_ACTION_REQUIRED",
            "episode_id": state["episode_id"],
            "cycle_id": state["cycle_id"],
            "decision": None,
            "steps": list(state.get("steps") or []),
            "resumed": False,
            "unresolved_reason": state["unresolved_reason"],
        }


    def complete(
        self,
        *,
        outcome: Mapping[str, Any],
        now: datetime,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        state = self._read(goal_id)
        if state.get("schema_version") != self.SCHEMA:
            return {"status": "NO_EPISODE"}
        if str(state.get("status") or "") == "COMPLETED":
            return {
                "status": "COMPLETED",
                "episode_id": state.get("episode_id"),
                "goal_scheduler_outcome": state.get("goal_scheduler_outcome"),
                "duplicate": True,
            }
        stamp = now.astimezone(timezone.utc)
        scheduler_outcome = None
        goal_context = (
            state.get("goal")
            if isinstance(state.get("goal"), Mapping)
            else {}
        )
        scheduler_decision_id = str(
            goal_context.get("goal_scheduler_decision_id") or ""
        ).strip()
        if scheduler_decision_id:
            from .goal_scheduler_evolution import GoalSchedulerEvolution

            scheduler_outcome = GoalSchedulerEvolution(self.state_root).settle(
                scheduler_decision_id,
                outcome=dict(outcome),
                now=stamp,
            )
        completed = {
            **state,
            "status": "COMPLETED",
            "outcome": self._compact(dict(outcome)),
            "goal_scheduler_outcome": self._compact(scheduler_outcome),
            "updated_at": stamp.isoformat(),
            "completed_at": stamp.isoformat(),
        }
        self._write(completed)
        return {
            "status": "COMPLETED",
            "episode_id": completed.get("episode_id"),
            "goal_scheduler_outcome": scheduler_outcome,
        }


__all__ = ["OwnerAgentDecision", "OwnerAgentEpisode"]
