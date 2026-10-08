"""Wakeful owner task logic; the host injects runner components."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

class OwnerTaskMethods:
    """Owner capability handlers for the shared wakeful host."""

    def _owner_behavioral_context(self) -> dict[str, Any]:
        wakeful = getattr(self, "wakeful", None)
        projection = getattr(wakeful, "behavioral_projection", None)
        if not callable(projection):
            projection = getattr(getattr(self, "state_client", None), "behavioral_projection", None)
        if not callable(projection):
            raise RuntimeError(
                "owner cognition requires Ferro behavioral projection"
            )
        from wechat_traffic_agent.behavioral_state import build_behavioral_state, build_phenotype_prior

        behavioral_state = build_behavioral_state(projection())
        phenotype_prior = build_phenotype_prior(behavioral_state)
        return {
            "phenotype_prior": phenotype_prior,
            "behavioral_state": behavioral_state,
        }

    def _owner_activated_external_goal_ids(self) -> set[str]:
        """Return external-mission goals with durable private-owner activation evidence."""
        return {
            goal_id
            for goal_id, row in self._owner_active_goal_proposals().items()
            if str(row.get("kind") or "") == "EXTERNAL_MISSION"
        }

    def _owner_active_goal_proposals(self) -> dict[str, dict[str, Any]]:
        """Latest canonical activated owner proposal for each backing runtime goal."""
        state_root = getattr(self, "state_root", None)
        if state_root is None:
            return {}
        proposals_root = (
            Path(state_root) / "application" / "operator_chat" / "goal_proposals"
        )
        if not proposals_root.is_dir():
            return {}
        rows: dict[str, tuple[str, dict[str, Any]]] = {}
        for path in proposals_root.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") != "ACTIVATED":
                continue
            semantic = str(row.get("semantic_status") or "CANONICAL")
            if semantic not in {"CANONICAL", "ACTIVE", ""}:
                continue
            goal_id = str(row.get("goal_id") or "").strip()
            if not goal_id:
                continue
            stamp = str(
                row.get("activated_at")
                or row.get("created_at")
                or ""
            )
            current = rows.get(goal_id)
            if current is None or stamp >= current[0]:
                rows[goal_id] = (stamp, dict(row))
        return {goal_id: row for goal_id, (_, row) in rows.items()}

    def _active_owner_goal_context(
        self,
        *,
        relevant_refs: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
        daily_goal = DailyPostingGoal(self.state_root).owner_goal(now or datetime.now(timezone.utc))
        if daily_goal is not None:
            return daily_goal
        runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        goals = list(getattr(runtime, "active_goals", ()) or ())
        seen_goal_ids = {
            str(getattr(goal, "goal_id", "") or "").strip()
            for goal in goals
        }
        for goal in (getattr(runtime, "goals", ()) or ()):
            status = str(
                getattr(
                    getattr(goal, "status", None),
                    "value",
                    getattr(goal, "status", ""),
                )
            )
            goal_id = str(getattr(goal, "goal_id", "") or "").strip()
            if status == "ACTIVE" and goal_id not in seen_goal_ids:
                goals.append(goal)
                seen_goal_ids.add(goal_id)
        owner_proposals = self._owner_active_goal_proposals()
        owner_external_goal_ids = {
            goal_id
            for goal_id, row in owner_proposals.items()
            if str(row.get("kind") or "") == "EXTERNAL_MISSION"
        }
        # Runtime external missions are already durable operator-issued
        # commitments. Use them as an independent owner-authority source so a
        # stale/missing application proposal projection cannot demote an active
        # owner mission to an ordinary USER goal.
        for mission in (getattr(runtime, "external_missions", ()) or ()):
            mission_status = str(
                getattr(
                    getattr(mission, "status", None),
                    "value",
                    getattr(mission, "status", ""),
                )
            )
            issued_by = str(getattr(mission, "issued_by", "") or "").strip().lower()
            mission_goal_id = str(getattr(mission, "goal_id", "") or "").strip()
            if (
                mission_status == "ACTIVE"
                and issued_by == "operator"
                and mission_goal_id
            ):
                owner_external_goal_ids.add(mission_goal_id)
        candidates: list[tuple[Any, str]] = []
        for goal in goals:
            origin = str(
                getattr(
                    getattr(goal, "origin", None),
                    "value",
                    getattr(goal, "origin", ""),
                )
            )
            status = str(
                getattr(
                    getattr(goal, "status", None),
                    "value",
                    getattr(goal, "status", ""),
                )
            )
            goal_id = str(getattr(goal, "goal_id", "") or "").strip()
            context_origin = ""
            if goal_id in owner_external_goal_ids and origin in {"USER", "EXTERNAL_MISSION"}:
                # Agent Runtime represents an external mission's backing Goal as
                # USER-origin. Durable owner proposal binding is the stronger
                # application-level fact for content routing.
                context_origin = "EXTERNAL_MISSION"
            elif origin == "USER":
                context_origin = "USER"
            if context_origin and status == "ACTIVE":
                candidates.append((goal, context_origin))
        if not candidates:
            return None
        preferred_ids = {
            str(ref).strip()
            for ref in relevant_refs
            if str(ref).strip()
        }
        if preferred_ids:
            preferred = [
                item
                for item in candidates
                if str(getattr(item[0], "goal_id", "") or "").strip()
                in preferred_ids
            ]
            if preferred:
                candidates = preferred

        episode = getattr(self, "owner_agent_episode", None)
        projection_for = getattr(episode, "projection_for", None)
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        scored: list[dict[str, Any]] = []
        for goal, origin in candidates:
            goal_id = str(getattr(goal, "goal_id", "") or "").strip()
            priority = float(getattr(goal, "priority", 0.0) or 0.0)
            state = (
                projection_for(goal_id)
                if goal_id and callable(projection_for)
                else {}
            )
            updated_raw = str(
                state.get("updated_at")
                or state.get("completed_at")
                or state.get("created_at")
                or ""
            )
            try:
                updated_at = datetime.fromisoformat(
                    updated_raw.replace("Z", "+00:00")
                )
                if updated_at.tzinfo is None or updated_at.utcoffset() is None:
                    raise ValueError
                age_seconds = max(
                    0.0,
                    (stamp - updated_at.astimezone(timezone.utc)).total_seconds(),
                )
            except ValueError:
                # Never-run goals must get a turn before already-active goals.
                age_seconds = 10**9
            row = {
                "goal_id": goal_id,
                "priority": priority,
                "age_seconds": age_seconds,
                "created_at": str(getattr(goal, "created_at", "") or ""),
                "goal": goal,
                "origin": origin,
            }
            scored.append(row)
        pool = scored
        scheduler_receipt: dict[str, Any] | None = None
        if preferred_ids:
            # Explicit owner wake references are authority, not a policy trial.
            pool.sort(
                key=lambda row: (
                    float(row["age_seconds"])
                    + max(0.0, min(1.0, float(row["priority"]))) * 300.0,
                    float(row["priority"]),
                    str(row["created_at"]),
                ),
                reverse=True,
            )
            chosen = pool[0]
        else:
            scheduler = getattr(self, "goal_scheduler_evolution", None)
            if scheduler is not None:
                scheduler_receipt = scheduler.select(
                    [
                        {
                            "goal_id": row["goal_id"],
                            "priority": row["priority"],
                            "age_seconds": row["age_seconds"],
                            "created_at": row["created_at"],
                        }
                        for row in pool
                    ],
                    now=stamp,
                )
                selected_goal_id = str(scheduler_receipt.get("goal_id") or "")
                chosen = next(
                    row for row in pool
                    if str(row["goal_id"]) == selected_goal_id
                )
            else:
                pool.sort(
                    key=lambda row: (
                        float(row["age_seconds"])
                        + max(0.0, min(1.0, float(row["priority"]))) * 300.0,
                        float(row["priority"]),
                        str(row["created_at"]),
                    ),
                    reverse=True,
                )
                chosen = pool[0]
        goal = chosen["goal"]
        origin = str(chosen["origin"])
        parent_description = str(
            getattr(goal, "description", "") or ""
        ).strip()
        goal_id = str(getattr(goal, "goal_id", "") or "").strip()
        proposal = owner_proposals.get(goal_id) or {}
        proposal_description = str(
            proposal.get("description") or ""
        ).strip()
        description = proposal_description or parent_description
        if not goal_id or not description:
            return None
        return {
            "goal_id": goal_id,
            "origin": origin,
            "description": description,
            "parent_goal_description": parent_description,
            "owner_proposal_id": str(
                proposal.get("proposal_id") or ""
            ) or None,
            "owner_commitment_kind": str(
                proposal.get("kind") or ""
            ) or None,
            "owner_commitment_reason": str(
                proposal.get("reason") or ""
            ) or None,
            "reason": str(getattr(goal, "reason", "") or ""),
            "priority": float(getattr(goal, "priority", 0.0) or 0.0),
            "created_at": str(getattr(goal, "created_at", "") or ""),
            "selection_authority": (
                "EXACT_OWNER_GOAL_REF"
                if goal_id in preferred_ids
                else "FAIR_OWNER_GOAL_SCHEDULER"
            ),
            "goal_scheduler_decision_id": (
                str(scheduler_receipt.get("decision_id") or "")
                if scheduler_receipt is not None
                else None
            ),
            "goal_scheduler_arm": (
                str(scheduler_receipt.get("arm") or "")
                if scheduler_receipt is not None
                else None
            ),
            "goal_scheduler_priority_bonus_seconds": (
                float(scheduler_receipt.get("priority_bonus_seconds"))
                if scheduler_receipt is not None
                else None
            ),
            "goal_scheduler_awe_experiment_id": (
                (str(scheduler_receipt.get("awe_experiment_id") or "") or None)
                if scheduler_receipt is not None
                else None
            ),
        }

    def _owner_agent_mission_context(
        self,
        owner_goal: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        goal_id = str(owner_goal.get("goal_id") or "")
        for mission in list(getattr(runtime, "external_missions", ()) or ()):
            if str(getattr(mission, "goal_id", "") or "") != goal_id:
                continue
            status = str(
                getattr(
                    getattr(mission, "status", None),
                    "value",
                    getattr(mission, "status", ""),
                )
            )
            if status != "ACTIVE":
                continue
            current = trajectory = pressure = None
            controller = getattr(runtime, "missions", None)
            if controller is not None:
                try:
                    progress = controller.progress(
                        str(getattr(mission, "mission_id")),
                        now,
                    )
                    current = getattr(progress, "current_value", None)
                    trajectory = getattr(progress, "trajectory", None)
                    pressure = getattr(progress, "pressure", None)
                except Exception:
                    pass
            roadmap_context = None
            if controller is not None:
                latest_roadmap = getattr(controller, "latest_roadmap", None)
                if callable(latest_roadmap):
                    try:
                        roadmap = latest_roadmap(
                            str(getattr(mission, "mission_id"))
                        )
                    except Exception:
                        roadmap = None
                    if roadmap is not None:
                        schedule_by_step: dict[str, Any] = {}
                        portfolio_schedule = getattr(
                            controller, "portfolio_schedule", None
                        )
                        if callable(portfolio_schedule):
                            try:
                                schedule = portfolio_schedule(
                                    now=now,
                                    max_parallelism=2,
                                )
                                schedule_by_step = {
                                    str(item.step_id): item
                                    for item in list(
                                        getattr(schedule, "items", ()) or ()
                                    )
                                    if str(
                                        getattr(item, "mission_id", "") or ""
                                    )
                                    == str(getattr(mission, "mission_id"))
                                }
                            except Exception:
                                schedule_by_step = {}
                        roadmap_context = {
                            "roadmap_id": str(
                                getattr(roadmap, "roadmap_id", "") or ""
                            ),
                            "version": int(
                                getattr(roadmap, "version", 0) or 0
                            ),
                            "rationale": str(
                                getattr(roadmap, "rationale", "") or ""
                            )[:800],
                            "steps": [
                                {
                                    "step_id": str(
                                        getattr(step, "step_id", "") or ""
                                    ),
                                    "title": str(
                                        getattr(step, "title", "") or ""
                                    )[:240],
                                    "objective": str(
                                        getattr(step, "objective", "") or ""
                                    )[:500],
                                    "depends_on": list(
                                        getattr(
                                            step, "depends_on_step_ids", ()
                                        )
                                        or ()
                                    )[:8],
                                    "status": str(
                                        getattr(
                                            getattr(
                                                schedule_by_step.get(
                                                    str(
                                                        getattr(
                                                            step,
                                                            "step_id",
                                                            "",
                                                        )
                                                        or ""
                                                    )
                                                ),
                                                "status",
                                                "",
                                            ),
                                            "value",
                                            getattr(
                                                schedule_by_step.get(
                                                    str(
                                                        getattr(
                                                            step,
                                                            "step_id",
                                                            "",
                                                        )
                                                        or ""
                                                    )
                                                ),
                                                "status",
                                                "",
                                            ),
                                        )
                                        or (
                                            "COMPLETED"
                                            if getattr(step, "completed_at", None)
                                            else "CANCELLED"
                                            if getattr(step, "cancelled_at", None)
                                            else "RUNNING"
                                            if getattr(step, "started_at", None)
                                            else "PLANNED"
                                        )
                                    ),
                                }
                                for step in list(
                                    getattr(roadmap, "steps", ()) or ()
                                )[:16]
                            ],
                        }
            return {
                "mission_id": str(getattr(mission, "mission_id", "") or ""),
                "goal_id": goal_id,
                "description": str(getattr(mission, "description", "") or "")[:1200],
                "metric": str(getattr(mission, "metric", "") or ""),
                "baseline": getattr(mission, "baseline", None),
                "target": getattr(mission, "target", None),
                "current": current,
                "trajectory": trajectory,
                "pressure": pressure,
                "deadline": (
                    getattr(mission, "deadline").isoformat()
                    if hasattr(getattr(mission, "deadline", None), "isoformat")
                    else None
                ),
                "constraints": [
                    str(item)[:500]
                    for item in list(getattr(mission, "constraints", ()) or ())[:8]
                ],
                "roadmap": roadmap_context,
            }
        return {}

    def _owner_agent_control(
        self,
        *,
        owner_goal: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        episode = getattr(self, "owner_agent_episode", None)
        domain_runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        if episode is None or domain_runtime is None:
            return {"status": "UNAVAILABLE"}
        goal_id = str(owner_goal.get("goal_id") or "").strip()
        active_blocker = episode.cognition_blocker_for(
            goal_id,
            now=now,
        )
        if active_blocker is not None:
            return {
                "status": "OWNER_COGNITION_PROVIDER_UNAVAILABLE",
                "goal_id": goal_id,
                **active_blocker,
            }
        mission_context = self._owner_agent_mission_context(owner_goal, now)
        from wechat_traffic_agent.x_goal_progress import owner_x_execution_context
        mission_context = {
            **mission_context,
            "x_execution": owner_x_execution_context(
                Path(self.state_root) if getattr(self, "state_root", None) is not None else None,
                configured=getattr(self, "x_loop", None) is not None,
            ),
        }
        content_mind = getattr(self, "content_mind", None)
        if content_mind is not None:
            mission_context["current_mind"] = content_mind.public_projection()
        cognition_runtime = getattr(self, "owner_cognition_runtime", None)
        if cognition_runtime is None:
            from wechat_traffic_agent.runtime_backend import load_owner_cognition_runtime

            cognition_runtime = load_owner_cognition_runtime(
                state_root=Path(self.state_root)
                if getattr(self, "state_root", None) is not None
                else None,
                behavioral_context_provider=self._owner_behavioral_context,
            )
            self.owner_cognition_runtime = cognition_runtime

        async def execute_tool(decision: dict[str, Any]) -> dict[str, Any]:
            action = str(decision.get("action") or "")
            if action == "INSPECT_MISSION":
                return {
                    "status": "OK",
                    "mission": mission_context,
                    "owner_goal": owner_goal,
                }
            if action == "SEARCH_WORLD":
                query = str(decision.get("query") or "").strip()
                feed = getattr(
                    getattr(self, "continuous_loop", None),
                    "world_feed",
                    None,
                )
                searcher = getattr(feed, "web_searcher", None)
                if not query or not callable(searcher):
                    return {"status": "UNAVAILABLE", "query": query}
                try:
                    found = await searcher(query, now)
                except Exception as exc:
                    return {
                        "status": "ERROR",
                        "query": query,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:300],
                    }
                rows = []
                for item in list(found or ())[:8]:
                    if not isinstance(item, dict):
                        continue
                    row = {
                        key: item.get(key)
                        for key in (
                            "evidence_id", "source_kind", "source", "title",
                            "summary", "url", "published_at", "observed_at",
                            "source_revision", "evidence_refs", "publisher_feed",
                            "search_query", "search_rank", "learning_kind",
                        )
                        if item.get(key) is not None
                    }
                    if not str(row.get("evidence_id") or "").strip():
                        row["evidence_id"] = "owner-search:" + sha256(
                            json.dumps(
                                {
                                    "url": row.get("url"),
                                    "title": row.get("title"),
                                    "summary": row.get("summary"),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest()[:32]
                    row.setdefault("source_kind", "web-search")
                    row.setdefault(
                        "source_revision",
                        str(item.get("source_revision") or row["evidence_id"]),
                    )
                    row.setdefault("evidence_refs", [row["evidence_id"]])
                    rows.append(row)
                return {"status": "OK", "query": query, "items": rows}
            if action in {"CHECK_X", "ENGAGE_X"}:
                loop = getattr(self, "x_loop", None)
                if loop is None:
                    return {"status": "DISABLED"}
                try:
                    result = await loop.observe(now=now, allow_remote_actions=action == "ENGAGE_X")
                    count = int(result.get("new_mentions") or 0)
                    if count > 0:
                        result["state_event"] = self.state_events.emit(
                            "X_MENTIONS",
                            {"count": count},
                            observed_at=now,
                        )
                    return {"status": "OK" if str(result.get("status") or "").startswith("OK") else "DEGRADED", "x_social_observation": result}
                except Exception as exc:
                    return {"status": "DEGRADED", **self._error(exc)}
            if action == "CHECK_OUTCOMES":
                return self._outcomes(now)
            return {"status": "UNSUPPORTED", "action": action}

        from wechat_traffic_agent.cognition_context import owner_cognition_scope

        with owner_cognition_scope({
            "goal_id": str(owner_goal.get("goal_id") or ""),
            "mission_id": str(mission_context.get("mission_id") or ""),
            "description": str(owner_goal.get("description") or ""),
        }):
            try:
                return self._run_async(
                    episode.run(
                        runtime=cognition_runtime,
                        owner_goal=owner_goal,
                        mission_context=mission_context,
                        tool_executor=execute_tool,
                        now=now,
                    )
                )
            except Exception as exc:
                from wechat_traffic_agent.openai_public_chat import OpenAIPublicChatError

                if not isinstance(exc, OpenAIPublicChatError):
                    raise
                retry_seconds = 300 if "HTTP_429" in str(exc) else 120
                blocked = episode.mark_cognition_blocked(
                    goal_id=goal_id,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retry_at=now + timedelta(seconds=retry_seconds),
                    now=now,
                )
                self.task_scheduler.schedule(
                    "CONTENT_COGNITION",
                    now + timedelta(seconds=retry_seconds),
                    reason="resume owner cognition after provider block",
                    relevant_refs=(goal_id,),
                )
                return {
                    **blocked,
                    "goal_id": goal_id,
                    "retry_seconds": retry_seconds,
                }

    @staticmethod
    def _owner_agent_seed_evidence(control: dict[str, Any]) -> list[dict[str, Any]]:
        """Carry read-only evidence gathered by the controller into its executor."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for step in list(control.get("steps") or ()):
            if not isinstance(step, dict):
                continue
            decision = step.get("decision")
            result = step.get("result")
            if (
                not isinstance(decision, dict)
                or str(decision.get("action") or "") != "SEARCH_WORLD"
                or not isinstance(result, dict)
            ):
                continue
            for item in list(result.get("items") or ())[:8]:
                if not isinstance(item, dict):
                    continue
                evidence_id = str(item.get("evidence_id") or "").strip()
                title = str(item.get("title") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if not evidence_id or not title or not summary or evidence_id in seen:
                    continue
                seen.add(evidence_id)
                rows.append(dict(item))
        return rows
