"""Minimal, model-facing selection summary from a full decision workspace.

The model sees source provenance, counts and the bounded fact index, not the
host's retrieval questions or internal candidate-selection instructions.
"""
from __future__ import annotations
from collections.abc import Mapping


def selection_receipts(decision_context):
    index = [
        {
            "context_id": row.get("context_id"),
            "source": row.get("source"),
            "kind": row.get("kind"),
        }
        for row in list(decision_context.get("selected_facts") or [])
        if isinstance(row, Mapping)
    ]
    preview = {
        "schema_version": decision_context.get("schema_version"),
        "role": "SEED_WORKSPACE_NOT_REASONING_BOUNDARY",
        "model_may_reframe": True,
        "fact_index": index,
        "candidate_count": int(decision_context.get("candidate_count") or 0),
        "selected_count": int(decision_context.get("selected_count") or 0),
        "selected_chars_estimate": int(
            decision_context.get("selected_chars_estimate") or 0
        ),
        "budget_chars": int(decision_context.get("budget_chars") or 0),
        "selection_policy_ref": decision_context.get("selection_policy_ref"),
        "memory_router": dict(decision_context.get("memory_router") or {}),
        "always_on_sources": ["phenotype_prior", "behavioral_state", "interoception"],
    }
    summary = {
        "retrieval_policy": "decision_conditioned_context_retrieval_v1",
        "information_need_ids": [
            str(row.get("need_id") or "")
            for row in list(decision_context.get("information_needs") or [])
            if isinstance(row, Mapping)
        ],
        "candidate_count": int(decision_context.get("candidate_count") or 0),
        "selected_count": int(decision_context.get("selected_count") or 0),
        "excluded_count": int(decision_context.get("excluded_count") or 0),
        "selected_sources": list(decision_context.get("selected_sources") or []),
        "selection_policy_ref": decision_context.get("selection_policy_ref"),
        "selected_facts": index[:16],
        "rejected_candidates": list(
            decision_context.get("rejected_candidates") or []
        )[:16],
        "memory_router": dict(decision_context.get("memory_router") or {}),
        "always_on_sources": ["phenotype_prior", "behavioral_state", "interoception"],
    }
    return preview, summary
