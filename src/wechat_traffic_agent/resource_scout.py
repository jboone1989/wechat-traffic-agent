"""Content Agent orchestration for Runtime-owned autonomous resource discovery."""
from __future__ import annotations

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
    ProviderTrialMeasurement, model_trials_from_measurements, resolve_trial_target,
    run_bounded_provider_trial,
)


class ContentResourceScout:
    def __init__(self, *, state_root: Path, capability_runtime: Any) -> None:
        self.state_root = Path(state_root)
        self.capability_runtime = capability_runtime
        self.scout = ResourceScout(self.state_root / "application", self._search)

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
            new_friction = any(
                row.status == "BLOCKED_EXTERNAL"
                and datetime.fromisoformat(row.observed_at.replace("Z", "+00:00")) > last
                for row in outcomes
            )
            if not new_friction and now.astimezone(timezone.utc) - last < retry_after:
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
        payload = {
            "status": "PLANNED" if plans else "NO_VERIFIED_TRIAL_CANDIDATES",
            "observed_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "pending_verification_count": pending_count,
            "autonomous_work_pending": pending_count > 0,
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

    def run_ready_trials(self, *, now: datetime, provider_limit: int = 2) -> dict[str, Any]:
        leads = self.scout.provider_leads()
        verifications = self.scout.provider_verifications()
        outcomes = self.acquisition_outcomes()
        available = {item.credential_env for item in leads if os.environ.get(item.credential_env)}
        plans = plan_provider_trials(
            leads, verifications, available_credentials=available,
            acquisition_outcomes=outcomes, limit=max(3, int(provider_limit)),
        )
        ready = [item for item in plans if item.status == "READY_TO_TRIAL"][: max(1, min(int(provider_limit), 3))]
        existing = list(self.scout.model_trials())
        executed_measurements: list[ProviderTrialMeasurement] = []
        provider_rows: list[dict[str, Any]] = []
        for plan in ready:
            api_key = str(os.environ.get(plan.credential_env) or "")
            if not api_key:
                continue
            excluded_models: frozenset[str] = frozenset()
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
                low_quality_models = set()
                for model_id, rows in model_rows.items():
                    completed = [
                        row for row in rows
                        if row.status == "COMPLETED" and row.quality_score is not None
                    ]
                    if len(completed) >= 3:
                        mean_quality = sum(float(row.quality_score) for row in completed) / len(completed)
                        if mean_quality < 0.75:
                            low_quality_models.add(model_id)
                exhausted_models = rate_limited_models | low_quality_models
                if len(exhausted_models) >= 3:
                    error_codes = []
                    if rate_limited_models:
                        error_codes.append("HTTP_429")
                    if low_quality_models:
                        error_codes.append("QUALITY_BELOW_FLOOR")
                    provider_rows.append({
                        "provider_label": plan.provider_label,
                        "credential_env": plan.credential_env,
                        "lead_id": plan.lead_id,
                        "status": "FREE_TIER_CANDIDATES_EXHAUSTED",
                        "request_count": 0,
                        "completed_count": 0,
                        "trial_ids": [],
                        "mean_quality": None,
                        "mean_latency_ms": None,
                        "observed_cost_total": None,
                        "cost_observed_for_all_completed": False,
                        "models": sorted(exhausted_models),
                        "error_codes": error_codes,
                    })
                    continue
                excluded_models = frozenset(exhausted_models)
            try:
                target = resolve_trial_target(
                    plan.credential_env, api_key=api_key, exclude_models=excluded_models,
                )
            except Exception as exc:  # Provider catalogue failures must not block other candidates.
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
                    "error_codes": [str(getattr(exc, "code", "") or type(exc).__name__)],
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
        profiles = provider_profiles(all_trials, minimum_quality=0.75, minimum_trials=3)
        selected = select_provider(profiles, task_class="simple_public_text", quality_floor=0.75)
        selection = self._persist_provider_selection(now=now, selected=selected, trials=all_trials)
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
        disk_used_ratio = round(disk.used / disk.total, 4) if disk.total else 1.0
        engines = [name for name in ("ollama", "llama-cli", "llama-server", "llamafile") if shutil.which(name)]
        gpu_present = Path("/dev/nvidia0").exists() or Path("/dev/dri/renderD128").exists()
        reasons: list[str] = []
        if disk_free_gib < 5.0 or disk_used_ratio >= 0.90:
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
