"""Content Agent orchestration for Runtime-owned autonomous resource discovery."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
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
    provider_trial_execution_evidence_ref, resolve_trial_target, run_bounded_provider_trial,
)

from .nvidia_catalog import classify_nvidia_model
from .provider_diagnostics import diagnose_provider_trials
from .resource_trial_policy import REPLAN_OUTCOME_STATUSES as _REPLAN_OUTCOME_STATUSES


def _provider_trial_resolver_evidence_ref() -> str:
    """Fingerprint target-resolution behavior without depending on a release path.

    Target-resolution failures are durable evidence, but they must not make a
    provider look permanently "already attempted" after the resolver itself has
    changed.  Hashing the resolver source gives those failures an implementation
    generation while preserving the original immutable outcome.
    """
    try:
        payload = inspect.getsource(resolve_trial_target)
    except (OSError, TypeError):
        code = getattr(resolve_trial_target, "__code__", None)
        payload = "\0".join((
            str(getattr(resolve_trial_target, "__module__", "")),
            str(getattr(resolve_trial_target, "__qualname__", "")),
            bytes(getattr(code, "co_code", b"")).hex(),
            repr(tuple(getattr(code, "co_names", ()))),
        ))
    return "provider-trial-resolver-sha256:" + sha256(
        payload.encode("utf-8")
    ).hexdigest()


from .resource_trial_policy import (
    _parse_aware_time, retry_plan as _provider_trial_retry_plan,
)


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

    def _runtime_incompatible_models(
        self,
        *,
        credential_env: str,
        model_rows: dict[str, list[ModelTrial]],
    ) -> dict[str, str]:
        pool = self.state_root / "application" / "llm_resource_pool"
        try:
            manifest = json.loads((pool / "resources.json").read_text(encoding="utf-8"))
            health_doc = json.loads(
                (pool / "runtime" / "health.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        resources = manifest.get("resources") if isinstance(manifest, dict) else []
        health = health_doc.get("resources") if isinstance(health_doc, dict) else {}
        if not isinstance(resources, list) or not isinstance(health, dict):
            return {}

        invalidated: dict[str, str] = {}
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            if str(resource.get("credential_env") or "") != str(credential_env):
                continue
            model = str(resource.get("model") or "")
            resource_id = str(resource.get("resource_id") or "")
            row = health.get(resource_id)
            if not model or not resource_id or not isinstance(row, dict):
                continue
            code = str(row.get("last_error_code") or "")
            failure_at = _parse_aware_time(row.get("last_failure_at"))
            if code not in {"HTTP_404", "HTTP_410"} or failure_at is None:
                continue
            successful_at = [
                parsed
                for trial in model_rows.get(model, [])
                if trial.status == "COMPLETED"
                for parsed in [_parse_aware_time(getattr(trial, "completed_at", None))]
                if parsed is not None
            ]
            if successful_at and max(successful_at) >= failure_at:
                continue
            invalidated[model] = (
                f"llm-runtime-health:{resource_id}:{code}:{failure_at.isoformat()}"
            )
        return invalidated

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
        from .provider_trial_executor import ProviderTrialExecutor
        from .resource_trial_policy import ResourceTrialPolicy
        from .resource_trial_coordinator import ResourceTrialCoordinator
        from .resource_feedback_consumer import ResourceFeedbackConsumer
        leads = self.scout.provider_leads()
        credentials = {
            lead.credential_env: str(os.environ.get(lead.credential_env) or "")
            for lead in leads
        }
        return ResourceTrialCoordinator(
            scout=self, policy=ResourceTrialPolicy(),
            executor=ProviderTrialExecutor(
                resolve_target=resolve_trial_target,
                run_trial=run_bounded_provider_trial,
                to_trials=model_trials_from_measurements,
            ),
            recorder=self._outcome_recorder(),
            feedback=ResourceFeedbackConsumer(
                state_root=self.state_root, scout=self.scout,
                persist_selection=self._persist_provider_selection,
            ),
            plan_provider_trials=plan_provider_trials,
            resolver_evidence_ref=_provider_trial_resolver_evidence_ref(),
        ).run(now=now, provider_limit=provider_limit, credentials=credentials)

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

    def _outcome_recorder(self):
        from .resource_outcome_recorder import ResourceOutcomeRecorder
        return ResourceOutcomeRecorder(
            state_root=self.state_root, scout=self.scout,
            write_json=self._atomic_json,
        )

    def _persist_trial_receipt(self, row: dict[str, Any]) -> None:
        self._outcome_recorder()._persist_trial_receipt(row)

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
        return self._outcome_recorder().record_acquisition_outcome(
            plan_id=plan_id, lead_id=lead_id, status=status, now=now,
            friction_kind=friction_kind, source=source,
            detail_summary=detail_summary, evidence_refs=evidence_refs,
        )

    def acquisition_outcomes(self) -> list[ResourceAcquisitionOutcome]:
        return self._outcome_recorder().acquisition_outcomes()

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
