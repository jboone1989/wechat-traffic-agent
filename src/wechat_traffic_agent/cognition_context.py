from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .memory_self import ProspectiveSelfMemoryService


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _clip(value: Any, limit: int = 1200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 14] + "...[truncated]"


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

    neural_life = wake.get("neural_life") if isinstance(wake, Mapping) else {}
    if not isinstance(neural_life, Mapping):
        neural_life = {}
    compact_neural_life = {
        key: neural_life.get(key)
        for key in ("affect", "body", "drives", "intention", "last_authority_action")
        if neural_life.get(key) is not None
    }

    app_runtime = getattr(application, "runtime", None)
    domain_runtime = getattr(application, "domain_runtime", None)
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

    mind_section = {
        "mind_schema": projection.get("mind_schema"),
        "mind_tick": projection.get("mind_tick"),
        "interests": list(projection.get("interests") or [])[:8],
        "open_inquiries": list(projection.get("open_inquiries") or [])[:8],
        "beliefs": list(projection.get("beliefs") or [])[:8],
        "recent_belief_changes": list(projection.get("recent_belief_changes") or [])[-8:],
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
        "current_intent": list(packet.current_intent),
        "missions": _mission_state(domain_runtime, now) if domain_runtime is not None else {},
        "mind": mind_section,
        "recent_experience": _recent_life_experience(mind),
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
            "control_signals": list(wake.get("neural_control_signals") or [])[-8:],
            "feedback": list(wake.get("neural_feedback") or [])[-8:],
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
        ),
        replace_runtime_state=True,
    )


__all__ = ["bind_ferro_cognition_context", "build_ferro_cognition_context"]
