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


def _parse_time(value: Any) -> datetime | None:
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


def _runtime_health(state_root: Path) -> dict[str, dict[str, Any]]:
    path = (
        Path(state_root)
        / "application"
        / "llm_resource_pool"
        / "runtime"
        / "health.json"
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("resources") if isinstance(raw, dict) else {}
        return {
            str(key): dict(value)
            for key, value in (rows or {}).items()
            if isinstance(value, dict)
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


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
    """Persist an intentionally empty API-provider pool.

    Ferro cognition is ChatGPT-only. Historical provider discovery/trial records
    remain evidence, but NVIDIA, Cerebras, OpenRouter and DeepSeek are never
    promoted into executable LLM resources again.
    """
    del minimum_quality, minimum_completed
    state_root = Path(state_root)
    now = now or datetime.now(timezone.utc)
    observed_at = _iso(now)
    payload = {
        "schema_version": "llm-resource-pool.v1",
        "observed_at": observed_at,
        "resource_count": 0,
        "resources": [],
        "provider_mode": "AGENT_RUNTIME_CHATGPT_ONLY",
        "disabled_provider_ids": [
            "nvidia",
            "cerebras",
            "openrouter",
            "deepseek",
        ],
    }
    folder = state_root / "application" / "llm_resource_pool"
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_json(folder / "resources.json", payload)
    return {
        "status": "CHATGPT_ONLY",
        "observed_at": observed_at,
        "resource_count": 0,
        "free_count": 0,
        "paid_count": 0,
        "windfall_count": 0,
        "resource_ids": [],
        "suppressed_runtime_invalid_count": 0,
        "suppressed_runtime_invalid_resource_ids": [],
        "disabled_provider_ids": list(payload["disabled_provider_ids"]),
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
