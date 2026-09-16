"""Evidence adequacy and bounded enrichment before social expression."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_loop import ReceiptRuntime, _put, _structured
from .sources.runtime_web import RuntimeWebSearch


class SocialEvidencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["READY", "SEEK", "QUESTION", "SKIP"]
    supported_facts: tuple[str, ...] = Field(max_length=4)
    missing_evidence: tuple[str, ...] = Field(max_length=4)
    search_query: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=10, max_length=600)

    @model_validator(mode="after")
    def evidence_shape(self) -> "SocialEvidencePlan":
        if self.action == "SEEK" and not self.missing_evidence:
            raise ValueError("SEEK requires at least one concrete evidence gap")
        if self.action in {"READY", "QUESTION"} and not self.supported_facts:
            raise ValueError(f"{self.action} requires at least one concrete supported fact")
        return self


class SocialEvidenceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["DRAFT", "QUESTION", "SKIP"]
    useful_facts: tuple[str, ...] = Field(max_length=6)
    rationale: str = Field(min_length=10, max_length=600)

    @model_validator(mode="after")
    def information_value(self) -> "SocialEvidenceResolution":
        if self.action in {"DRAFT", "QUESTION"} and not self.useful_facts:
            raise ValueError(f"{self.action} requires at least one concrete useful fact")
        return self


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


def _mentioned_crypto_symbols(cognition: dict[str, Any], artifact: dict[str, Any]) -> tuple[str, ...]:
    text = json.dumps({"cognition": cognition, "artifact": artifact}, ensure_ascii=False).upper()
    known = ("BTC", "ETH", "SOL", "XRP", "BNB")
    found = tuple(symbol for symbol in known if re.search(rf"(?<![A-Z]){symbol}(?![A-Z])", text))
    return found or known


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current / previous) - 1.0) * 100.0


async def collect_okx_social_market_context(now: datetime, *, symbols: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Fetch current and change evidence from OKX public no-auth endpoints."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("social market evidence timestamp must be timezone-aware")
    symbols = tuple(dict.fromkeys(symbols or ("BTC", "ETH", "SOL", "XRP", "BNB")))
    supported = {"BTC", "ETH", "SOL", "XRP", "BNB"}
    symbols = tuple(symbol for symbol in symbols if symbol in supported)
    if not symbols:
        raise ValueError("at least one supported crypto symbol is required")

    spot_url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
    oi_url = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP"
    spot_payload, oi_payload = await asyncio.gather(
        asyncio.to_thread(_read_json_url, spot_url),
        asyncio.to_thread(_read_json_url, oi_url),
    )
    spots = {str(row.get("instId")): row for row in spot_payload.get("data", []) if isinstance(row, dict)}
    oi_rows = {str(row.get("instId")): row for row in oi_payload.get("data", []) if isinstance(row, dict)}

    async def derivatives(symbol: str) -> tuple[str, dict[str, Any]]:
        inst_id = f"{symbol}-USDT-SWAP"
        urls = {
            "funding_current": "https://www.okx.com/api/v5/public/funding-rate?" + urllib.parse.urlencode({"instId": inst_id}),
            "funding_history": "https://www.okx.com/api/v5/public/funding-rate-history?" + urllib.parse.urlencode({"instId": inst_id, "limit": 3}),
            "oi_history": "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?" + urllib.parse.urlencode({"ccy": symbol, "period": "1H"}),
            "spot_candles": "https://www.okx.com/api/v5/market/candles?" + urllib.parse.urlencode({"instId": f"{symbol}-USDT", "bar": "1H", "limit": 3}),
        }
        values: dict[str, Any] = {}
        failures: list[dict[str, str]] = []
        for name, url in urls.items():
            try:
                payload = await asyncio.to_thread(_read_json_url, url)
                values[name] = payload.get("data", [])
            except Exception as exc:  # noqa: BLE001 - partial enrichment is useful
                values[name] = []
                failures.append({"symbol": symbol, "source": name, "error_type": type(exc).__name__})
        values["failures"] = failures
        return symbol, values

    derivative_rows = dict(await asyncio.gather(*(derivatives(symbol) for symbol in symbols)))
    failures: list[dict[str, str]] = []
    details: list[dict[str, Any]] = []
    summaries: list[str] = []
    for symbol in symbols:
        spot = spots.get(f"{symbol}-USDT")
        if not spot:
            failures.append({"symbol": symbol, "source": "spot-ticker", "error_type": "MISSING"})
            continue
        derived = derivative_rows.get(symbol) or {}
        failures.extend(derived.get("failures") or [])
        last = float(spot.get("last") or 0)
        opened = float(spot.get("open24h") or 0)
        change_24h = _pct_change(last, opened)
        quote_volume_24h = float(spot.get("volCcy24h") or 0)
        current_oi = oi_rows.get(f"{symbol}-USDT-SWAP") or {}
        oi_ccy = float(current_oi["oiCcy"]) if current_oi.get("oiCcy") not in (None, "") else None

        current_funding_rows = derived.get("funding_current") or []
        current_funding = current_funding_rows[0] if current_funding_rows and isinstance(current_funding_rows[0], dict) else {}
        funding_rate = float(current_funding["fundingRate"]) if current_funding.get("fundingRate") not in (None, "") else None
        funding_history = [row for row in (derived.get("funding_history") or []) if isinstance(row, dict)]
        realized_funding = [float(row["fundingRate"]) for row in funding_history[:2] if row.get("fundingRate") not in (None, "")]

        oi_history = derived.get("oi_history") or []
        oi_now = float(oi_history[0][1]) if len(oi_history) >= 1 and len(oi_history[0]) >= 2 else None
        oi_prev = float(oi_history[1][1]) if len(oi_history) >= 2 and len(oi_history[1]) >= 2 else None
        oi_change_1h = _pct_change(oi_now, oi_prev)

        candles = derived.get("spot_candles") or []
        close_now = float(candles[0][4]) if len(candles) >= 1 and len(candles[0]) >= 5 else None
        close_prev = float(candles[1][4]) if len(candles) >= 2 and len(candles[1]) >= 5 else None
        price_change_1h = _pct_change(close_now, close_prev)
        quote_volume_1h = float(candles[0][7]) if len(candles) >= 1 and len(candles[0]) >= 8 else None

        row = {
            "symbol": symbol,
            "spot_last_usdt": last,
            "spot_change_24h_pct": change_24h,
            "spot_quote_volume_24h_usdt": quote_volume_24h,
            "spot_change_since_previous_hour_close_pct": price_change_1h,
            "spot_quote_volume_current_hour_usdt": quote_volume_1h,
            "swap_funding_rate": funding_rate,
            "swap_last_realized_funding_rate": realized_funding[0] if realized_funding else None,
            "swap_previous_realized_funding_rate": realized_funding[1] if len(realized_funding) > 1 else None,
            "swap_open_interest_base_ccy": oi_ccy,
            "swap_open_interest_change_latest_hour_pct": oi_change_1h,
            "spot_ts": spot.get("ts"),
            "funding_ts": current_funding.get("ts"),
            "oi_ts": current_oi.get("ts"),
        }
        details.append(row)
        bits = [f"{symbol}: {last:g} USDT"]
        if change_24h is not None:
            bits.append(f"24h {change_24h:+.2f}%")
        if price_change_1h is not None:
            bits.append(f"1h {price_change_1h:+.2f}%")
        if quote_volume_1h is not None:
            bits.append(f"current-hour spot quote volume {quote_volume_1h:,.0f} USDT")
        if funding_rate is not None:
            bits.append(f"current swap funding {funding_rate * 100:+.4f}%")
        if realized_funding:
            prior = f", prior {realized_funding[1] * 100:+.4f}%" if len(realized_funding) > 1 else ""
            bits.append(f"last realized funding {realized_funding[0] * 100:+.4f}%{prior}")
        if oi_change_1h is not None:
            bits.append(f"latest-hour open-interest change {oi_change_1h:+.2f}%")
        summaries.append("; ".join(bits))

    revision = hashlib.sha256(json.dumps(details, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "social-okx-market-evidence.v2",
        "status": "AVAILABLE" if details else "UNAVAILABLE",
        "source": "OKX public API",
        "observed_at": now.isoformat(),
        "source_revision": "sha256:" + revision,
        "symbols": list(symbols),
        "evidence_refs": [
            "okx-public:market-tickers:sha256:" + revision,
            "okx-public:funding-rate:sha256:" + revision,
            "okx-public:funding-rate-history:sha256:" + revision,
            "okx-public:open-interest:sha256:" + revision,
            "okx-public:open-interest-history:sha256:" + revision,
            "okx-public:spot-candles:sha256:" + revision,
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
        "READY only when the supplied cognition/artifact support at least one specific useful public claim without making the main value a list of missing data; put those concrete facts in supported_facts. "
        "SEEK when one or more concrete missing public facts could materially resolve the uncertainty and are worth looking up now; preserve any already-supported facts in supported_facts. "
        "QUESTION only when there is at least one concrete known fact to anchor a genuinely useful open question; put those known facts in supported_facts. "
        "SKIP when there is no meaningful information gain and no useful question. Do not reward posting frequency. Return JSON only.\n"
        + json.dumps({"cognition": cognition, "artifact": artifact}, ensure_ascii=False)
    )
    plan = await _structured(model_runtime, plan_prompt, SocialEvidencePlan)
    supplemental: dict[str, Any] | None = None
    if plan.action == "SEEK":
        collector = evidence_collector
        if collector is not None:
            try:
                supplemental = await collector(now)
            except Exception as exc:  # noqa: BLE001 - failure should lead to question/skip, not fake certainty
                supplemental = {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "evidence_refs": []}
        elif _crypto_context(cognition, artifact):
            try:
                supplemental = await collect_okx_social_market_context(
                    now, symbols=_mentioned_crypto_symbols(cognition, artifact)
                )
            except Exception as exc:  # noqa: BLE001 - failure should lead to question/skip, not fake certainty
                supplemental = {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "evidence_refs": []}
        elif plan.search_query:
            try:
                rows = await RuntimeWebSearch(runtime).search(query=plan.search_query[:180], now=now)
                refs = [ref for row in rows for ref in (row.get("evidence_refs") or [])]
                supplemental = {
                    "schema_version": "social-web-evidence.v1",
                    "status": "AVAILABLE" if rows else "UNAVAILABLE",
                    "source": "runtime:web.search+fetch",
                    "observed_at": now.isoformat(),
                    "evidence_refs": list(dict.fromkeys(str(ref) for ref in refs)),
                    "rows": rows[:4],
                    "summary": " | ".join(str(row.get("summary") or "")[:800] for row in rows[:4]),
                }
            except Exception as exc:  # noqa: BLE001 - search failure should not manufacture certainty
                supplemental = {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "evidence_refs": []}
        else:
            supplemental = {"status": "NO_DEDICATED_SOURCE", "evidence_refs": []}

        resolution_prompt = (
            "SOCIAL_EVIDENCE_RESOLUTION: One bounded evidence task has now been attempted. Choose DRAFT, QUESTION, or SKIP; do not ask for another lookup in this cycle. "
            "DRAFT only if the original plus supplemental evidence now support a specific useful claim, and list the concrete facts that make the post worth reading in useful_facts. "
            "QUESTION only when at least one concrete known fact anchors a useful unresolved question; list those known facts in useful_facts and do not smuggle an answer into the question. "
            "SKIP when the lookup did not create enough information value. Never make a list of missing data the post's main value. Return JSON only.\n"
            + json.dumps({"plan": plan.model_dump(mode="json"), "cognition": cognition,
                          "artifact": artifact, "supplemental_evidence": supplemental}, ensure_ascii=False)
        )
        resolution = await _structured(model_runtime, resolution_prompt, SocialEvidenceResolution)
    elif plan.action == "READY":
        resolution = SocialEvidenceResolution(action="DRAFT", useful_facts=plan.supported_facts, rationale=plan.rationale)
    elif plan.action == "QUESTION":
        resolution = SocialEvidenceResolution(action="QUESTION", useful_facts=plan.supported_facts, rationale=plan.rationale)
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
