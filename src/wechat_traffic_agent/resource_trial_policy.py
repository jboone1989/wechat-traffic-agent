"""Pure trial priority and model retry policy; never reads credentials or writes state."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_runtime.resource_discovery import ModelTrial, ResourceAcquisitionOutcome
from agent_runtime.resource_trials import provider_trial_execution_evidence_ref

REPLAN_OUTCOME_STATUSES = frozenset({
    "NO_VIABLE_MODEL", "MODEL_TARGETS_INCOMPATIBLE",
    "FREE_CANDIDATES_EXHAUSTED", "TEMPORARY_RATE_LIMIT", "TASK_MISMATCH",
})


def _parse_aware_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def retry_plan(plan: Any, prior: list[ModelTrial], *, now: datetime) -> Any | None:
    if not prior:
        return plan
    if any(row.status == "COMPLETED" for row in prior):
        return None
    current_ref = provider_trial_execution_evidence_ref()
    current = [row for row in prior if current_ref in tuple(row.evidence_refs or ())]
    if not current:
        return replace(plan, plan_id=f"{plan.plan_id}:executor-refresh",
                       evidence_refs=tuple(dict.fromkeys((*plan.evidence_refs, current_ref))))
    if any(str(row.error_code or "").upper() in {"HTTP_401", "HTTP_404", "HTTP_410"}
           for row in current):
        return None
    batch_size = max(1, min(int(plan.max_requests), 3))
    if len(current) >= batch_size * 3:
        return None
    latest = max((value for value in (
        _parse_aware_time(row.completed_at or row.started_at) for row in current)
        if value is not None), default=None)
    point = now.astimezone(timezone.utc)
    if latest is not None and point - latest < timedelta(hours=6):
        return None
    bucket = int(point.timestamp() // int(timedelta(hours=6).total_seconds()))
    return replace(plan, plan_id=f"{plan.plan_id}:retry:{bucket}",
                   evidence_refs=tuple(dict.fromkeys((*plan.evidence_refs, current_ref))))


@dataclass(frozen=True)
class ModelEligibility:
    excluded_models: frozenset[str]
    incompatible_models: frozenset[str]
    exhausted: dict[str, Any] | None = None


class ResourceTrialPolicy:
    def prioritized(self, plans, prior, outcomes, *, provider_limit, resolver_ref):
        attempted = {str(row.opportunity_id) for row in prior if str(row.opportunity_id or "")}
        attempted.update(str(row.lead_id) for row in outcomes
                         if str(row.lead_id or "") and (
                             row.status in {
                                 "TRIAL_STARTED", "TRIAL_SUCCEEDED", "NO_VIABLE_MODEL",
                                 "AUTHENTICATION_REJECTED", "PROVIDER_UNAVAILABLE",
                                 "MODEL_TARGETS_INCOMPATIBLE", "FREE_CANDIDATES_EXHAUSTED"
                             } or (row.status == "TRIAL_FAILED"
                                   and resolver_ref in row.evidence_refs)))
        current_executor = provider_trial_execution_evidence_ref()
        stale = {str(row.opportunity_id) for row in prior if str(row.opportunity_id or "")}
        stale = {lead_id for lead_id in stale if not any(
            row.opportunity_id == lead_id and row.status == "COMPLETED" for row in prior
        ) and not any(row.opportunity_id == lead_id
                      and current_executor in tuple(row.evidence_refs or ())
                      for row in prior)}
        ready = [plan for plan in plans if plan.status == "READY_TO_TRIAL"]
        ready.sort(key=lambda plan: (
            0 if plan.lead_id not in attempted
            else 1 if plan.lead_id in stale else 2
        ))
        return ready[:max(1, min(int(provider_limit), 3))]

    def models(self, plan, prior, *, expand_during_outage, runtime_incompatible):
        rows = {}
        for row in prior:
            if row.opportunity_id == plan.lead_id and row.model_id:
                rows.setdefault(row.model_id, []).append(row)
        incompatible = {
            model for model, history in rows.items()
            if len(history) >= max(1, min(int(plan.max_requests), 3))
            and all(row.status == "FAILED"
                    and row.error_code in {"HTTP_404", "HTTP_410"} for row in history)
        }
        incompatible.update(runtime_incompatible(
            credential_env=plan.credential_env, model_rows=rows,
        ))
        previously_tried = set(rows)
        excluded = frozenset(incompatible | (
            previously_tried if expand_during_outage else set()
        ))
        if plan.credential_env != "OPENROUTER_API_KEY":
            return ModelEligibility(excluded, frozenset(incompatible))
        candidates = {model: history for model, history in rows.items()
                      if model != "openrouter/free"}
        rate_limited = {model for model, history in candidates.items()
                        if history and all(row.status == "FAILED"
                                           and row.error_code == "HTTP_429" for row in history)}
        quality_impossible = set()
        sample = max(1, min(int(plan.max_requests), 3))
        for model, history in candidates.items():
            completed = [row for row in history
                         if row.status == "COMPLETED" and row.quality_score is not None]
            if len(history) < sample:
                continue
            possible = (sum(float(row.quality_score) for row in completed)
                        + max(0, sample - len(completed))) / sample
            if possible < 0.75:
                quality_impossible.add(model)
        exhausted = set(incompatible) | rate_limited | quality_impossible
        excluded = frozenset(exhausted | (
            previously_tried if expand_during_outage else set()
        ))
        if len(exhausted) < 3:
            return ModelEligibility(excluded, frozenset(incompatible))
        refs = tuple(row.trial_id for model in sorted(exhausted)
                     for row in rows[model])
        return ModelEligibility(excluded, frozenset(incompatible), {
            "models": sorted(exhausted),
            "trial_ids": list(refs),
            "error_codes": (
                (["HTTP_429"] if rate_limited else []) +
                (["QUALITY_CANNOT_REACH_FLOOR"] if quality_impossible else [])
            ),
        })
