"""Build a secret-free Runtime LLM resource manifest from verified provider evidence."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import mean
from typing import Any

from agent_runtime.resource_discovery import ResourceScout
from agent_runtime.resource_trials import trial_target_for_credential


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("LLM resource sync time requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _deepseek_base_url() -> str:
    raw = str(
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("VIRTUALBRAIN_LLM_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    if raw.endswith("/anthropic"):
        return raw
    if raw.endswith("/v1"):
        raw = raw[:-3].rstrip("/")
    return raw + "/anthropic"


def _optional_float(name: str) -> float | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    return max(0.0, float(raw))


def _optional_time(name: str) -> str | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _resource_id(provider_id: str, model: str) -> str:
    digest = sha256(f"{provider_id}\0{model}".encode()).hexdigest()[:20]
    return f"llm-resource:{provider_id}:{digest}"


def _env_metadata(credential_env: str) -> dict[str, Any]:
    return {
        "quota_remaining": _optional_float(credential_env + "_QUOTA_REMAINING"),
        "quota_limit": _optional_float(credential_env + "_QUOTA_LIMIT"),
        "quota_unit": str(
            os.environ.get(credential_env + "_QUOTA_UNIT") or "REQUESTS"
        ).strip().upper(),
        "quota_reset_at": _optional_time(credential_env + "_RESET_AT"),
        "expires_at": _optional_time(credential_env + "_EXPIRES_AT"),
    }




_HIGH_VALUE_TASK_CLASSES = (
    "self_upgrade",
    "code_audit",
    "deep_research",
    "long_reflection",
    "architecture_review",
)


def model_capability_profile(
    model: str,
    *,
    reputation_book: Any | None = None,
) -> dict[str, Any]:
    """Derive capability from current external reputation, never a hand list."""
    row = reputation_book.get(model) if reputation_book is not None else None
    if row is None:
        score = 0.50
        tier = "STANDARD"
        tags = ["general_text", "externally_unranked"]
        tasks = ["general_text", "simple_public_text"]
        source = "EXTERNAL_REPUTATION_MISSING"
        windfall = False
    else:
        score = float(row.score_for("self_upgrade"))
        if score >= 0.82:
            tier = "FRONTIER"
        elif score >= 0.68:
            tier = "ADVANCED"
        else:
            tier = "STANDARD"
        tags = ["general_text", "external_reputation"]
        if float(row.coding) >= 0.70:
            tags.append("coding")
        if float(row.agentic) >= 0.70:
            tags.append("agentic")
        if float(row.human_preference) >= 0.70:
            tags.append("human_preferred")
        if float(row.popularity) >= 0.70:
            tags.append("widely_used")
        if float(row.chinese) >= 0.70:
            tags.append("chinese")
        tasks = ["general_text", "simple_public_text"]
        if score >= 0.68:
            tasks.extend(_HIGH_VALUE_TASK_CLASSES)
        source = "EXTERNAL_LEADERBOARDS"
        windfall = score >= 0.72
    return {
        "capability_tier": tier,
        "capability_score": round(score, 6),
        "capability_tags": list(dict.fromkeys(tags)),
        "capability_source": source,
        "windfall_eligible": windfall,
        "task_classes": list(dict.fromkeys(tasks)),
    }


def sync_llm_resource_pool(
    *,
    state_root: Path,
    now: datetime | None = None,
    minimum_quality: float = 0.70,
    minimum_completed: int = 2,
) -> dict[str, Any]:
    state_root = Path(state_root)
    now = now or datetime.now(timezone.utc)
    observed_at = _iso(now)
    from .model_reputation import load_model_reputation_book
    reputation_book = load_model_reputation_book(state_root)
    scout = ResourceScout(
        state_root / "application",
        lambda *_args, **_kwargs: {"results": []},
    )
    leads = {row.lead_id: row for row in scout.provider_leads()}
    verifications = {row.lead_id: row for row in scout.provider_verifications()}
    trials = scout.model_trials()
    resources: list[dict[str, Any]] = []

    stable_credential = next(
        (
            name
            for name in ("DEEPSEEK_API_KEY", "VIRTUALBRAIN_LLM_API_KEY")
            if str(os.environ.get(name) or "").strip()
        ),
        None,
    )
    if stable_credential:
        model = str(
            os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("VIRTUALBRAIN_LLM_MODEL")
            or "deepseek-flash"
        ).strip()
        resources.append({
            "resource_id": _resource_id("deepseek", model),
            "provider_id": "deepseek",
            "provider_label": "DeepSeek",
            "model": model,
            "credential_env": stable_credential,
            "protocol": "deepseek_anthropic",
            "base_url": _deepseek_base_url(),
            "lifecycle": "STABLE_PAID",
            "pricing_class": "PAID",
            "reserve": True,
            "quality_score": 0.80,
            "success_rate": 0.90,
            "mean_latency_ms": None,
            "quota_remaining": None,
            "quota_limit": None,
            "quota_unit": "CREDITS",
            "quota_reset_at": None,
            "expires_at": None,
            "task_classes": ["general_text"],
            "capability_tier": "STANDARD",
            "capability_score": 0.70,
            "capability_tags": ["general_text", "stable_reserve"],
            "capability_source": "CONFIGURED_PRIOR",
            "windfall_eligible": False,
            "evidence_refs": [
                "configured:stable-deepseek-reserve",
                "bootstrap-prior:stable-paid-resource",
            ],
            "include_usage": False,
            "metrics_source": "CONFIGURED_PRIOR",
        })

    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in trials:
        if row.provider_lead_id and row.model_id:
            grouped[(row.provider_lead_id, row.model_id)].append(row)
    for (lead_id, model), rows in sorted(grouped.items()):
        lead = leads.get(lead_id)
        verification = verifications.get(lead_id)
        if lead is None or verification is None:
            continue
        if not str(os.environ.get(lead.credential_env) or "").strip():
            continue
        target = trial_target_for_credential(lead.credential_env)
        if target is None:
            continue
        completed = [row for row in rows if row.status == "COMPLETED"]
        if len(completed) < max(1, int(minimum_completed)):
            continue
        qualities = [
            float(row.quality_score)
            for row in completed
            if row.quality_score is not None
        ]
        if not qualities:
            continue
        quality = mean(qualities)
        success_rate = len(completed) / len(rows)
        if quality < float(minimum_quality) or success_rate < 0.45:
            continue
        free_status = str(verification.free_status or "").upper()
        if free_status not in {"RECURRING_FREE", "TRIAL_CREDIT"}:
            continue
        lifecycle = (
            "RECURRING_FREE"
            if free_status == "RECURRING_FREE"
            else "TEMPORARY_FREE"
        )
        latencies = [
            float(row.latency_ms)
            for row in completed
            if row.latency_ms is not None
        ]
        provider_id = (
            "openrouter"
            if lead.credential_env == "OPENROUTER_API_KEY"
            else "nvidia"
            if lead.credential_env == "NVIDIA_API_KEY"
            else lead.official_root.replace(".", "-").lower()
        )
        metadata = _env_metadata(lead.credential_env)
        capability = model_capability_profile(
            model, reputation_book=reputation_book
        )
        resources.append({
            "resource_id": _resource_id(provider_id, model),
            "provider_id": provider_id,
            "provider_label": lead.provider_label,
            "model": model,
            "credential_env": lead.credential_env,
            "protocol": "openai_chat",
            "base_url": target.base_url,
            "lifecycle": lifecycle,
            "pricing_class": "FREE",
            "reserve": False,
            "quality_score": round(quality, 6),
            "success_rate": round(success_rate, 6),
            "mean_latency_ms": round(mean(latencies), 3) if latencies else None,
            **metadata,
            "task_classes": capability["task_classes"],
            "capability_tier": capability["capability_tier"],
            "capability_score": capability["capability_score"],
            "capability_tags": capability["capability_tags"],
            "capability_source": capability["capability_source"],
            "windfall_eligible": capability["windfall_eligible"],
            "evidence_refs": list(dict.fromkeys(
                [lead_id, *[row.trial_id for row in rows]]
            )),
            "include_usage": bool(target.include_usage),
            "metrics_source": "EMPIRICAL_TRIALS",
        })

    resources.sort(
        key=lambda row: (
            0 if row["pricing_class"] == "FREE" else 1,
            row["lifecycle"],
            -float(row["quality_score"]),
            row["resource_id"],
        )
    )
    payload = {
        "schema_version": "llm-resource-pool.v1",
        "observed_at": observed_at,
        "resource_count": len(resources),
        "resources": resources,
    }
    folder = state_root / "application" / "llm_resource_pool"
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_json(folder / "resources.json", payload)
    return {
        "status": "SYNCED",
        "observed_at": observed_at,
        "resource_count": len(resources),
        "free_count": sum(1 for row in resources if row["pricing_class"] == "FREE"),
        "paid_count": sum(1 for row in resources if row["pricing_class"] == "PAID"),
        "windfall_count": sum(1 for row in resources if row.get("windfall_eligible")),
        "resource_ids": [row["resource_id"] for row in resources],
    }



def eligible_free_resources(state_root: Path, now: datetime) -> list[dict[str, Any]]:
    """Only fresh, verified, credentialed and unexhausted free routes count."""
    path = Path(state_root) / "application" / "llm_resource_pool" / "resources.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(manifest["observed_at"]).replace("Z", "+00:00"))
        if observed.tzinfo is None or not 0 <= (now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() <= 7200:
            return []
        rows = manifest.get("resources") or []
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return []
    eligible = []
    for row in rows:
        if not isinstance(row, dict) or row.get("pricing_class") != "FREE":
            continue
        credential = str(row.get("credential_env") or "")
        if not credential or not str(os.environ.get(credential) or "").strip():
            continue
        remaining = row.get("quota_remaining")
        if remaining is not None:
            try:
                if float(remaining) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        expires = row.get("expires_at")
        if expires:
            try:
                expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if expiry.tzinfo is None or expiry.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
                    continue
            except ValueError:
                continue
        if str(row.get("metrics_source") or "") != "EMPIRICAL_TRIALS":
            continue
        eligible.append(row)
    return eligible


def pool_metrics(state_root: Path) -> dict[str, float]:
    state_root = Path(state_root)
    folder = state_root / "application" / "llm_resource_pool"
    resources: list[dict[str, Any]] = []
    try:
        raw = json.loads((folder / "resources.json").read_text(encoding="utf-8"))
        rows = raw.get("resources") if isinstance(raw, dict) else []
        resources = [row for row in rows if isinstance(row, dict)]
    except (OSError, ValueError, json.JSONDecodeError):
        resources = []
    by_id = {
        str(row.get("resource_id")): row
        for row in resources
        if row.get("resource_id")
    }
    free_count = sum(1 for row in resources if row.get("pricing_class") == "FREE")
    paid_count = sum(1 for row in resources if row.get("pricing_class") == "PAID")
    windfall_count = sum(1 for row in resources if row.get("windfall_eligible"))
    best_capability = max(
        (float(row.get("capability_score") or 0.0) for row in resources),
        default=0.0,
    )
    decisions_dir = folder / "runtime" / "decisions"
    selected_free = selected_paid = failovers = failures = 0
    paths = sorted(decisions_dir.glob("*.json"))[-64:] if decisions_dir.exists() else []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        selected = by_id.get(str(row.get("selected_resource_id") or ""))
        if selected and selected.get("pricing_class") == "FREE":
            selected_free += 1
        elif selected and selected.get("pricing_class") == "PAID":
            selected_paid += 1
        if len(row.get("attempted_resource_ids") or []) > 1:
            failovers += 1
        if str(row.get("result") or "") != "COMPLETED":
            failures += 1
    return {
        "llm_pool_resource_count": float(len(resources)),
        "llm_pool_free_resource_count": float(free_count),
        "llm_pool_paid_resource_count": float(paid_count),
        "llm_pool_windfall_resource_count": float(windfall_count),
        "llm_pool_best_capability_score": float(best_capability),
        "llm_pool_recent_free_selections": float(selected_free),
        "llm_pool_recent_paid_selections": float(selected_paid),
        "llm_pool_recent_failovers": float(failovers),
        "llm_pool_recent_route_failures": float(failures),
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


__all__ = [
    "model_capability_profile",
    "pool_metrics",
    "eligible_free_resources",
    "sync_llm_resource_pool",
]
