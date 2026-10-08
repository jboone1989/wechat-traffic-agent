from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from .behavioral_state import (
    build_behavioral_state,
    build_phenotype_prior,
    compact_behavioral_state_for_prompt,
    compact_phenotype_prior_for_prompt,
)
from .capability_contracts import CapabilityContractMonitor
from .context_compiler import compile_surface_metadata, infer_decision_frame
from .decision_context import build_ferro_decision_context, extract_commitments
from .interoceptive_fault import InteroceptiveFaultSystem, read_interoceptive_projection
from .memory_self import ProspectiveSelfMemoryService


_OWNER_COGNITION_SCOPE: ContextVar[dict[str, str] | None] = ContextVar(
    "ferro_owner_cognition_scope",
    default=None,
)

_CONTEXT_STOP_TERMS = frozenset({
    "the", "and", "for", "from", "with", "without", "within", "into",
    "over", "under", "this", "that", "these", "those", "then", "than",
    "what", "which", "when", "where", "why", "how", "who", "does", "did",
    "can", "could", "would", "should", "will", "may", "might", "have",
    "has", "had", "been", "being", "are", "was", "were", "not", "only",
    "before", "after", "current", "next", "one", "use", "using", "choose",
    "owner", "goal", "mission", "agent", "episode", "task", "action",
    "tool", "tools", "content", "evidence", "return", "json",
})


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
        if token.lower() not in _CONTEXT_STOP_TERMS
    }


def _relevant_rows(
    rows: list[Any],
    query: str,
    *,
    limit: int,
    fallback_to_recent: bool = True,
    min_score: int = 1,
) -> list[Any]:
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
    threshold = max(1, int(min_score))
    selected = [row for score, _index, row in scored if score >= threshold][:limit]
    return selected or (list(rows[-limit:]) if fallback_to_recent else [])


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


def _recent_life_experience(
    mind: Any, *, limit: int | None = 8
) -> list[dict[str, Any]]:
    try:
        snapshot = mind.session.snapshot()
    except Exception:
        return []
    autobiography = snapshot.get("autobiography") if isinstance(snapshot, Mapping) else {}
    rows = list((autobiography or {}).get("events") or [])
    result: list[dict[str, Any]] = []
    selected_rows = rows if limit is None else rows[-max(1, int(limit)):]
    for row in selected_rows:
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
        roadmap_payload = None
        latest_roadmap = getattr(controller, "latest_roadmap", None)
        if callable(latest_roadmap):
            try:
                roadmap = latest_roadmap(mission.mission_id)
            except Exception:
                roadmap = None
            if roadmap is not None:
                schedule_by_step: dict[str, Any] = {}
                portfolio_schedule = getattr(controller, "portfolio_schedule", None)
                if callable(portfolio_schedule):
                    try:
                        schedule = portfolio_schedule(now=now, max_parallelism=2)
                        schedule_by_step = {
                            str(item.step_id): item
                            for item in list(getattr(schedule, "items", ()) or ())
                            if str(getattr(item, "mission_id", "") or "")
                            == str(mission.mission_id)
                        }
                    except Exception:
                        schedule_by_step = {}
                roadmap_payload = {
                    "roadmap_id": str(getattr(roadmap, "roadmap_id", "") or ""),
                    "version": int(getattr(roadmap, "version", 0) or 0),
                    "rationale": _clip(getattr(roadmap, "rationale", ""), 800),
                    "steps": [
                        {
                            "step_id": str(getattr(step, "step_id", "") or ""),
                            "title": _clip(getattr(step, "title", ""), 240),
                            "objective": _clip(getattr(step, "objective", ""), 500),
                            "depends_on": list(
                                getattr(step, "depends_on_step_ids", ()) or ()
                            )[:8],
                            "status": _enum(
                                getattr(schedule_by_step.get(str(getattr(step, "step_id", "") or "")), "status", "")
                            ) or (
                                "COMPLETED" if getattr(step, "completed_at", None)
                                else "CANCELLED" if getattr(step, "cancelled_at", None)
                                else "RUNNING" if getattr(step, "started_at", None)
                                else "PLANNED"
                            ),
                        }
                        for step in list(getattr(roadmap, "steps", ()) or ())[:16]
                    ],
                }
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
            "roadmap": roadmap_payload,
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
    l1_provider: Any | None = None,
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
    from .cognition_scope_planner import CognitionScopePlanner
    scope = CognitionScopePlanner().prepare(
        owner_scope=owner_scope, owner_mode=owner_mode, prompt=prompt,
        mission_state=mission_state, current_intent=packet.current_intent,
        projection=projection, compact_latest_plan=_compact_latest_plan,
    )
    mission_state, query_context = scope.missions, scope.query
    intents, latest_plan = scope.intents, scope.latest_plan

    from .cognition_environment_reader import CognitionEnvironmentReader
    environment = CognitionEnvironmentReader(
        build_behavior=build_behavioral_state, build_phenotype=build_phenotype_prior,
        compact_behavior=compact_behavioral_state_for_prompt,
        compact_phenotype=compact_phenotype_prior_for_prompt,
    ).collect(application=application, domain_runtime=domain_runtime, wake=wake)
    app_runtime = getattr(application, "runtime", None)
    capabilities, app_caps, domain_caps, manifest_caps = (
        environment.capabilities, environment.application_capabilities,
        environment.domain_capabilities, environment.manifest_capabilities,
    )
    capability_beliefs = environment.capability_beliefs
    social_projection, relationship_rows = (
        environment.social_projection, environment.relationships,
    )
    full_behavioral_state, full_phenotype_prior = (
        environment.full_behavior, environment.full_phenotype,
    )
    behavioral_state, phenotype_prior = environment.behavior, environment.phenotype

    state_root = (
        getattr(mind, "state_root", None)
        or getattr(wakeful, "state_root", None)
        or getattr(application, "state_root", None)
    )
    decision_frame = infer_decision_frame(
        query_context or prompt,
        surface="agent_runtime",
        owner_mode=owner_mode,
        state_root=state_root,
        l1_provider=l1_provider,
    )
    all_life_experience = _recent_life_experience(mind, limit=None)
    commitments = extract_commitments(domain_runtime)
    decision_context = build_ferro_decision_context(
        prompt=query_context or prompt,
        decision_frame=decision_frame,
        owner_scope=owner_scope,
        mission_state=mission_state,
        intents=[
            dict(row)
            for row in intents
            if isinstance(row, Mapping)
        ],
        projection=projection,
        resources=dict(packet.current_resources),
        capabilities=capabilities,
        capability_beliefs=capability_beliefs,
        relationship_rows=relationship_rows,
        commitments=commitments,
        life_experience=all_life_experience,
        wake=wake,
        latest_plan=latest_plan,
        state_root=state_root,
        now=now,
        budget_chars=12_000 if owner_mode else 18_000,
    )
    from .cognition_selected_projection import project_selected_cognition
    views = project_selected_cognition(decision_context, projection)
    selected_current_intent, selected_mission_state, mind_section = (
        views.current_intent, views.missions, views.mind,
    )
    recent_experience, retrieved_memory, selected_resources = (
        views.experience, views.memory, views.resources,
    )
    selected_capabilities, selected_capability_beliefs = (
        views.capabilities, views.capability_beliefs,
    )
    selected_relationships, selected_commitments = (
        views.relationships, views.commitments,
    )
    selected_operational, selected_neural_life = views.operational, views.neural_life

    raw_interoception = read_interoceptive_projection(state_root)
    interoception = {
        key: raw_interoception.get(key)
        for key in (
            "schema_version",
            "status",
            "updated_at",
            "distress",
            "confidence_in_self",
            "active_episode_ids",
            "suspected_faults",
            "symptoms",
            "latest_observation_id",
        )
        if raw_interoception.get(key) is not None
    }

    from .cognition_selection_receipts import selection_receipts
    llm_decision_context, selection_summary = selection_receipts(decision_context)

    core_sections = {
        "continuity": {
            "application_cycle": int(getattr(app_runtime, "cycle", 0) or 0),
            "mind_tick": int(projection.get("mind_tick") or 0),
        },
        "decision_context": llm_decision_context,
        "current_intent": selected_current_intent,
        "missions": selected_mission_state,
        "commitments": selected_commitments,
        "mind": mind_section,
        "recent_experience": recent_experience,
        "retrieved_memory": retrieved_memory,
        "resources": selected_resources,
        "capabilities": {
            "available": selected_capabilities,
            "evidence_bound_beliefs": selected_capability_beliefs,
        },
        "social_context": {"recent_relationships": selected_relationships},
        "operational_state": selected_operational,
        "neural_state": {"life": selected_neural_life},
        "interoception": interoception,
        "phenotype_prior": phenotype_prior,
        "behavioral_state": behavioral_state,
    }
    metadata = compile_surface_metadata(
        prompt=query_context or prompt,
        surface="agent_runtime",
        owner_mode=owner_mode,
        state_root=state_root,
        l1_provider=l1_provider,
        decision_frame=decision_frame,
        sections=core_sections,
        missions=list(selected_mission_state.get("active") or []),
        goals=[
            row
            for row in selected_current_intent
            if isinstance(row, Mapping) and str(row.get("kind") or "") == "GOAL"
        ],
        inquiries=[
            row
            for row in list(mind_section.get("open_inquiries") or [])
            if isinstance(row, Mapping)
        ],
        # The queue count is itself an unresolved-work signal, not a payload
        # dump. Keep it available to the open-loop compiler.
        pending_plan_count=int(wake.get("pending_plan_count") or 0),
        candidate_counts={
            "intents": len(packet.current_intent),
            "missions": len(list(mission_state.get("active") or [])),
            "commitments": len(commitments),
            "interests": len(list(projection.get("interests") or [])),
            "open_inquiries": len(list(projection.get("open_inquiries") or [])),
            "beliefs": len(list(projection.get("beliefs") or [])),
            "recent_experience": len(all_life_experience),
            "capabilities": len(capabilities),
            "decision_candidates": int(
                decision_context.get("candidate_count") or 0
            ),
        },
        downstream_budget_chars=60_000,
        selection_summary=selection_summary,
    )

    from .cognition_snapshot_presentation import build_cognition_snapshot
    snapshot = build_cognition_snapshot(
        now=now, application=application, app_runtime=app_runtime,
        projection=projection, wake=wake, environment=environment, views=views,
        metadata=metadata, llm_decision_context=llm_decision_context,
        selection_summary=selection_summary, interoception=interoception,
        owner_mode=owner_mode,
    )

    from .cognition_contract_observer import CognitionContractObserver
    CognitionContractObserver(
        state_root=state_root, monitor_factory=CapabilityContractMonitor,
        fault_factory=InteroceptiveFaultSystem,
    ).observe(
        snapshot, full_phenotype_prior=full_phenotype_prior,
        full_behavioral_state=full_behavioral_state,
        context_receipt=metadata["context_receipt"],
        now=now, domain_runtime=domain_runtime, app_runtime=app_runtime,
    )
    return snapshot


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
