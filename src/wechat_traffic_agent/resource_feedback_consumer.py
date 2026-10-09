"""Consume persisted trial facts into diagnostics and resource pool policy.

The trial executor is not invoked here. Replaying unchanged trial facts
must not cause another outbound provider trial.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agent_runtime.resource_discovery import provider_profiles, select_provider
from agent_runtime.resource_trials import ProviderTrialMeasurement
from .provider_diagnostics import diagnose_provider_trials


class ResourceFeedbackConsumer:
    def __init__(self, *, state_root, scout, persist_selection):
        self.state_root = state_root
        self.scout = scout
        self.persist_selection = persist_selection

    def consume(self, *, leads, available, now, executed_measurements,
                provider_rows, replan_required):
        all_trials = self.scout.model_trials()
        diagnostics: list[dict[str, Any]] = []
        for lead in leads:
            if lead.credential_env not in available:
                continue
            history = [row for row in all_trials if row.opportunity_id == lead.lead_id]
            if not history:
                continue
            try:
                diagnostics.append(diagnose_provider_trials(
                    state_root=self.state_root,
                    provider_label=lead.provider_label,
                    credential_env=lead.credential_env,
                    lead_id=lead.lead_id,
                    rows=history,
                    now=now,
                ))
            except Exception as exc:  # Diagnostics must not erase the underlying trial evidence.
                diagnostics.append({
                    "status": "DIAGNOSTIC_ERROR",
                    "lead_id": lead.lead_id,
                    "provider_label": lead.provider_label,
                    "error_type": type(exc).__name__,
                })
        profiles = provider_profiles(all_trials, minimum_quality=0.75, minimum_trials=3)
        selected = select_provider(profiles, task_class="simple_public_text", quality_floor=0.75)
        selection = self.persist_selection(now=now, selected=selected, trials=all_trials)
        from .llm_resources import sync_llm_resource_pool
        try:
            pool_sync = sync_llm_resource_pool(state_root=self.state_root, now=now)
        except Exception as exc:
            pool_sync = {
                "status": "SYNC_ERROR",
                "error_type": type(exc).__name__,
            }
        completed_count = sum(1 for row in executed_measurements if row.status == "COMPLETED")
        failed_count = len(executed_measurements) - completed_count
        return {
            "status": "TRIALED" if executed_measurements else ("ALREADY_TRIALED" if provider_rows else "NO_READY_TRIALS"),
            "executed_request_count": len(executed_measurements),
            "created_count": len(executed_measurements),
            "completed_count": completed_count,
            "failed_count": failed_count,
            "trial_ids": [row.trial_id for row in executed_measurements],
            "error_codes": sorted({
                str(row.error_code or row.error_type)
                for row in executed_measurements if row.error_code or row.error_type
            }),
            "providers": provider_rows,
            "diagnostics": diagnostics,
            "empirical_profiles": [
                {
                    "lead_id": row.opportunity_id, "model_id": row.model_id,
                    "task_class": row.task_class, "viable": row.viable,
                    "mean_quality": row.mean_quality, "mean_latency_ms": row.mean_latency_ms,
                    "mean_cost": row.mean_cost, "trial_count": row.trial_count,
                }
                for row in profiles
            ],
            "empirical_selection": None if selected is None else {
                "lead_id": selected.opportunity_id, "model_id": selected.model_id,
                "task_class": selected.task_class,
                "mean_quality": selected.mean_quality, "mean_latency_ms": selected.mean_latency_ms,
                "mean_cost": selected.mean_cost, "trial_count": selected.trial_count,
            },
            "selection": selection,
            "llm_resource_pool": pool_sync,
            "replan_required": replan_required,
        }
