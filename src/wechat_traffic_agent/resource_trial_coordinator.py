"""One bounded orchestration cycle for resource trial policy/execution/receipts/feedback."""
from __future__ import annotations

from typing import Any

from .resource_trial_policy import retry_plan, REPLAN_OUTCOME_STATUSES


class ResourceTrialCoordinator:
    def __init__(self, *, scout, policy, executor, recorder, feedback,
                 plan_provider_trials, resolver_evidence_ref):
        self.scout, self.policy = scout, policy
        self.executor, self.recorder = executor, recorder
        self.feedback = feedback
        self.plan_provider_trials = plan_provider_trials
        self.resolver_evidence_ref = resolver_evidence_ref

    @staticmethod
    def _summary(plan, status, *, models=(), trial_ids=(), error_codes=()):
        return {
            "provider_label": plan.provider_label,
            "credential_env": plan.credential_env,
            "lead_id": plan.lead_id,
            "status": status, "request_count": 0, "completed_count": 0,
            "trial_ids": list(trial_ids), "mean_quality": None,
            "mean_latency_ms": None, "observed_cost_total": None,
            "cost_observed_for_all_completed": False,
            "models": list(models), "error_codes": list(error_codes),
        }

    def _outcome(self, plan, status, now, *, summary, refs=(), plan_id=None, source="RUNTIME"):
        result = self.recorder.record_acquisition_outcome(
            plan_id=plan_id or plan.plan_id, lead_id=plan.lead_id,
            status=status, now=now, source=source,
            detail_summary=summary, evidence_refs=tuple(refs),
        )
        return bool(result.get("created")) and status in REPLAN_OUTCOME_STATUSES

    def _exhausted(self, plan, eligibility, now):
        exhausted = eligibility.exhausted
        changed = self._outcome(
            plan, "FREE_CANDIDATES_EXHAUSTED", now,
            summary=("bounded free-tier candidate sample exhausted; "
                     "provider-wide unavailability was not established"),
            refs=exhausted["trial_ids"],
        )
        report = self._summary(
            plan, "FREE_TIER_CANDIDATES_EXHAUSTED",
            models=exhausted["models"], trial_ids=exhausted["trial_ids"],
            error_codes=exhausted["error_codes"],
        )
        return report, [], changed

    def _resolution_error(self, plan, error, now, eligibility):
        code = str(getattr(error, "code", "") or type(error).__name__) if error else "NO_VIABLE_MODEL"
        if code == "NO_COMPATIBLE_MODEL" and eligibility.incompatible_models:
            rows = [row for row in self.scout.scout.model_trials()
                    if row.opportunity_id == plan.lead_id and row.model_id in eligibility.incompatible_models]
            refs = tuple(row.trial_id for model in sorted(eligibility.incompatible_models)
                         for row in rows if row.model_id == model)
            changed = self._outcome(
                plan, "MODEL_TARGETS_INCOMPATIBLE", now,
                summary=("all currently resolvable compatible model targets were exhausted "
                         "after excluding model-level 404/410 evidence; provider-wide "
                         "unavailability was not established"), refs=refs,
            )
            return self._summary(
                plan, "PROTOCOL_TARGETS_EXHAUSTED",
                models=sorted(eligibility.incompatible_models), trial_ids=refs,
                error_codes=["HTTP_404", "HTTP_410"],
            ), [], changed
        status = ("AUTHENTICATION_REJECTED" if code == "HTTP_401"
                  else "NO_VIABLE_MODEL" if code in {"NO_COMPATIBLE_MODEL", "NO_VIABLE_MODEL"}
                  else "TRIAL_FAILED")
        refs = (
            ("target-resolution:no-candidate",) if error is None
            else (f"target-resolution-error:{code}", self.resolver_evidence_ref)
        )
        changed = self._outcome(
            plan, status, now,
            summary=("bounded provider trial target resolution returned no candidate"
                     if error is None else
                     f"bounded provider trial target resolution failed with {code}"),
            refs=refs,
        )
        return self._summary(
            plan, "AUTHENTICATION_REJECTED" if status == "AUTHENTICATION_REJECTED"
            else "TARGET_RESOLUTION_FAILED", error_codes=[code],
        ), [], changed

    def _execute(self, plan, api_key, target, now, existing):
        prior = [row for row in existing
                 if row.opportunity_id == plan.lead_id and row.model_id == target.model]
        plan_to_run = retry_plan(plan, prior, now=now)
        if plan_to_run is None:
            return self.scout._trial_provider_summary(
                plan.provider_label, plan.credential_env, plan.lead_id,
                prior, status="ALREADY_TRIED",
            ), [], False
        self._outcome(
            plan, "CREDENTIAL_ACQUIRED", now, plan_id=plan_to_run.plan_id,
            source="ENVIRONMENT",
            summary=f"credential variable {plan.credential_env} is present",
            refs=(f"env-presence:{plan.credential_env}",),
        )
        execution = self.executor.execute(
            plan_to_run, api_key=api_key, target=target, now=now,
        )
        self.recorder.record_trials(execution)
        existing.extend(execution.trials)
        completed = [row for row in execution.measurements if row.status == "COMPLETED"]
        quality = (
            sum(float(row.quality_score or 0.0) for row in completed) / len(completed)
            if completed else 0.0
        )
        status = ("TRIAL_SUCCEEDED" if len(completed) == len(execution.measurements)
                  and quality >= 0.75 else "TRIAL_FAILED")
        self._outcome(
            plan, status, now, plan_id=plan_to_run.plan_id,
            summary=(f"bounded synthetic trial completed={len(completed)}/"
                     f"{len(execution.measurements)} mean_quality={quality:.3f}"),
            refs=tuple(row.trial_id for row in execution.measurements),
        )
        return self.scout._trial_provider_summary(
            plan.provider_label, plan.credential_env, plan.lead_id,
            list(execution.trials), status=status,
            measurements=list(execution.measurements),
        ), list(execution.measurements), False

    def _one(self, plan, credentials, now, existing, expand):
        api_key = credentials.get(plan.credential_env, "")
        if not api_key:
            return None
        eligibility = self.policy.models(
            plan, existing, expand_during_outage=expand,
            runtime_incompatible=self.scout._runtime_incompatible_models,
        )
        if eligibility.exhausted:
            return self._exhausted(plan, eligibility, now)
        try:
            target = self.executor.resolve(
                plan, api_key=api_key, excluded_models=eligibility.excluded_models,
            )
        except Exception as exc:
            return self._resolution_error(plan, exc, now, eligibility)
        if target is None:
            return self._resolution_error(plan, None, now, eligibility)
        return self._execute(plan, api_key, target, now, existing)

    def run(self, *, now, provider_limit, credentials):
        leads = self.scout.scout.provider_leads()
        verifications = self.scout.scout.provider_verifications()
        outcomes = self.recorder.acquisition_outcomes()
        available = {lead.credential_env for lead in leads
                     if credentials.get(lead.credential_env)}
        plans = self.plan_provider_trials(
            leads, verifications, available_credentials=available,
            acquisition_outcomes=outcomes, limit=max(3, int(provider_limit)),
        )
        existing = list(self.scout.scout.model_trials())
        ready = self.policy.prioritized(
            plans, existing, outcomes, provider_limit=provider_limit,
            resolver_ref=self.resolver_evidence_ref,
        )
        measurements, rows, replan = [], [], False
        expand = self.scout._active_free_pool_is_unavailable(now=now)
        for plan in ready:
            result = self._one(plan, credentials, now, existing, expand)
            if result is None:
                continue
            row, observed, changed = result
            rows.append(row)
            measurements.extend(observed)
            replan = replan or changed
        return self.feedback.consume(
            leads=leads, available=available, now=now,
            executed_measurements=measurements, provider_rows=rows,
            replan_required=replan,
        )
