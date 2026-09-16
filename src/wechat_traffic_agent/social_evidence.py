"""Evidence adequacy and bounded enrichment before social expression."""
from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_loop import ReceiptRuntime, _put, _structured


class SocialEvidencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["READY", "SEEK", "QUESTION", "SKIP"]
    missing_evidence: tuple[str, ...] = Field(max_length=4)
    search_query: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=10, max_length=600)

    @model_validator(mode="after")
    def seek_requires_gap(self) -> "SocialEvidencePlan":
        if self.action == "SEEK" and not self.missing_evidence:
            raise ValueError("SEEK requires at least one concrete evidence gap")
        return self


class SocialEvidenceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["DRAFT", "QUESTION", "SKIP"]
    useful_facts: tuple[str, ...] = Field(max_length=6)
    rationale: str = Field(min_length=10, max_length=600)


EvidenceCollector = Callable[[datetime], Awaitable[dict[str, Any]]]


def _read_json_url(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "LivingRuntime-Content-Agent/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise RuntimeError("OKX public API returned a non-success response")
    return payload


def _crypto_context(cognition: dict[str, Any], artifact: dict[str, Any]) -> bool:
    text = json.dumps({"cognition": cognition, "artifact": artifact}, ensure_ascii=False).lower()
    cues = ("crypto", "bitcoin", "btc", "ethereum", "eth", "solana", "sol-", "xrp", "bnb", "okx")
    return any(cue in text for cue in cues)


async def collect_okx_social_market_context(now: datetime) -> dict[str, Any]:
    """Fetch price, spot volume, funding and OI from OKX public no-auth endpoints."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("social market evidence timestamp must be timezone-aware")
    symbols = ("BTC", "ETH", "SOL", "XRP", "BNB")
    spot_url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
    oi_url = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP"
    spot_payload, oi_payload = await asyncio.gather(
        asyncio.to_thread(_read_json_url, spot_url),
        asyncio.to_thread(_read_json_url, oi_url),
    )
    spots = {str(row.get("instId")): row for row in spot_payload.get("data", []) if isinstance(row, dict)}
    oi_rows = {str(row.get("instId")): row for row in oi_payload.get("data", []) if isinstance(row, dict)}

    async def funding(symbol: str) -> tuple[str, dict[str, Any] | None, str | None]:
        inst_id = f"{symbol}-USDT-SWAP"
        url = "https://www.okx.com/api/v5/public/funding-rate?" + urllib.parse.urlencode({"instId": inst_id})
        try:
            payload = await asyncio.to_thread(_read_json_url, url)
            rows = payload.get("data", [])
            return symbol, (rows[0] if rows and isinstance(rows[0], dict) else None), None
        except Exception as exc:  # noqa: BLE001 - partial enrichment is useful
            return symbol, None, type(exc).__name__

    funding_rows = await asyncio.gather(*(funding(symbol) for symbol in symbols))
    funding_map = {symbol: row for symbol, row, _ in funding_rows if row is not None}
    failures = [{"symbol": symbol, "source": "funding-rate", "error_type": error}
                for symbol, _row, error in funding_rows if error]

    details: list[dict[str, Any]] = []
    summaries: list[str] = []
    for symbol in symbols:
        spot = spots.get(f"{symbol}-USDT")
        if not spot:
            failures.append({"symbol": symbol, "source": "spot-ticker", "error_type": "MISSING"})
            continue
        last = float(spot.get("last") or 0)
        opened = float(spot.get("open24h") or 0)
        change = ((last / opened) - 1.0) * 100.0 if opened else None
        quote_volume = float(spot.get("volCcy24h") or 0)
        swap_id = f"{symbol}-USDT-SWAP"
        funding_row = funding_map.get(symbol) or {}
        oi_row = oi_rows.get(swap_id) or {}
        funding_rate = float(funding_row["fundingRate"]) if funding_row.get("fundingRate") not in (None, "") else None
        oi_ccy = float(oi_row["oiCcy"]) if oi_row.get("oiCcy") not in (None, "") else None
        row = {
            "symbol": symbol, "spot_last_usdt": last, "spot_change_24h_pct": change,
            "spot_quote_volume_24h_usdt": quote_volume, "swap_funding_rate": funding_rate,
            "swap_open_interest_base_ccy": oi_ccy,
            "spot_ts": spot.get("ts"), "funding_ts": funding_row.get("ts"), "oi_ts": oi_row.get("ts"),
        }
        details.append(row)
        bits = [f"{symbol}: {last:g} USDT"]
        if change is not None:
            bits.append(f"24h {change:+.2f}%")
        bits.append(f"spot quote volume 24h {quote_volume:,.0f} USDT")
        if funding_rate is not None:
            bits.append(f"swap funding {funding_rate * 100:+.4f}%")
        if oi_ccy is not None:
            bits.append(f"swap OI {oi_ccy:,.4g} {symbol}")
        summaries.append("; ".join(bits))

    revision = hashlib.sha256(json.dumps(details, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "social-okx-market-evidence.v1",
        "status": "AVAILABLE" if details else "UNAVAILABLE",
        "source": "OKX public API",
        "observed_at": now.isoformat(),
        "source_revision": "sha256:" + revision,
        "evidence_refs": [
            "okx-public:market-tickers:sha256:" + revision,
            "okx-public:funding-rate:sha256:" + revision,
            "okx-public:open-interest:sha256:" + revision,
        ],
        "summary": " | ".join(summaries),
        "details": details,
        "failures": failures,
    }


async def resolve_social_evidence(*, runtime: Any, folder: Path, cognition: dict[str, Any],
                                  artifact: dict[str, Any], now: datetime,
                                  evidence_collector: EvidenceCollector | None = None) -> dict[str, Any]:
    """Choose seek/draft/question/skip, with at most one bounded enrichment attempt."""
    path = Path(folder) / "social_evidence_decision.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass

    model_runtime = ReceiptRuntime(runtime, Path(folder) / "social-evidence-model-calls")
    plan_prompt = (
        "SOCIAL_EVIDENCE_PLAN: Decide evidence adequacy before writing any social post. This is not a writing task. "
        "READY only when the supplied cognition/artifact support at least one specific useful public claim without making the main value a list of missing data. "
        "SEEK when one or more concrete missing public facts could materially resolve the uncertainty and are worth looking up now. "
        "QUESTION when the uncertainty itself is useful enough to ask publicly without implying an unsupported answer. "
        "SKIP when there is no meaningful information gain and no useful question. Do not reward posting frequency. Return JSON only.\n"
        + json.dumps({"cognition": cognition, "artifact": artifact}, ensure_ascii=False)
    )
    plan = await _structured(model_runtime, plan_prompt, SocialEvidencePlan)
    supplemental: dict[str, Any] | None = None
    if plan.action == "SEEK":
        collector = evidence_collector
        if collector is None and _crypto_context(cognition, artifact):
            collector = collect_okx_social_market_context
        if collector is not None:
            try:
                supplemental = await collector(now)
            except Exception as exc:  # noqa: BLE001 - failure should lead to question/skip, not fake certainty
                supplemental = {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "evidence_refs": []}
        else:
            supplemental = {"status": "NO_DEDICATED_SOURCE", "evidence_refs": []}

        resolution_prompt = (
            "SOCIAL_EVIDENCE_RESOLUTION: One bounded evidence lookup has now been attempted. Choose DRAFT, QUESTION, or SKIP; do not ask for another lookup in this cycle. "
            "DRAFT only if the original plus supplemental evidence now support a specific useful claim. QUESTION when the unresolved uncertainty itself is a useful public question and the post can clearly separate known facts from what remains unknown. "
            "SKIP when the lookup did not create enough information value. Do not fill space with generic caveats. Return JSON only.\n"
            + json.dumps({"plan": plan.model_dump(mode="json"), "cognition": cognition,
                          "artifact": artifact, "supplemental_evidence": supplemental}, ensure_ascii=False)
        )
        resolution = await _structured(model_runtime, resolution_prompt, SocialEvidenceResolution)
    elif plan.action == "READY":
        resolution = SocialEvidenceResolution(action="DRAFT", useful_facts=(), rationale=plan.rationale)
    elif plan.action == "QUESTION":
        resolution = SocialEvidenceResolution(action="QUESTION", useful_facts=(), rationale=plan.rationale)
    else:
        resolution = SocialEvidenceResolution(action="SKIP", useful_facts=(), rationale=plan.rationale)

    payload = {
        "schema_version": "social-evidence-decision.v1",
        "created_at": now.isoformat(),
        "plan": plan.model_dump(mode="json"),
        "resolution": resolution.model_dump(mode="json"),
        "supplemental_evidence": supplemental,
        "publication_authority": False,
    }
    _put(path, payload)
    return payload


__all__ = [
    "SocialEvidencePlan", "SocialEvidenceResolution", "collect_okx_social_market_context",
    "resolve_social_evidence",
]
