"""Build the bounded model-facing cognition snapshot with no I/O.

The snapshot is a presentation of already selected facts and runtime
projections; this module never re-queries memory or alters agent behavior.
"""
from __future__ import annotations
from collections.abc import Mapping
from datetime import timezone


def _snapshot_header(*, now, application, app_runtime,
                     projection, metadata, llm_decision_context, selection_summary):
    return {
        "continuity": {
            "schema_version": "ferro-cognition-snapshot.v1",
            "as_of": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "subject_id": getattr(
                getattr(application, "subject", None), "subject_id", None
            ),
            "subject_tick": int(
                getattr(getattr(application, "subject", None), "tick", 0) or 0
            ),
            "application_cycle": int(getattr(app_runtime, "cycle", 0) or 0),
            "mind_tick": int(projection.get("mind_tick") or 0),
            "agent_runtime_cycle_is_host_internal": True,
        },
        "decision_frame": metadata["decision_frame"],
        "decision_context": llm_decision_context,
        "context_receipt": {
            key: metadata["context_receipt"].get(key)
            for key in (
                "schema_version",
                "receipt_id",
                "decision_type",
                "selected_chars_estimate",
                "candidate_counts",
                "downstream_budget_chars",
                "budget_utilization_estimate",
                "selection_policy",
            )
            if metadata["context_receipt"].get(key) is not None
        } | {
            "selection_summary": {
                "retrieval_policy": selection_summary["retrieval_policy"],
                "selected_sources": selection_summary["selected_sources"],
                "always_on_sources": selection_summary["always_on_sources"],
            }
        },
    }


def build_cognition_snapshot(*, now, application, app_runtime, projection,
                             wake, environment, views, metadata,
                             llm_decision_context, selection_summary,
                             interoception, owner_mode):
    (selected_current_intent, selected_mission_state, mind_section,
     recent_experience, retrieved_memory, selected_resources) = (
        views.current_intent, views.missions, views.mind,
        views.experience, views.memory, views.resources,
    )
    (selected_capabilities, selected_capability_beliefs,
     selected_relationships, selected_commitments, selected_operational,
     selected_neural_life) = (
        views.capabilities, views.capability_beliefs, views.relationships,
        views.commitments, views.operational, views.neural_life,
    )
    app_caps, domain_caps, manifest_caps = (
        environment.application_capabilities, environment.domain_capabilities,
        environment.manifest_capabilities,
    )
    social_projection = environment.social_projection
    phenotype_prior, behavioral_state = environment.phenotype, environment.behavior
    selected_application_caps = [
        str(item) for item in app_caps if str(item) in selected_capabilities
    ]
    selected_domain_caps = [
        str(item) for item in domain_caps if str(item) in selected_capabilities
    ]
    selected_manifest_caps = [
        str(item) for item in manifest_caps if str(item) in selected_capabilities
    ]
    internal_selected = bool(selected_neural_life)

    snapshot = {
        **_snapshot_header(
            now=now, application=application, app_runtime=app_runtime,
            projection=projection, metadata=metadata,
            llm_decision_context=llm_decision_context,
            selection_summary=selection_summary,
        ),
        "current_intent": selected_current_intent,
        "open_loops": metadata["open_loops"],
        "missions": selected_mission_state,
        "commitments": selected_commitments,
        "mind": mind_section,
        "recent_experience": recent_experience,
        "retrieved_memory": retrieved_memory,
        "resources": selected_resources,
        "capabilities": {
            "available": selected_capabilities,
            "application": selected_application_caps,
            "domain_reasoning": selected_domain_caps,
            "manifest": selected_manifest_caps,
            "evidence_bound_beliefs": selected_capability_beliefs,
        },
        "social_context": {
            "relationship_count": int(
                social_projection.get("relationship_count") or 0
            ),
            "recent_relationships": selected_relationships,
            "authority": "PERSISTED_NEURAL_SOCIAL_STATE",
        },
        "operational_state": selected_operational,
        "neural_state": {
            "life": selected_neural_life,
            "control_signals": (
                []
                if owner_mode or not internal_selected
                else list(wake.get("neural_control_signals") or [])[-6:]
            ),
            "feedback": (
                []
                if owner_mode or not internal_selected
                else list(wake.get("neural_feedback") or [])[-6:]
            ),
            "control_authority": "ADVISORY_SIGNAL_ONLY",
        },
        "interoception": interoception,
        "phenotype_prior": phenotype_prior,
        "behavioral_state": behavioral_state,
        "epistemic_contract": metadata["epistemic_contract"],
    }
    return snapshot
