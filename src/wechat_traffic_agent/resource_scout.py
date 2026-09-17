"""Content Agent orchestration for Runtime-owned autonomous resource discovery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from typing import Any

from agent_runtime.resource_discovery import ResourceNeed, ResourceScout, plan_provider_trials


class ContentResourceScout:
    def __init__(self, *, state_root: Path, capability_runtime: Any) -> None:
        self.state_root = Path(state_root)
        self.capability_runtime = capability_runtime
        self.scout = ResourceScout(self.state_root / "application", self._search)

    def maybe_discover(self, *, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("resource scout time requires timezone")
        runs = self._runs()
        if runs:
            last_run = runs[-1]
            last = datetime.fromisoformat(str(last_run["observed_at"]).replace("Z", "+00:00"))
            found_last_time = bool(last_run.get("new_opportunity_ids"))
            retry_after = timedelta(hours=24) if found_last_time else timedelta(minutes=30)
            if now.astimezone(timezone.utc) - last < retry_after:
                return {
                    "status": "NOT_DUE",
                    "last_observed_at": last.isoformat(),
                    "retry_after_seconds": int(retry_after.total_seconds()),
                    "opportunity_count": len(self.scout.opportunities()),
                }
        need = ResourceNeed(
            resource_kind="llm_inference",
            objective="reduce financial burn while preserving useful task quality",
            reason="paid-provider concentration exists and no low-cost alternative has trial evidence",
            constraints=(
                "public evidence only",
                "never send secrets during discovery",
                "prefer zero-cost or recurring free quota",
                "no paid signup or privilege expansion without operator approval",
            ),
        )
        created = self.scout.discover(need, now=now, max_queries=6, results_per_query=6)
        opportunities = self.scout.opportunities()
        return {
            "status": "DISCOVERED" if created else "NO_NEW_OPPORTUNITIES",
            "new_count": len(created),
            "opportunity_count": len(opportunities),
            "claimed_free_count": sum(1 for x in opportunities if x.claimed_free),
            "credential_hint_count": sum(1 for x in opportunities if x.credential_hint),
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
        return {
            "status": "VERIFIED" if rows else "NO_PENDING",
            "processed_count": len(rows),
            "verified_candidate_count": len(verified),
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
        plans = plan_provider_trials(
            leads, verifications, available_credentials=available, limit=limit
        )
        local = self.local_inference_feasibility()
        lead_by_id = {item.lead_id: item for item in leads}
        payload = {
            "status": "PLANNED" if plans else "NO_VERIFIED_TRIAL_CANDIDATES",
            "observed_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "available_credential_envs": sorted(available),
            "local_inference": local,
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
