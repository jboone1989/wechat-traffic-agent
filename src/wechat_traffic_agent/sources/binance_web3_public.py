"""Official Binance public Web3 endpoints -> bounded research evidence.

Read-only observations. No wallet auth, Square key, cognition or publishing.
Source paths pinned to binance-skills-hub commit 9960c675387b (2026-10-09).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4
from pathlib import Path
from .live_cache import live_cache_root
from typing import Any

import httpx

BASE = "https://web3.binance.com/bapi/defi"
RWA = ("https://www.binance.com/bapi/defi/v1/public/wallet-direct/"
       "buw/wallet/market/token/rwa/stock/detail/list/ai")


@dataclass(frozen=True)
class ReadSpec:
    name: str
    method: str
    url: str
    params: dict


PUBLIC_READS = (
    ReadSpec("spot-btc-24h", "GET", "https://api.binance.com/api/v3/ticker/24hr", {"symbol": "BTCUSDT"}),
    ReadSpec("web3-smart-money", "POST", BASE + "/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai", {"chainId": "56", "page": 1, "pageSize": 10}),
    ReadSpec("web3-smart-inflow", "POST", BASE + "/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query/ai", {"chainId": "56", "period": "24h", "tagType": 2}),
    ReadSpec("web3-token-rank", "POST", BASE + "/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai", {"chainId": "56", "rankType": 10, "page": 1, "size": 10}),
    ReadSpec("web3-token-search", "GET", BASE + "/v5/public/wallet-direct/buw/wallet/market/token/search/ai", {"chainId": "56", "keyword": "BNB"}),
    ReadSpec("web3-trader-leaderboard", "GET", BASE + "/v1/public/wallet-direct/market/leaderboard/query/ai", {"chainId": "56", "period": "7d", "tag": "ALL", "pageNo": 1, "pageSize": 5}),
    ReadSpec("web3-token-dynamic", "GET", BASE + "/v4/public/wallet-direct/buw/wallet/market/token/dynamic/info/ai", {"chainId": "56", "contractAddress": "0x55d398326f99059ff775485246999027b3197955"}),
    ReadSpec("web3-token-audit", "POST", BASE + "/v1/public/wallet-direct/security/token/audit", {"binanceChainId": "56", "contractAddress": "0x55d398326f99059ff775485246999027b3197955"}),
    ReadSpec("web3-social-hype", "GET", BASE + "/v1/public/wallet-direct/buw/wallet/market/token/pulse/social/hype/rank/leaderboard/ai", {"chainId": "56", "targetLanguage": "en", "timeRange": 1}),
    ReadSpec("web3-topic-rush", "GET", BASE + "/v2/public/wallet-direct/buw/wallet/market/token/social-rush/rank/list/ai", {"chainId": "56", "rankType": 10, "sort": 10}),
    ReadSpec("web3-rwa-stocks", "GET", RWA, {}),
)


def _rows(payload: Any, spec: ReadSpec) -> list[dict]:
    if spec.name == "spot-btc-24h":
        if not isinstance(payload, dict) or payload.get("symbol") != "BTCUSDT":
            raise ValueError("spot ticker shape mismatch")
        return [payload]
    if not isinstance(payload, dict) or str(payload.get("code")) != "000000":
        raise ValueError("Binance Web3 response code not successful")
    data = payload.get("data")
    if spec.name in {"web3-token-dynamic", "web3-token-audit"} and isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for name in ("tokens", "leaderBoardList", "data", "list", "rows"):
            if isinstance(data.get(name), list):
                return [row for row in data[name] if isinstance(row, dict)]
    raise ValueError("Binance Web3 data is missing an expected list")


def _scalar_fields(row: dict) -> dict:
    names = ("symbol", "tokenName", "ticker", "chainId", "contractAddress",
             "ca", "price", "lastPrice", "priceChangePercent", "quoteVolume",
             "percentChange24h", "priceChangeRate", "volume24h", "volume",
             "marketCap", "liquidity", "netInflow", "inflow", "signalId",
             "smartSignalType", "tokenRiskLevel", "multiplier",
             "topicNetInflow", "createTime", "address", "realizedPnl",
             "realizedPnlPercent", "winRate", "riskLevel", "riskLevelEnum",
             "hasResult", "isSupported", "aggPrice", "volume24hBuy", "volume24hSell")
    clean = {key: row[key] for key in names
             if key in row and isinstance(row[key], (int, float, str, bool))}
    meta = row.get("metaInfo")
    if isinstance(meta, dict):
        clean.update({key: meta[key] for key in ("symbol", "chainId")
                      if key in meta and isinstance(meta[key], (str, int))})
    social = row.get("socialHypeInfo")
    if isinstance(social, dict):
        clean.update({key: social[key] for key in ("socialHype", "sentiment")
                      if key in social and isinstance(social[key], (str, int, float))})
    if isinstance(row.get("name"), dict):
        clean["topic"] = str(row["name"].get("topicNameEn") or "")[:100]
    if isinstance(row.get("aiSummary"), dict):
        clean["ai_summary"] = str(row["aiSummary"].get("aiSummaryEn") or "")[:250]
    return {k: str(v)[:140] for k, v in clean.items()}


def _observation(spec: ReadSpec, data: Any, now: datetime) -> dict:
    rows = _rows(data, spec)
    if not rows:
        raise ValueError("no observations returned")
    if spec.name == "web3-rwa-stocks":
        selected = [r for r in rows if str(r.get("ticker", "")).upper() in {"TSLA", "NVDA", "AAPL", "MSFT"}][:4]
        samples = selected or rows[:3]
    else:
        samples = rows[:5]
    selected = [_scalar_fields(x) for x in samples]
    if not any(selected):
        raise ValueError("no usable primitive evidence fields")
    identity = sha256(json.dumps([spec.name, selected], sort_keys=True).encode()).hexdigest()
    return {
        "source": spec.name, "api_url": spec.url,
        "parameters": spec.params, "row_count": len(rows),
        "sample": selected, "observed_at": now.isoformat(),
        "revision": "sha256:" + identity,
        "interpretation": "Source snapshot only; rankings and signals do not establish returns or causes.",
    }


async def collect_binance_public(client: httpx.AsyncClient | None = None, *, now=None) -> dict:
    observed = now or datetime.now(timezone.utc)
    own = client is None
    client = client or httpx.AsyncClient(timeout=12, follow_redirects=False,
                                        headers={"Accept": "application/json",
                                                 "User-Agent": "binance-web3/2.0 (Skill)"})
    observations, failures = [], []
    try:
        for spec in PUBLIC_READS:
            try:
                params = dict(spec.params)
                if spec.name == "web3-token-audit":
                    params["requestId"] = str(uuid4())
                kw = {"params": params} if spec.method == "GET" else {"json": params}
                response = await client.request(spec.method, spec.url, **kw)
                response.raise_for_status()
                observations.append(_observation(spec, response.json(), observed))
            except (httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                failures.append({"source": spec.name, "error": type(exc).__name__})
    finally:
        if own:
            await client.aclose()
    return {"observed_at": observed.isoformat(), "observations": observations,
            "failures": failures, "publication_authority": False}


def save_binance_snapshot(state_root: Path, report: dict) -> Path:
    path = live_cache_root(state_root) / "binance_web3" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    pending.replace(path)
    return path


def read_binance_candidates(state_root: Path, now: datetime, max_age_seconds=3600) -> list[dict]:
    path = live_cache_root(state_root) / "binance_web3" / "latest.json"
    if not path.exists():
        return []
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(snapshot["observed_at"])
        if abs((now - observed).total_seconds()) > max_age_seconds:
            return []
        candidates = []
        for entry in snapshot.get("observations", []):
            data = json.dumps(entry["sample"], ensure_ascii=False)
            summary = (f"{entry['source']}: {entry['row_count']} records from official Binance API; "
                       f"sample fields: {data[:1550]}")
            key = entry["revision"]
            candidates.append({
                "evidence_id": "binance:" + key[7:39], "source_kind": "public-api",
                "source": entry["source"], "title": "Binance read-only data: " + entry["source"],
                "summary": summary[:1990], "evidence_text": data[:10000],
                "url": entry["api_url"], "observed_at": snapshot["observed_at"],
                "published_at": None, "source_revision": key,
                "evidence_refs": ["binance-web3:" + key],
            })
        return candidates
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return []
