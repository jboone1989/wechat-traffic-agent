"""Bounded all-market Binance spot observations; volatile snapshots only."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import httpx
from .live_cache import live_cache_root

URL = "https://api.binance.com/api/v3/ticker/24hr"
STABLE_BASES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "USD1", "EURI"}

def market_candidates(rows, now, *, limit=24, min_quote_volume=1000000):
    usable = []
    for row in rows:
        pair = str(row.get("symbol", ""))
        if not pair.endswith("USDT") or not re.fullmatch(r"[A-Z0-9]{1,15}USDT", pair):
            continue
        base = pair[:-4]
        if base in STABLE_BASES or base in {"BTC", "ETH"}:
            continue
        try:
            change = Decimal(row["priceChangePercent"])
            volume = Decimal(row["quoteVolume"])
            price = Decimal(row["lastPrice"])
            stamp = int(row["closeTime"])
            if (not all(v.is_finite() for v in (change, volume, price)) or price <= 0
                    or volume < min_quote_volume or not 0 <= now.timestamp() * 1000 - stamp <= 120000):
                continue
        except (KeyError, ValueError, TypeError, InvalidOperation):
            continue
        usable.append((row, change, volume))
    ranked = [sorted(usable, key=lambda x: x[1], reverse=True),
              sorted(usable, key=lambda x: x[1]),
              sorted(usable, key=lambda x: x[2], reverse=True)]
    selected = {}
    for index in range((limit + 2) // 3):
        for group in ranked:
            if index < len(group):
                row = group[index][0]
                selected[row["symbol"]] = row
    candidates = []
    for pair, row in list(selected.items())[:limit]:
        facts = {key: row[key] for key in ("symbol", "lastPrice", "priceChangePercent", "quoteVolume", "openTime", "closeTime")}
        revision = sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        observed = datetime.fromtimestamp(int(row["closeTime"]) / 1000, timezone.utc).isoformat()
        summary = json.dumps({"venue": "Binance spot", "window": "rolling 24h",
            "facts": facts, "scope": "Price change and turnover only; no measured inflows, OI or cause."}, ensure_ascii=False)
        candidates.append({"evidence_id": "binance-alt-market:" + pair + ":" + revision[:24],
            "source_kind": "public-api", "source": "binance-spot-all-market",
            "title": pair + " rolling 24h price and turnover observation", "summary": summary,
            "evidence_text": summary, "url": URL + "?symbol=" + pair,
            "observed_at": observed, "published_at": None, "source_revision": "sha256:" + revision,
            "evidence_refs": ["binance-spot:" + revision]})
    return candidates

async def collect_altcoin_candidates(state_root, now, *, client=None):
    path = live_cache_root(state_root) / "binance_alt_market" / "latest.json"
    try:
        cached = json.loads(path.read_text())
        if 0 <= (now - datetime.fromisoformat(cached["fetched_at"])).total_seconds() <= 90:
            return market_candidates(cached["rows"], now)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    owned = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        response = await client.get(URL)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("Binance all-market ticker must be a list")
        observed = datetime.now(timezone.utc)
        result = market_candidates(rows, observed)
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps({"fetched_at": observed.isoformat(), "rows": rows}))
        pending.replace(path)
        return result
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    finally:
        if owned:
            await client.aclose()
