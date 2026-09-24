"""Content Agent orchestration for Runtime-owned autonomous resource discovery."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from typing import Any

from agent_runtime.resource_discovery import (
    ModelTrial, ResourceAcquisitionOutcome, ResourceNeed, ResourceScout,
    plan_provider_trials, provider_profiles, resource_discovery_constraints, select_provider,
)
from agent_runtime.resource_trials import (
    ProviderTrialMeasurement, ProviderTrialRequestError, model_trials_from_measurements,
    resolve_trial_target, run_bounded_provider_trial,
)

from .nvidia_catalog import classify_nvidia_model
from .provider_diagnostics import diagnose_provider_trials


_REPLAN_OUTCOME_STATUSES = {
    "NO_VIABLE_MODEL",  # legacy broad status; now interpreted as model-level replan
    "MODEL_TARGETS_INCOMPATIBLE",
    "FREE_CANDIDATES_EXHAUSTED",
    "TEMPORARY_RATE_LIMIT",
    "TASK_MISMATCH",
}


def rank_high_capability_model(
    model_ids: list[str],
    excluded_models: frozenset[str],
    *,
    reputation_book: Any | None = None,
    task_class: str = "self_upgrade",
) -> str | None:
    """Rank NVIDIA text candidates from current external leaderboard knowledge."""
    candidates: list[str] = []
    for raw in model_ids:
        model = str(raw or "").strip()
        if not model or model in excluded_models:
            continue
        classification = classify_nvidia_model(model)
        if classification.get("endpoint_family") != "openai_chat":
            continue
        if classification.get("category") not in {
            "text_reasoning", "vision_multimodal"
        }:
            continue
        candidates.append(model)
    if not candidates:
        return None
    if reputation_book is None:
        return sorted(candidates)[0]
    ranked = reputation_book.rank(
        candidates,
        task_class=task_class,
        excluded=excluded_models,
    )
    return ranked[0] if ranked else None


class ContentResourceScout:
    def __init__(self, *, state_root: Path, capability_runtime: Any) -> None:
        self.state_root = Path(state_root)
        self.capability_runtime = capability_runtime
        self.scout = ResourceScout(self.state_root / "application", self._search)

    def _external_reputation_ranker(
        self,
        credential_env: str,
        *,
        task_class: str = "self_upgrade",
    ):
        from .model_reputation import load_model_reputation_book

        book = load_model_reputation_book(self.state_root)

        def _ranker(
            model_ids: list[str],
            excluded_models: frozenset[str],
        ) -> str | None:
            if credential_env == "NVIDIA_API_KEY":
                return rank_high_capability_model(
                    model_ids,
                    excluded_models,
                    reputation_book=book,
                    task_class=task_class,
                )
            ranked = book.rank(
                model_ids,
                task_class=task_class,
                excluded=excluded_models,
            )
            return ranked[0] if ranked else None

        return _ranker

    def maybe_discover(self, *, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("resource scout time requires timezone")
        outcomes = self.acquisition_outcomes()
        adaptive_constraints = resource_discovery_constraints(outcomes)
        runs = self._runs()
        if runs:
            last_run = runs[-1]
            last = datetime.fromisoformat(str(last_run["observed_at"]).replace("Z", "+00:00"))
            found_last_time = bool(last_run.get("new_opportunity_ids"))
            retry_after = timedelta(hours=24) if found_last_time else timedelta(minutes=30)
            new_replan_signal = any(
                (row.status == "BLOCKED_EXTERNAL" or row.status in _REPLAN_OUTCOME_STATUSES)
                and datetime.fromisoformat(row.observed_at.replace("Z", "+00:00")) > last
                for row in outcomes
            )
            if not new_replan_signal and now.astimezone(timezone.utc) - last < retry_after:
                return {
                    "status": "NOT_DUE",
                    "last_observed_at": last.isoformat(),
                    "retry_after_seconds": int(retry_after.total_seconds()),
                    "opportunity_count": len(self.scout.opportunities()),
                    "adaptive_constraints": list(adaptive_constraints),
                }
        need = ResourceNeed(
            resource_kind="llm_inference",
            objective="reduce financial burn while preserving useful task quality",
            reason=(
                "recent provider trial evidence requires alternate routes"
                if any(row.status in _REPLAN_OUTCOME_STATUSES for row in outcomes) else
                "recent resource acquisition friction requires alternate routes"
                if adaptive_constraints else
                "paid-provider concentration exists and no low-cost alternative has trial evidence"
            ),
            constraints=(
                "public evidence only",
                "never send secrets during discovery",
                "prefer zero-cost or recurring free quota",
                "no paid signup or privilege expansion without operator approval",
            ),
            search_constraints=adaptive_constraints,
        )
        created = self.scout.discover(need, now=now, max_queries=6, results_per_query=6)
        opportunities = self.scout.opportunities()
        return {
            "status": "DISCOVERED" if created else "NO_NEW_OPPORTUNITIES",
            "new_count": len(created),
            "opportunity_count": len(opportunities),
            "claimed_free_count": sum(1 for x in opportunities if x.claimed_free),
            "credential_hint_count": sum(1 for x in opportunities if x.credential_hint),
            "adaptive_constraints": list(adaptive_constraints),
            "new_opportunities": [
                {
                    "opportunity_id": x.opportunity_id,
                    "title": x.title,
                    "url": x.url,
                    "claimed_free": x.claimed_free,
                    "credential_hint": x.credential_hint,
                    "source_class": x.source_class,
                    "status": x.status,
                }
                for x in created[:12]
            ],
        }


    def verify_pending(self, *, now: datetime, limit: int = 8) -> dict[str, Any]:
        rows = self.scout.verify_pending(now=now, fetch=self._fetch, limit=limit)
        all_rows = self.scout.verifications()
        verified = [row for row in all_rows if row.status == "VERIFIED_CANDIDATE"]
        pending_count = self.scout.pending_verification_count()
        return {
            "status": "VERIFIED" if rows else "NO_PENDING",
            "processed_count": len(rows),
            "verified_candidate_count": len(verified),
            "pending_count": pending_count,
            "processed": [
                {
                    "opportunity_id": row.opportunity_id,
                    "status": row.status,
                    "relevance_score": row.relevance_score,
                    "free_evidence": row.free_evidence,
                    "local_evidence": row.local_evidence,
                    "credential_evidence": row.credential_evidence,
                    "privacy_caution": row.privacy_caution,
                    "verification_url": row.verification_url,
                }
                for row in rows
            ],
        }

    def _fetch(self, url: str, max_chars: int):
        try:
            from types import SimpleNamespace
            from .survival_resources import SurvivalResourceManager

            remote = SurvivalResourceManager(
                self.state_root
            ).fetch_remote_resource_research(
                url,
                max_chars=int(max_chars),
                now=datetime.now(timezone.utc).replace(microsecond=0),
            )
            result = dict(remote.get("result") or {})
            return SimpleNamespace(
                receipt_id=str(remote.get("receipt_id") or ""),
                payload={
                    "ok": True,
                    "page": {
                        "success": True,
                        "url": str(result.get("url") or url),
                        "markdown": str(result.get("text") or ""),
                        "content_type": result.get("content_type"),
                        "status_code": result.get("status_code"),
                        "remote_host_id": remote.get("host_id"),
                        "remote_transport": remote.get("transport"),
                    },
                },
            )
        except Exception:
            # Remote research is disposable compute. A failed/unavailable body
            # must never make public evidence collection unavailable.
            receipt = self.capability_runtime.invoke(
                "web.fetch", {"url": url, "max_chars": int(max_chars)}
            )
            persist = getattr(self.capability_runtime, "persist", None)
            if callable(persist):
                persist()
            return receipt

    def expand_provider_leads(self, *, now: datetime, limit: int = 12) -> dict[str, Any]:
        created = self.scout.expand_provider_leads(now=now, fetch=self._fetch, limit=limit)
        leads = self.scout.provider_leads()
        return {
            "status": "DISCOVERED" if created else "NO_NEW_LEADS",
            "new_count": len(created),
            "lead_count": len(leads),
            "new_leads": [
                {
                    "lead_id": item.lead_id,
                    "provider_label": item.provider_label,
                    "credential_env": item.credential_env,
                    "signup_url": item.signup_url,
                    "official_root": item.official_root,
                    "claimed_free": item.claimed_free,
                    "status": item.status,
                }
                for item in created[:20]
            ],
        }

    def verify_provider_leads(self, *, now: datetime, limit: int = 8) -> dict[str, Any]:
        rows = self.scout.verify_provider_leads(
            now=now, fetch=self._fetch, limit=limit, results_per_query=8
        )
        all_rows = self.scout.provider_verifications()
        counts: dict[str, int] = {}
        for row in all_rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {
            "status": "VERIFIED" if rows else "NO_PENDING",
            "processed_count": len(rows),
            "verification_count": len(all_rows),
            "status_counts": counts,
            "processed": [
                {
                    "lead_id": row.lead_id,
                    "status": row.status,
                    "free_status": row.free_status,
                    "payment_method_required": row.payment_method_required,
                    "privacy_caution": row.privacy_caution,
                    "rate_limit_evidence": row.rate_limit_evidence,
                    "official_urls": list(row.official_urls),
                }
                for row in rows
            ],
        }

    def plan_trials(self, *, now: datetime, limit: int = 3) -> dict[str, Any]:
        leads = self.scout.provider_leads()
        verifications = self.scout.provider_verifications()
        available = {item.credential_env for item in leads if os.environ.get(item.credential_env)}
        outcomes = self.acquisition_outcomes()
        plans = plan_provider_trials(
            leads, verifications, available_credentials=available,
            acquisition_outcomes=outcomes, limit=limit
        )
        local = self.local_inference_feasibility()
        lead_by_id = {item.lead_id: item for item in leads}
        pending_count = self.scout.pending_verification_count()
        pending_provider_count = self.scout.pending_provider_verification_count()
        payload = {
            "status": "PLANNED" if plans else "NO_VERIFIED_TRIAL_CANDIDATES",
            "observed_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "pending_verification_count": pending_count,
            "pending_provider_verification_count": pending_provider_count,
            "autonomous_work_pending": (pending_count + pending_provider_count) > 0,
            "available_credential_envs": sorted(available),
            "local_inference": local,
            "adaptive_constraints": list(resource_discovery_constraints(outcomes)),
            "acquisition_outcome_count": len(outcomes),
            "plans": [
                {
                    "plan_id": item.plan_id,
                    "lead_id": item.lead_id,
                    "provider_label": item.provider_label,
                    "credential_env": item.credential_env,
                    "signup_url": lead_by_id[item.lead_id].signup_url,
                    "official_root": lead_by_id[item.lead_id].official_root,
                    "status": item.status,
                    "priority_score": item.priority_score,
                    "task_class": item.task_class,
                    "data_policy": item.data_policy,
                    "max_requests": item.max_requests,
                    "reasons": list(item.reasons),
                    "policy_version": item.policy_version,
                }
                for item in plans
            ],
        }
        self._persist_trial_plan(payload)
        return payload

    def _active_free_pool_is_unavailable(self, *, now: datetime) -> bool:
        """Return true only when every currently activated free route is cooling down.

        Provider acquisition is normally idempotent after a model has completed a
        bounded trial. During a total free-pool outage, however, that behavior can
        strand the agent on the same already-known models forever. This predicate
        enables bounded catalogue expansion only for that outage window.
        """
        pool_root = self.state_root / "application" / "llm_resource_pool"
        try:
            manifest = json.loads(
                (pool_root / "resources.json").read_text(encoding="utf-8")
            )
            health = json.loads(
                (pool_root / "runtime" / "health.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        resources = [
            row
            for row in (manifest.get("resources") or ())
            if isinstance(row, dict)
            and str(row.get("pricing_class") or "").upper() == "FREE"
            and str(row.get("resource_id") or "")
        ]
        if not resources:
            return False
        health_rows = health.get("resources") if isinstance(health, dict) else {}
        point = now.astimezone(timezone.utc)
        for row in resources:
            resource_id = str(row["resource_id"])
            state = health_rows.get(resource_id) if isinstance(health_rows, dict) else None
            if not isinstance(state, dict):
                return False
            raw = str(state.get("cooldown_until") or "").strip()
            if not raw:
                return False
            try:
                cooldown_until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return False
            if cooldown_until.tzinfo is None or cooldown_until.astimezone(timezone.utc) <= point:
                return False
        return True

    def run_ready_trials(self, *, now: datetime, provider_limit: int = 2) -> dict[str, Any]:
        leads = self.scout.provider_leads()
        verifications = self.scout.provider_verifications()
        outcomes = self.acquisition_outcomes()
        available = {item.credential_env for item in leads if os.environ.get(item.credential_env)}
        plans = plan_provider_trials(
            leads, verifications, available_credentials=available,
            acquisition_outcomes=outcomes, limit=max(3, int(provider_limit)),
        )
        existing = list(self.scout.model_trials())
        executed_measurements: list[ProviderTrialMeasurement] = []
        provider_rows: list[dict[str, Any]] = []
        replan_required = False
        expand_during_outage = self._active_free_pool_is_unavailable(now=now)
        ready = [item for item in plans if item.status == "READY_TO_TRIAL"]
        previously_tried_leads = {
            str(row.opportunity_id)
            for row in existing
            if str(row.opportunity_id or "")
        }
        # A static provider priority must not permanently starve a verified
        # provider that has never received its first bounded trial. Keep the
        # planner's original priority ordering within each group, but let
        # never-tried READY_TO_TRIAL providers enter the bounded shortlist
        # before providers that already have empirical evidence.
        ready.sort(key=lambda item: item.lead_id in previously_tried_leads)
        ready = ready[: max(1, min(int(provider_limit), 3))]
        for plan in ready:
            api_key = str(os.environ.get(plan.credential_env) or "")
            if not api_key:
                continue
            plan_model_rows: dict[str, list[ModelTrial]] = {}
            for row in existing:
                if row.opportunity_id == plan.lead_id and row.model_id:
                    plan_model_rows.setdefault(row.model_id, []).append(row)
            incompatible_models = {
                model_id
                for model_id, rows in plan_model_rows.items()
                if len(rows) >= max(1, min(int(plan.max_requests), 3))
                and all(
                    row.status == "FAILED" and row.error_code in {"HTTP_404", "HTTP_410"}
                    for row in rows
                )
            }
            previously_tried_models = set(plan_model_rows)
            excluded_models: frozenset[str] = frozenset(
                incompatible_models
                | (previously_tried_models if expand_during_outage else set())
            )
            if plan.credential_env == "OPENROUTER_API_KEY":
                model_rows: dict[str, list[ModelTrial]] = {}
                for row in existing:
                    if row.opportunity_id != plan.lead_id or not row.model_id or row.model_id == "openrouter/free":
                        continue
                    model_rows.setdefault(row.model_id, []).append(row)
                rate_limited_models = {
                    model_id
                    for model_id, rows in model_rows.items()
                    if rows and all(
                        row.status == "FAILED" and row.error_code == "HTTP_429"
                        for row in rows
                    )
                }
                quality_impossible_models = set()
                benchmark_count = max(1, min(int(plan.max_requests), 3))
                quality_floor = 0.75
                for model_id, rows in model_rows.items():
                    completed = [
                        row for row in rows
                        if row.status == "COMPLETED" and row.quality_score is not None
                    ]
                    if len(rows) < benchmark_count:
                        continue
                    remaining = max(0, benchmark_count - len(completed))
                    best_possible = (
                        sum(float(row.quality_score) for row in completed) + float(remaining)
                    ) / float(benchmark_count)
                    if best_possible < quality_floor:
                        quality_impossible_models.add(model_id)
                exhausted_models = (
                    set(incompatible_models)
                    | rate_limited_models
                    | quality_impossible_models
                )
                if len(exhausted_models) >= 3:
                    error_codes = []
                    if rate_limited_models:
                        error_codes.append("HTTP_429")
                    if quality_impossible_models:
                        error_codes.append("QUALITY_CANNOT_REACH_FLOOR")
                    refs = tuple(
                        row.trial_id
                        for model_id in sorted(exhausted_models)
                        for row in model_rows[model_id]
                    )
                    outcome = self.record_acquisition_outcome(
                        plan_id=plan.plan_id, lead_id=plan.lead_id, status="FREE_CANDIDATES_EXHAUSTED",
                        now=now, source="RUNTIME",
                        detail_summary="bounded free-tier candidate sample exhausted; provider-wide unavailability was not established",
                        evidence_refs=refs,
                    )
                    replan_required = replan_required or bool(outcome.get("created"))
                    provider_rows.append({
                        "provider_label": plan.provider_label,
                        "credential_env": plan.credential_env,
                        "lead_id": plan.lead_id,
                        "status": "FREE_TIER_CANDIDATES_EXHAUSTED",
                        "request_count": 0,
                        "completed_count": 0,
                        "trial_ids": list(refs),
                        "mean_quality": None,
                        "mean_latency_ms": None,
                        "observed_cost_total": None,
                        "cost_observed_for_all_completed": False,
                        "models": sorted(exhausted_models),
                        "error_codes": error_codes,
                    })
                    continue
                excluded_models = frozenset(
                    exhausted_models
                    | (previously_tried_models if expand_during_outage else set())
                )
            try:
                target = resolve_trial_target(
                    plan.credential_env, api_key=api_key, exclude_models=excluded_models,
                )
            except Exception as exc:  # Provider catalogue failures must not block other candidates.
                error_code = str(getattr(exc, "code", "") or type(exc).__name__)
                if error_code == "NO_COMPATIBLE_MODEL" and incompatible_models:
                    refs = tuple(
                        row.trial_id
                        for model_id in sorted(incompatible_models)
                        for row in plan_model_rows[model_id]
                    )
                    outcome = self.record_acquisition_outcome(
                        plan_id=plan.plan_id,
                        lead_id=plan.lead_id,
                        status="MODEL_TARGETS_INCOMPATIBLE",
                        now=now,
                        source="RUNTIME",
                        detail_summary=(
                            "all currently resolvable compatible model targets were exhausted "
                            "after excluding model-level 404/410 evidence; provider-wide "
                            "unavailability was not established"
                        ),
                        evidence_refs=refs,
                    )
                    replan_required = replan_required or bool(outcome.get("created"))
                    provider_rows.append({
                        "provider_label": plan.provider_label,
                        "credential_env": plan.credential_env,
                        "lead_id": plan.lead_id,
                        "status": "PROTOCOL_TARGETS_EXHAUSTED",
                        "request_count": 0,
                        "completed_count": 0,
                        "trial_ids": list(refs),
                        "mean_quality": None,
                        "mean_latency_ms": None,
                        "observed_cost_total": None,
                        "cost_observed_for_all_completed": False,
                        "models": sorted(incompatible_models),
                        "error_codes": ["HTTP_404", "HTTP_410"],
                    })
                    continue
                provider_rows.append({
                    "provider_label": plan.provider_label,
                    "credential_env": plan.credential_env,
                    "lead_id": plan.lead_id,
                    "status": "TARGET_RESOLUTION_FAILED",
                    "request_count": 0,
                    "completed_count": 0,
                    "trial_ids": [],
                    "mean_quality": None,
                    "mean_latency_ms": None,
                    "observed_cost_total": None,
                    "cost_observed_for_all_completed": False,
                    "models": [],
                    "error_codes": [error_code],
                })
                continue
            if target is None:
                continue
            prior = [
                row for row in existing
                if row.opportunity_id == plan.lead_id and row.model_id == target.model
            ]
            if prior:
                provider_rows.append(self._trial_provider_summary(
                    plan.provider_label, plan.credential_env, plan.lead_id, prior, status="ALREADY_TRIED"
                ))
                continue
            self.record_acquisition_outcome(
                plan_id=plan.plan_id, lead_id=plan.lead_id, status="CREDENTIAL_ACQUIRED",
                now=now, source="ENVIRONMENT",
                detail_summary=f"credential variable {plan.credential_env} is present",
                evidence_refs=(f"env-presence:{plan.credential_env}",),
            )
            measurements = run_bounded_provider_trial(
                plan, api_key=api_key, now=now, target=target,
            )
            trials = model_trials_from_measurements(measurements)
            for measurement, trial in zip(measurements, trials):
                self.scout.record_model_trial(trial)
                self._persist_trial_receipt(measurement.to_payload())
                existing.append(trial)
            executed_measurements.extend(measurements)
            completed = [row for row in measurements if row.status == "COMPLETED"]
            mean_quality = (
                sum(float(row.quality_score or 0.0) for row in completed) / len(completed)
                if completed else 0.0
            )
            outcome_status = (
                "TRIAL_SUCCEEDED"
                if len(completed) == len(measurements) and mean_quality >= 0.75
                else "TRIAL_FAILED"
            )
            self.record_acquisition_outcome(
                plan_id=plan.plan_id, lead_id=plan.lead_id, status=outcome_status, now=now,
                source="RUNTIME",
                detail_summary=(
                    f"bounded synthetic trial completed={len(completed)}/{len(measurements)} "
                    f"mean_quality={mean_quality:.3f}"
                ),
                evidence_refs=tuple(row.trial_id for row in measurements),
            )
            provider_rows.append(self._trial_provider_summary(
                plan.provider_label, plan.credential_env, plan.lead_id, trials,
                status=outcome_status, measurements=measurements,
            ))
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
        selection = self._persist_provider_selection(now=now, selected=selected, trials=all_trials)
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

    def probe_capability_windfall(self, *, now: datetime) -> dict[str, Any]:
        """Backward-compatible name for the cross-provider free-model market."""
        return self.probe_free_model_market(now=now)

    def probe_free_model_market(self, *, now: datetime) -> dict[str, Any]:
        """Probe one externally reputable free model from any available provider."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("free-model market probe time requires timezone awareness")

        supported = {
            "NVIDIA_API_KEY",
            "OPENROUTER_API_KEY",
            "CEREBRAS_API_KEY",
        }
        leads = self.scout.provider_leads()
        verifications = self.scout.provider_verifications()
        available = {
            lead.credential_env
            for lead in leads
            if lead.credential_env in supported
            and str(os.environ.get(lead.credential_env) or "").strip()
        }
        plans = plan_provider_trials(
            leads,
            verifications,
            available_credentials=available,
            acquisition_outcomes=self.acquisition_outcomes(),
            limit=25,
        )
        ready = [
            row for row in plans
            if row.credential_env in supported
            and row.status == "READY_TO_TRIAL"
            and str(os.environ.get(row.credential_env) or "").strip()
        ]
        if not ready:
            return {
                "status": "NO_READY_FREE_PROVIDER_PLAN",
                "available_credential_envs": sorted(available),
            }

        probe_root = (
            self.state_root / "application" / "resource_discovery"
            / "windfall_probes"
        )
        probe_root.mkdir(parents=True, exist_ok=True)
        current_time = now.astimezone(timezone.utc)
        excluded_by_provider: dict[str, set[str]] = {
            env: set() for env in supported
        }
        for path in probe_root.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            model = str(row.get("model") or "")
            if not model:
                continue
            credential_env = str(row.get("credential_env") or "")
            if not credential_env:
                provider_label = str(row.get("provider_label") or "").lower()
                if "nvidia" in provider_label:
                    credential_env = "NVIDIA_API_KEY"
            if credential_env not in excluded_by_provider:
                continue
            status = str(row.get("status") or "")
            if status == "AVAILABLE":
                excluded_by_provider[credential_env].add(model)
                continue
            retry_raw = str(row.get("retry_at") or "")
            if retry_raw:
                try:
                    retry_at = datetime.fromisoformat(
                        retry_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    retry_at = None
                if (
                    retry_at is not None
                    and retry_at.tzinfo is not None
                    and retry_at.astimezone(timezone.utc) > current_time
                ):
                    excluded_by_provider[credential_env].add(model)

        from .llm_resources import (
            model_capability_profile,
            sync_llm_resource_pool,
        )
        from .model_reputation import load_model_reputation_book

        reputation_book = load_model_reputation_book(self.state_root)
        candidates: list[tuple[float, float, Any, Any]] = []
        resolution_errors: list[dict[str, str]] = []
        for plan in ready:
            api_key = str(os.environ.get(plan.credential_env) or "").strip()
            excluded = excluded_by_provider.get(plan.credential_env, set())
            try:
                target = resolve_trial_target(
                    plan.credential_env,
                    api_key=api_key,
                    exclude_models=frozenset(excluded),
                    selection_mode="HIGH_CAPABILITY",
                    model_ranker=self._external_reputation_ranker(
                        plan.credential_env,
                        task_class="self_upgrade",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                resolution_errors.append({
                    "credential_env": plan.credential_env,
                    "provider_label": plan.provider_label,
                    "error_code": str(
                        getattr(exc, "code", "") or type(exc).__name__
                    ),
                })
                continue
            if target is None:
                continue
            reputation_score = reputation_book.score(
                target.model,
                task_class="self_upgrade",
            )
            candidates.append(
                (
                    float(reputation_score),
                    float(plan.priority_score),
                    plan,
                    target,
                )
            )

        if not candidates:
            return {
                "status": "NO_REPUTABLE_FREE_CANDIDATE",
                "resolution_errors": resolution_errors,
            }

        _rep_score, _plan_priority, plan, target = max(
            candidates,
            key=lambda row: (row[0], row[1], row[3].model),
        )
        capability = model_capability_profile(
            target.model,
            reputation_book=reputation_book,
        )
        if not bool(capability.get("windfall_eligible")):
            return {
                "status": "NO_HIGH_CAPABILITY_FREE_CANDIDATE",
                "provider_label": plan.provider_label,
                "credential_env": plan.credential_env,
                "best_remaining_model": target.model,
                "capability_tier": capability.get("capability_tier"),
                "capability_score": capability.get("capability_score"),
                "resolution_errors": resolution_errors,
            }

        api_key = str(os.environ.get(plan.credential_env) or "").strip()
        digest = sha256(
            (
                f"{plan.plan_id}\0{plan.credential_env}\0"
                f"windfall\0{target.model}"
            ).encode()
        ).hexdigest()[:24]
        preflight_plan = replace(
            plan,
            plan_id="provider-windfall-preflight:" + digest,
            task_class="capability_windfall",
            max_requests=1,
        )
        preflight_measurements = run_bounded_provider_trial(
            preflight_plan,
            api_key=api_key,
            now=now,
            target=target,
            timeout_seconds=12.0,
        )
        for measurement in preflight_measurements:
            self._persist_trial_receipt(measurement.to_payload())
        preflight_completed = [
            row for row in preflight_measurements
            if row.status == "COMPLETED"
        ]
        preflight_quality = (
            sum(
                float(row.quality_score or 0.0)
                for row in preflight_completed
            )
            / len(preflight_completed)
            if preflight_completed else 0.0
        )
        preflight_passed = (
            len(preflight_completed) == len(preflight_measurements)
            and len(preflight_completed) > 0
            and preflight_quality >= 0.75
        )

        if preflight_passed:
            windfall_plan = replace(
                plan,
                plan_id="provider-windfall:" + digest,
                task_class="capability_windfall",
                max_requests=3,
            )
            measurements = run_bounded_provider_trial(
                windfall_plan,
                api_key=api_key,
                now=now,
                target=target,
                timeout_seconds=30.0,
            )
            trials = model_trials_from_measurements(measurements)
            for measurement, trial in zip(measurements, trials):
                self.scout.record_model_trial(trial)
                self._persist_trial_receipt(measurement.to_payload())
        else:
            measurements = list(preflight_measurements)

        completed = [
            row for row in measurements if row.status == "COMPLETED"
        ]
        mean_quality = (
            sum(float(row.quality_score or 0.0) for row in completed)
            / len(completed)
            if completed else 0.0
        )
        errors = sorted({
            str(row.error_code or row.error_type)
            for row in measurements
            if row.error_code or row.error_type
        })
        available_now = (
            len(completed) == len(measurements)
            and len(completed) > 0
            and mean_quality >= 0.75
        )
        if available_now:
            status = "AVAILABLE"
            retry_at = None
        else:
            temporary = any(
                code in {
                    "HTTP_408", "HTTP_429", "HTTP_500", "HTTP_502",
                    "HTTP_503", "HTTP_504", "TIMEOUT", "NETWORK_ERROR",
                    "TimeoutError", "URLError",
                }
                for code in errors
            )
            delay = timedelta(hours=1 if temporary else 24)
            status = (
                "TEMPORARILY_UNAVAILABLE" if temporary else "UNAVAILABLE"
            )
            retry_at = (
                current_time + delay
            ).replace(microsecond=0).isoformat()

        reputation = reputation_book.get(target.model)
        probe = {
            "schema_version": "llm-free-model-market-probe.v1",
            "model": target.model,
            "provider_label": plan.provider_label,
            "credential_env": plan.credential_env,
            "observed_at": current_time.replace(microsecond=0).isoformat(),
            "status": status,
            "retry_at": retry_at,
            "preflight_status": (
                "PASSED" if preflight_passed else "FAILED"
            ),
            "preflight_quality": round(preflight_quality, 6),
            "preflight_request_count": len(preflight_measurements),
            "preflight_trial_ids": [
                row.trial_id for row in preflight_measurements
            ],
            "benchmark_ran": bool(preflight_passed),
            "completed_count": len(completed),
            "request_count": len(measurements),
            "mean_quality": round(mean_quality, 6),
            "error_codes": errors,
            "capability_tier": capability["capability_tier"],
            "capability_score": capability["capability_score"],
            "capability_tags": capability["capability_tags"],
            "capability_source": capability["capability_source"],
            "windfall_eligible": capability["windfall_eligible"],
            "reputation_sources": (
                list(reputation.source_refs) if reputation is not None else []
            ),
            "trial_ids": [row.trial_id for row in measurements],
        }
        probe_path = probe_root / (
            sha256(
                f"{plan.credential_env}\0{target.model}".encode()
            ).hexdigest()[:24]
            + ".json"
        )
        self._atomic_json(probe_path, probe)
        pool = sync_llm_resource_pool(
            state_root=self.state_root,
            now=now,
        )
        return {
            **probe,
            "resolution_errors": resolution_errors,
            "llm_resource_pool": pool,
        }

    def _persist_trial_receipt(self, row: dict[str, Any]) -> None:
        folder = self.state_root / "application" / "resource_discovery" / "trial_receipts"
        folder.mkdir(parents=True, exist_ok=True)
        key = sha256(str(row.get("trial_id") or "").encode()).hexdigest()[:24]
        self._atomic_json(folder / f"{key}.json", row)

    def _persist_provider_selection(self, *, now: datetime, selected: Any, trials: list[ModelTrial]) -> dict[str, Any]:
        if selected is None:
            payload = {
                "status": "NO_EMPIRICALLY_VIABLE_PROVIDER",
                "observed_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            }
        else:
            matching = [
                row for row in trials
                if row.opportunity_id == selected.opportunity_id
                and row.task_class == selected.task_class
                and row.model_id == selected.model_id
                and row.status == "COMPLETED"
            ]
            latest = matching[-1] if matching else None
            payload = {
                "status": "SELECTED_EMPIRICALLY",
                "observed_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
                "opportunity_id": selected.opportunity_id,
                "task_class": selected.task_class,
                "mean_quality": selected.mean_quality,
                "mean_latency_ms": selected.mean_latency_ms,
                "mean_cost": selected.mean_cost,
                "trial_count": selected.trial_count,
                "provider_lead_id": getattr(latest, "provider_lead_id", "") if latest else "",
                "provider_label": getattr(latest, "provider_label", "") if latest else "",
                "model_id": selected.model_id,
                "evidence_refs": list(selected.evidence_refs),
            }
        folder = self.state_root / "application" / "resource_discovery" / "provider_selection"
        folder.mkdir(parents=True, exist_ok=True)
        self._atomic_json(folder / "latest.json", payload)
        return payload

    @staticmethod
    def _trial_provider_summary(
        provider_label: str, credential_env: str, lead_id: str, rows: list[Any], *,
        status: str, measurements: list[ProviderTrialMeasurement] | None = None,
    ) -> dict[str, Any]:
        completed = [row for row in rows if row.status == "COMPLETED"]
        qualities = [float(row.quality_score) for row in completed if row.quality_score is not None]
        latencies = [float(row.latency_ms) for row in completed if row.latency_ms is not None]
        costs = [float(row.observed_cost) for row in completed if row.observed_cost is not None]
        trial_ids = [str(row.trial_id) for row in rows]
        models = sorted({
            str(getattr(row, "model_id", "") or getattr(row, "model", ""))
            for row in rows if getattr(row, "model_id", "") or getattr(row, "model", "")
        })
        errors = sorted({
            str(getattr(row, "error_code", "") or getattr(row, "error_type", ""))
            for row in (measurements or rows)
            if getattr(row, "error_code", "") or getattr(row, "error_type", "")
        })
        return {
            "provider_label": provider_label, "credential_env": credential_env,
            "lead_id": lead_id, "status": status, "request_count": len(rows),
            "completed_count": len(completed), "trial_ids": trial_ids,
            "mean_quality": (sum(qualities) / len(qualities)) if qualities else None,
            "mean_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
            "observed_cost_total": sum(costs) if len(costs) == len(completed) and completed else None,
            "cost_observed_for_all_completed": bool(completed) and len(costs) == len(completed),
            "models": models, "error_codes": errors,
        }

    def record_acquisition_outcome(
        self, *, plan_id: str, lead_id: str, status: str, now: datetime,
        friction_kind: str = "NONE", source: str = "RUNTIME",
        detail_summary: str = "", evidence_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("acquisition outcome time requires timezone")
        detail = " ".join(str(detail_summary or "").split())[:500]
        refs = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x)))
        digest_input = json.dumps({
            "plan_id": str(plan_id), "lead_id": str(lead_id), "status": str(status),
            "friction_kind": str(friction_kind), "source": str(source),
            "detail_summary": detail, "evidence_refs": refs,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        outcome = ResourceAcquisitionOutcome(
            outcome_id="resource-acquisition-outcome:" + sha256(digest_input.encode()).hexdigest()[:24],
            plan_id=str(plan_id), lead_id=str(lead_id), status=str(status),
            observed_at=now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            friction_kind=str(friction_kind), source=str(source), detail_summary=detail,
            evidence_refs=refs,
        )
        folder = self.state_root / "application" / "resource_discovery" / "acquisition_outcomes"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (outcome.outcome_id.split(":")[-1] + ".json")
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
                if str(prior.get("outcome_id") or "") == outcome.outcome_id:
                    return {
                        "outcome_id": outcome.outcome_id, "status": outcome.status,
                        "friction_kind": outcome.friction_kind,
                        "observed_at": str(prior.get("observed_at") or outcome.observed_at),
                        "created": False,
                    }
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        self._atomic_json(path, {
            "outcome_id": outcome.outcome_id, "plan_id": outcome.plan_id,
            "lead_id": outcome.lead_id, "status": outcome.status,
            "observed_at": outcome.observed_at, "friction_kind": outcome.friction_kind,
            "source": outcome.source, "detail_summary": outcome.detail_summary,
            "evidence_refs": list(outcome.evidence_refs), "schema_version": outcome.schema_version,
        })
        return {
            "outcome_id": outcome.outcome_id, "status": outcome.status,
            "friction_kind": outcome.friction_kind, "observed_at": outcome.observed_at,
            "created": True,
        }

    def acquisition_outcomes(self) -> list[ResourceAcquisitionOutcome]:
        folder = self.state_root / "application" / "resource_discovery" / "acquisition_outcomes"
        rows: list[ResourceAcquisitionOutcome] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                value["evidence_refs"] = tuple(value.get("evidence_refs") or ())
                rows.append(ResourceAcquisitionOutcome(**value))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(rows, key=lambda row: (row.observed_at, row.outcome_id))

    def local_inference_feasibility(self) -> dict[str, Any]:
        mem_total_gib = mem_available_gib = None
        try:
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                token = rest.strip().split()[0]
                if token.isdigit():
                    values[key] = int(token)
            if values.get("MemTotal") is not None:
                mem_total_gib = round(values["MemTotal"] / 1024 / 1024, 3)
            if values.get("MemAvailable") is not None:
                mem_available_gib = round(values["MemAvailable"] / 1024 / 1024, 3)
        except OSError:
            pass
        disk = shutil.disk_usage(self.state_root)
        disk_free_gib = round(disk.free / 1024**3, 3)
        disk_used_ratio = round(1.0 - (disk.free / disk.total), 4) if disk.total else 1.0
        engines = [name for name in ("ollama", "llama-cli", "llama-server", "llamafile") if shutil.which(name)]
        gpu_present = Path("/dev/nvidia0").exists() or Path("/dev/dri/renderD128").exists()
        reasons: list[str] = []
        if disk_free_gib < 10.0 or disk_used_ratio >= 0.85:
            reasons.append("disk_pressure")
        if mem_available_gib is not None and mem_available_gib < 2.0:
            reasons.append("memory_pressure")
        if not engines:
            reasons.append("no_local_inference_engine")
        if "disk_pressure" in reasons or "memory_pressure" in reasons:
            status = "DEFER_LOCAL_RESOURCE_PRESSURE"
        elif not engines:
            status = "LOCAL_SETUP_REQUIRED"
        else:
            status = "READY_FOR_LOCAL_TRIAL"
        return {
            "status": status,
            "mem_total_gib": mem_total_gib,
            "mem_available_gib": mem_available_gib,
            "disk_free_gib": disk_free_gib,
            "disk_used_ratio": disk_used_ratio,
            "gpu_present": gpu_present,
            "engines": engines,
            "reasons": reasons,
        }

    def _persist_trial_plan(self, payload: dict[str, Any]) -> None:
        folder = self.state_root / "application" / "resource_discovery" / "trial_plans"
        folder.mkdir(parents=True, exist_ok=True)
        canonical = dict(payload); canonical.pop("observed_at", None)
        signature = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        latest = folder / "latest.json"
        previous_signature = None
        if latest.exists():
            try:
                previous_signature = json.loads(latest.read_text()).get("signature")
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        row = dict(payload); row["signature"] = signature
        self._atomic_json(latest, row)
        if previous_signature != signature:
            stamp = payload["observed_at"].replace(":", "").replace("+", "_")
            self._atomic_json(folder / f"{stamp}.json", row)

    @staticmethod
    def _atomic_json(path: Path, row: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(tmp, path)

    def _search(self, query: str, limit: int, source_class: str):
        payload = {"query": query, "limit": int(limit), "safesearch": 1}
        if source_class == "technical":
            payload["categories"] = ["it"]
        receipt = self.capability_runtime.invoke("web.search", payload)
        persist = getattr(self.capability_runtime, "persist", None)
        if callable(persist):
            persist()
        return receipt

    def _runs(self) -> list[dict[str, Any]]:
        folder = self.state_root / "application" / "resource_discovery" / "runs"
        rows=[]
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                value=json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value,dict) and value.get("observed_at"): rows.append(value)
            except (OSError,json.JSONDecodeError):
                continue
        return sorted(rows,key=lambda x:str(x["observed_at"]))


__all__=["ContentResourceScout"]
