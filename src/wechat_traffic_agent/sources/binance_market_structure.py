"""BTC/ETH public data with synchronized completed windows and raw evidence."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import httpx

SYMBOLS = ("BTCUSDT", "ETHUSDT")


def change(before, after):
    return str((Decimal(str(after)) / Decimal(str(before)) - 1) * 100)


def analyze(report):
    observed_ms = int(datetime.fromisoformat(report["observed_at"]).timestamp() * 1000)
    histories = []
    for symbol in SYMBOLS:
        row = next((r for r in report["observations"]
                    if r["symbol"] == symbol and r["name"] == "open_interest_history"), {})
        if row.get("status") != "OK":
            raise ValueError(symbol + ": missing open interest history")
        histories.append({r["timestamp"] for r in row["data"] if r["timestamp"] <= observed_ms})
    common = set.intersection(*histories)
    if not common:
        raise ValueError("no common BTC/ETH open interest boundary")
    common_end = max(common)
    metrics = {}
    for symbol in SYMBOLS:
        rows = {row["name"]: row for row in report["observations"] if row["symbol"] == symbol}
        required = ("spot_24h", "mark_funding", "open_interest_history", "futures_klines")
        if any(rows.get(key, {}).get("status") != "OK" for key in required):
            raise ValueError(symbol + ": incomplete public market data")
        spot, funding = rows["spot_24h"]["data"], rows["mark_funding"]["data"]
        if any(abs(observed_ms - int(data[field])) > 120000
               for data, field in ((spot, "closeTime"), (funding, "time"))):
            raise ValueError(symbol + ": stale ticker or funding endpoint")
        oi = sorted(rows["open_interest_history"]["data"], key=lambda x: x["timestamp"])
        if not oi or observed_ms - oi[-1]["timestamp"] not in range(0, 600001):
            raise ValueError(symbol + ": stale open interest history")
        end = common_end
        if observed_ms - end not in range(0, 600001):
            raise ValueError(symbol + ": common open interest boundary is stale")
        start = end - 3600000
        indexed = {row["timestamp"]: row for row in oi}
        if start not in indexed or len({t for t in indexed if start <= t <= end}) != 13:
            raise ValueError(symbol + ": missing aligned 1h open interest window")
        candles = {row[0]: row for row in rows["futures_klines"]["data"]
                   if row[6] < observed_ms}
        # Price at each OI boundary is the immediately preceding candle close.
        before, after = candles[start - 300000], candles[end - 300000]
        window = [candles[t] for t in range(start, end, 300000)]
        quote = sum(Decimal(row[7]) for row in window)
        taker = sum(Decimal(row[10]) for row in window)
        previous = [candles[t] for t in range(start - 3600000, start, 300000)]
        previous_quote = sum(Decimal(row[7]) for row in previous)
        if quote <= 0 or previous_quote <= 0 or taker < 0 or taker > quote:
            raise ValueError(symbol + ": invalid quote volume evidence")
        metrics[symbol] = {
            "spot_price": spot["lastPrice"], "spot_change_24h_pct": spot["priceChangePercent"],
            "window_start": datetime.fromtimestamp(start / 1000, timezone.utc).isoformat(),
            "window_end": datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(),
            "perpetual_price_start": before[4], "perpetual_price_end": after[4],
            "perpetual_change_1h_pct": change(before[4], after[4]),
            "oi_quantity_start": indexed[start]["sumOpenInterest"],
            "oi_quantity_end": indexed[end]["sumOpenInterest"],
            "oi_quantity_change_1h_pct": change(indexed[start]["sumOpenInterest"], indexed[end]["sumOpenInterest"]),
            "latest_funding_pct": str(Decimal(funding["lastFundingRate"]) * 100),
            "next_funding_at": datetime.fromtimestamp(funding["nextFundingTime"] / 1000, timezone.utc).isoformat(),
            "quote_volume_1h_usdt": str(quote), "previous_quote_volume_1h_usdt": str(previous_quote),
            "quote_volume_ratio": str(quote / previous_quote),
            "taker_buy_quote_share_pct": str(taker / quote * 100),
        }
    if metrics[SYMBOLS[0]]["window_end"] != metrics[SYMBOLS[1]]["window_end"]:
        raise ValueError("BTC and ETH windows differ; repeat collection")
    return metrics


async def collect_market_structure(*, client=None):
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20)
    async def fetch(symbol, name, base, path, params):
        url = base + path
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return {"symbol": symbol, "name": name, "url": str(response.url),
                    "status": "OK", "data": response.json()}
        except Exception as exc:
            return {"symbol": symbol, "name": name, "url": url,
                    "status": "UNAVAILABLE", "error": type(exc).__name__}
    jobs = []
    for symbol in SYMBOLS:
        jobs.extend([
            fetch(symbol, "spot_24h", "https://api.binance.com", "/api/v3/ticker/24hr", {"symbol": symbol}),
            fetch(symbol, "mark_funding", "https://fapi.binance.com", "/fapi/v1/premiumIndex", {"symbol": symbol}),
            fetch(symbol, "open_interest_history", "https://fapi.binance.com", "/futures/data/openInterestHist",
                  {"symbol": symbol, "period": "5m", "limit": 25}),
            fetch(symbol, "futures_klines", "https://fapi.binance.com", "/fapi/v1/klines",
                  {"symbol": symbol, "interval": "5m", "limit": 31}),
        ])
    try:
        observations = await asyncio.gather(*jobs)
    finally:
        if owned:
            await client.aclose()
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "observations": observations}
    try:
        report.update(metrics=analyze(report), status="OK")
    except (ValueError, KeyError, IndexError, TypeError, ArithmeticError) as exc:
        report.update(status="INCOMPLETE", error=str(exc), metrics={})
    return report


def save_market_structure(state_root, report):
    folder = Path(state_root) / "sources" / "binance_market_structure"
    folder.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
    identity = sha256(encoded).hexdigest()
    archive = folder / (identity + ".json")
    if not archive.exists():
        archive.write_bytes(encoded)
    temporary = folder / "latest.tmp"
    temporary.write_bytes(encoded)
    temporary.replace(folder / "latest.json")
    return archive
