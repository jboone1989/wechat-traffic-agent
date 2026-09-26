from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from .memory_self import ProspectiveSelfMemoryService


_OWNER_COGNITION_SCOPE: ContextVar[dict[str, str] | None] = ContextVar(
    "ferro_owner_cognition_scope",
    default=None,
)


@contextmanager
def owner_cognition_scope(value: Mapping[str, Any] | None):
    """Propagate one explicit owner objective through downstream cognition."""
    scope = None
    if isinstance(value, Mapping):
        goal_id = str(value.get("goal_id") or "").strip()
        if goal_id:
            scope = {
                "goal_id": goal_id,
                "mission_id": str(value.get("mission_id") or "").strip(),
                "description": str(value.get("description") or "").strip()[:2400],
                "topic": str(value.get("topic") or "").strip()[:120],
            }
    token = _OWNER_COGNITION_SCOPE.set(scope)
    try:
        yield
    finally:
        _OWNER_COGNITION_SCOPE.reset(token)


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _clip(value: Any, limit: int = 1200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 14] + "...[truncated]"


def _terms(value: Any) -> set[str]:
    return {
        token.lower()
        for token in re.findall(
            r"[A-Za-z0-9][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}",
            str(value or ""),
        )
    }


def _relevant_rows(rows: list[Any], query: str, *, limit: int) -> list[Any]:
    if not rows:
        return []
    wanted = _terms(query)
    scored: list[tuple[int, int, Any]] = []
    for index, row in enumerate(rows):
        haystack = (
            json.dumps(row, ensure_ascii=False, default=str)
            if isinstance(row, Mapping)
            else str(row)
        )
        scored.append((len(wanted & _terms(haystack)), index, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [row for score, _index, row in scored if score > 0][:limit]
    return selected or list(rows[-limit:])


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _compact_latest_plan(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    keys = (
        "cycle",
        "goal",
        "endogenous_goal",
        "required_topic",
        "external_mission_id",
        "owner_goal_id",
        "mission_discovery",
        "mission_discovery_error",
        "world_inquiry_topics",
        "resource_decision",
        "neural_taste",
        "brain_cortex_competition",
    )
    return {key: value[key] for key in keys if key in value}


def _recent_life_experience(mind: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    try:
        snapshot = mind.session.snapshot()
    except Exception:
        return []
    autobiography = snapshot.get("autobiography") if isinstance(snapshot, Mapping) else {}
    rows = list((autobiography or {}).get("events") or [])
    result: list[dict[str, Any]] = []
    for row in rows[-max(1, int(limit)):]:
        if not isinstance(row, Mapping):
            continue
        result.append({
            key: row.get(key)
            for key in (
                "event_id",
                "tick",
                "summary",
                "source",
                "domain",
                "outcome",
                "confidence",
                "evidence_refs",
            )
            if row.get(key) is not None
        })
    return result


def _mission_state(runtime: Any, now: datetime) -> dict[str, Any]:
    controller = getattr(runtime, "missions", None)
    if controller is None:
        return {"active": [], "recent_experiments": []}
    active = []
    for mission in list(controller.active())[-8:]:
        try:
            progress = controller.progress(mission.mission_id, now)
            current = progress.current_value
            trajectory = progress.trajectory
            pressure = progress.pressure
        except Exception:
            current = None
            trajectory = "UNKNOWN"
            pressure = None
        active.append({
            "mission_id": mission.mission_id,
            "goal_id": mission.goal_id,
            "description": _clip(mission.description, 1600),
            "metric": mission.metric,
            "baseline": mission.baseline,
            "target": mission.target,
            "current": current,
            "direction": _enum(mission.direction),
            "status": _enum(mission.status),
            "deadline": _iso(mission.deadline),
            "trajectory": trajectory,
            "pressure": pressure,
            "constraints": [_clip(item, 600) for item in list(mission.constraints)[:12]],
        })
    experiments = []
    for item in list(getattr(controller, "experiments", []) or [])[-8:]:
        experiments.append({
            "experiment_id": item.experiment_id,
            "mission_id": item.mission_id,
            "strategy": _clip(item.strategy, 1600),
            "expected_effect": _clip(item.expected_effect, 1200),
            "status": _enum(item.status),
            "conclusion": _enum(item.conclusion) if item.conclusion is not None else None,
            "before_value": item.before_value,
            "after_value": item.after_value,
            "measurement_window_hours": item.measurement_window_hours,
            "settlement_metric": item.settlement_metric,
        })
    return {"active": active, "recent_experiments": experiments}


def build_ferro_cognition_context(
    *,
    application: Any,
    mind: Any,
    wakeful: Any,
    prompt: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the one current Ferro snapshot presented to every LLM cognition call.

    AgentRuntime remains the cognition/constraint engine, but the embedding
    harness owns mutable life state.  This prevents stale AgentRuntime world
    state from competing with ContentMind/ApplicationRuntime/VirtualBrain.
    """
    now = now or datetime.now(timezone.utc)
    packet = ProspectiveSelfMemoryService.snapshot_from_runtime(application, mind)
    projection = mind.public_projection()
    wake = wakeful.public_snapshot()
    owner_scope = _OWNER_COGNITION_SCOPE.get()
    owner_mode = bool(owner_scope) or str(prompt).lstrip().startswith("OWNER_AGENT_EPISODE")
    mission_state = (
        _mission_state(domain_runtime, now)
        if (domain_runtime := getattr(application, "domain_runtime", None)) is not None
        else {"active": [], "recent_experiments": []}
    )
    if owner_scope:
        scoped_goal_id = str(owner_scope.get("goal_id") or "")
        scoped_mission_id = str(owner_scope.get("mission_id") or "")
        active = [
            row for row in list(mission_state.get("active") or [])
            if (
                (scoped_goal_id and str(row.get("goal_id") or "") == scoped_goal_id)
                or (
                    scoped_mission_id
                    and str(row.get("mission_id") or "") == scoped_mission_id
                )
            )
        ]
        active_mission_ids = {
            str(row.get("mission_id") or "")
            for row in active
            if str(row.get("mission_id") or "")
        }
        mission_state = {
            "active": active,
            "recent_experiments": [
                row for row in list(mission_state.get("recent_experiments") or [])
                if str(row.get("mission_id") or "") in active_mission_ids
            ],
        }
    query_context = " ".join(
        [
            str(prompt),
            str((owner_scope or {}).get("description") or ""),
            str((owner_scope or {}).get("topic") or ""),
            *[
                str(row.get("description") or "")
                for row in mission_state.get("active") or []
            ],
        ]
    )

    neural_life = wake.get("neural_life") if isinstance(wake, Mapping) else {}
    if not isinstance(neural_life, Mapping):
        neural_life = {}
    compact_neural_life = {
        key: neural_life.get(key)
        for key in ("affect", "body", "drives", "intention", "last_authority_action")
        if neural_life.get(key) is not None
    }

    app_runtime = getattr(application, "runtime", None)
    app_caps = (
        list(app_runtime.available_capabilities())
        if app_runtime is not None and callable(getattr(app_runtime, "available_capabilities", None))
        else []
    )
    domain_caps = (
        list(domain_runtime.capability_inventory.usable_ids())
        if domain_runtime is not None
        and getattr(domain_runtime, "capability_inventory", None) is not None
        else []
    )
    manifest = getattr(domain_runtime, "agent_manifest", None)
    manifest_caps = list(getattr(manifest, "capabilities", ()) or ())
    capabilities = sorted(set(str(item) for item in [*app_caps, *domain_caps, *manifest_caps] if str(item).strip()))

    intents = list(packet.current_intent)
    if owner_mode:
        owner_goal_ids = (
            {str(owner_scope.get("goal_id") or "")}
            if owner_scope and str(owner_scope.get("goal_id") or "")
            else {
                str(row.get("goal_id") or "")
                for row in mission_state.get("active") or []
                if str(row.get("goal_id") or "")
            }
        )
        scoped_intents = []
        for row in intents:
            if str(row.get("goal_id") or "") not in owner_goal_ids:
                continue
            item = dict(row)
            kind = str(item.get("kind") or "")
            if kind == "GOAL":
                item["authority"] = "OWNER_OBJECTIVE"
            else:
                continue
            scoped_intents.append(item)
        intents = scoped_intents
        # Keep one owner objective in current_intent. The bound mission is
        # already present in missions.active with metric/trajectory/constraints;
        # repeating it here makes one objective look like competing intents.

    mind_section = {
        "mind_schema": projection.get("mind_schema"),
        "mind_tick": projection.get("mind_tick"),
        "interests": _relevant_rows(list(projection.get("interests") or []), query_context, limit=3 if owner_mode else 6),
        "open_inquiries": _relevant_rows(list(projection.get("open_inquiries") or []), query_context, limit=3 if owner_mode else 6),
        "beliefs": _relevant_rows(list(projection.get("beliefs") or []), query_context, limit=3 if owner_mode else 6),
        "recent_belief_changes": _relevant_rows(list(projection.get("recent_belief_changes") or []), query_context, limit=3 if owner_mode else 6),
        "latest_plan": _compact_latest_plan(projection.get("latest_plan")),
        "strategy_learning": {
            "outcome_count": int(
                ((projection.get("strategy_learning") or {}).get("outcome_count") or 0)
            ),
            "topics": list(
                ((projection.get("strategy_learning") or {}).get("topics") or [])
            )[-16:],
        },
        "learning_boundary": _clip(projection.get("learning_boundary"), 1200),
    }

    return {
        "continuity": {
            "schema_version": "ferro-cognition-snapshot.v1",
            "as_of": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "subject_id": getattr(getattr(application, "subject", None), "subject_id", None),
            "subject_tick": int(getattr(getattr(application, "subject", None), "tick", 0) or 0),
            "application_cycle": int(getattr(app_runtime, "cycle", 0) or 0),
            "mind_tick": int(projection.get("mind_tick") or 0),
            "agent_runtime_cycle_is_host_internal": True,
        },
        "current_intent": intents[:4] if owner_mode else intents[:8],
        "missions": mission_state,
        "mind": mind_section,
        "recent_experience": _relevant_rows(_recent_life_experience(mind), query_context, limit=4 if owner_mode else 8),
        "resources": dict(packet.current_resources),
        "capabilities": {
            "available": capabilities,
            "application": app_caps,
            "domain_reasoning": domain_caps,
            "manifest": manifest_caps,
        },
        "operational_state": {
            "wakeful_state": wake.get("state"),
            "attention": wake.get("attention"),
            "resource_pressure": wake.get("resource_pressure"),
            "last_cognition_at": wake.get("last_cognition_at"),
            "last_action_at": wake.get("last_action_at"),
            "pending_plan_count": wake.get("pending_plan_count"),
            "recent_stimulus_kinds": wake.get("recent_stimulus_kinds"),
        },
        "neural_state": {
            "life": compact_neural_life,
            "control_signals": [] if owner_mode else list(wake.get("neural_control_signals") or [])[-6:],
            "feedback": [] if owner_mode else list(wake.get("neural_feedback") or [])[-6:],
            "control_authority": "ADVISORY_SIGNAL_ONLY",
        },
    }


def bind_ferro_cognition_context(*, application: Any, mind: Any, wakeful: Any) -> None:
    runtime = getattr(application, "domain_runtime", None)
    bind = getattr(runtime, "bind_cognition_context_provider", None)
    if not callable(bind):
        raise RuntimeError("Agent Runtime does not support host cognition context binding")
    bind(
        lambda _prompt: build_ferro_cognition_context(
            application=application,
            mind=mind,
            wakeful=wakeful,
            prompt=_prompt,
        ),
        replace_runtime_state=True,
    )


__all__ = [
    "bind_ferro_cognition_context",
    "build_ferro_cognition_context",
    "owner_cognition_scope",
]
