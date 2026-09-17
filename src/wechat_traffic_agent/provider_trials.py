"""Bounded empirical trials for verified low-cost model providers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
import time
from typing import Any, Callable

import httpx
from agent_runtime.resource_discovery import ModelTrial


@dataclass(frozen=True)
class ProviderTrialSpec:
    credential_env: str
    provider_label: str
    endpoint: str
    model_id: str


@dataclass(frozen=True)
class SyntheticTrialCase:
    case_id: str
    prompt: str
    scorer: Callable[[str], float]

def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trial time requires timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _score_label(text: str) -> float:
    token = re.sub(r"[^A-Z]", "", str(text).upper())
    return 1.0 if token == "POSITIVE" else 0.0


def _score_json(text: str) -> float:
    try:
        raw = str(text).strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        row = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0
    checks = (
        str(row.get("asset", "")).upper() == "SOL",
        float(row.get("change_pct", -1)) == 5.0,
        str(row.get("direction", "")).lower() == "up",
    )
    return sum(bool(item) for item in checks) / len(checks)

def _score_neutral_rewrite(text: str) -> float:
    value = " ".join(str(text).split())
    low = value.lower()
    words = re.findall(r"\b[\w%.-]+\b", value)
    checks = (
        "btc" in low,
        "3%" in low,
        len(words) <= 12,
        not any(token in low for token in ("exploded", "proving", "totally", "bulls have won")),
    )
    return sum(bool(item) for item in checks) / len(checks)


TRIAL_CASES = (
    SyntheticTrialCase(
        "sentiment-v1",
        "Return exactly one token: POSITIVE, NEGATIVE, or NEUTRAL. "
        "Text: Bitcoin gained 5% after strong spot ETF inflows.",
        _score_label,
    ),
    SyntheticTrialCase(
        "extract-v1",
        "Return only compact JSON with keys asset, change_pct, direction. "
        "Text: SOL moved from $180 to $189, a 5% increase.",
        _score_json,
    ),
    SyntheticTrialCase(
        "neutral-rewrite-v1",
        "Rewrite into one neutral factual sentence of at most 12 words, preserving BTC and 3%: "
        "BTC absolutely exploded 3% today, proving bulls have totally won.",
        _score_neutral_rewrite,
    ),
)


def resolve_trial_spec(plan: dict[str, Any]) -> ProviderTrialSpec | None:
    credential_env = str(plan.get("credential_env") or "")
    if credential_env != "NVIDIA_API_KEY":
        return None
    return ProviderTrialSpec(
        credential_env=credential_env,
        provider_label=str(plan.get("provider_label") or "NVIDIA NIM"),
        endpoint=os.environ.get(
            "CONTENT_NVIDIA_TRIAL_ENDPOINT",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        model_id=os.environ.get(
            "CONTENT_NVIDIA_TRIAL_MODEL",
            "meta/llama-3.3-70b-instruct",
        ),
    )


def trial_id(*, plan_id: str, model_id: str, case_id: str) -> str:
    raw = f"{plan_id}\0{model_id}\0{case_id}\0provider-model-trial.v1"
    digest = sha256(raw.encode()).hexdigest()[:24]
    return "model-trial:" + digest


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def execute_case(
    *,
    plan: dict[str, Any],
    spec: ProviderTrialSpec,
    case: SyntheticTrialCase,
    opportunity_id: str,
    now: datetime,
    evidence_refs: tuple[str, ...],
    observed_cost: float | None,
    post: Callable[..., Any] | None = None,
) -> tuple[ModelTrial, dict[str, Any]]:
    api_key = os.environ.get(spec.credential_env, "")
    if not api_key:
        raise RuntimeError(f"missing credential environment: {spec.credential_env}")
    started_at = _utc_iso(now)
    start = time.monotonic()
    response_text = ""
    prompt_tokens = completion_tokens = None
    error_code = ""
    status = "FAILED"
    quality = None
    response_digest = ""
    try:
        sender = post or httpx.post
        response = sender(
            spec.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": spec.model_id,
                "messages": [{"role": "user", "content": case.prompt}],
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 128,
                "stream": False,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        response_text = str(((choices[0].get("message") or {}).get("content")) or "") if choices else ""
        if not response_text:
            raise ValueError("provider returned no assistant text")
        usage = payload.get("usage") or {}
        prompt_tokens = _optional_int(usage.get("prompt_tokens"))
        completion_tokens = _optional_int(usage.get("completion_tokens"))
        quality = round(float(case.scorer(response_text)), 6)
        response_digest = sha256(response_text.encode()).hexdigest()
        status = "COMPLETED"
    except httpx.TimeoutException:
        error_code = "TIMEOUT"
    except httpx.HTTPStatusError as exc:
        error_code = f"HTTP_{exc.response.status_code}"
    except (httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        error_code = type(exc).__name__.upper()
    latency_ms = round((time.monotonic() - start) * 1000.0, 3)
    completed_at = _utc_iso(datetime.now(timezone.utc))
    refs = list(evidence_refs)
    refs.append(str(plan.get("plan_id") or ""))
    if response_digest:
        refs.append("provider-response-sha256:" + response_digest)
    trial = ModelTrial(
        trial_id=trial_id(
            plan_id=str(plan["plan_id"]), model_id=spec.model_id, case_id=case.case_id
        ),
        opportunity_id=opportunity_id,
        task_class=str(plan.get("task_class") or "simple_public_text"),
        status=status,
        quality_score=quality,
        latency_ms=latency_ms,
        observed_cost=observed_cost,
        evidence_refs=tuple(dict.fromkeys(x for x in refs if x)),
        provider_lead_id=str(plan.get("lead_id") or ""),
        provider_label=spec.provider_label,
        model_id=spec.model_id,
        started_at=started_at,
        completed_at=completed_at,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_code=error_code,
    )
    receipt = {
        "trial_id": trial.trial_id,
        "case_id": case.case_id,
        "provider_label": spec.provider_label,
        "model_id": spec.model_id,
        "endpoint": spec.endpoint,
        "status": status,
        "quality_score": quality,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "error_code": error_code,
        "response_sha256": response_digest or None,
        "response_excerpt": response_text[:500] if response_text else "",
        "data_policy": str(plan.get("data_policy") or ""),
    }
    return trial, receipt


__all__ = ["ProviderTrialSpec", "SyntheticTrialCase", "TRIAL_CASES", "execute_case", "resolve_trial_spec", "trial_id"]
