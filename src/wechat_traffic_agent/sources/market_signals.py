"""Bounded read-only live sources for Ferro's research, not template posts."""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import json
import math
import re
import xml.etree.ElementTree as ET

import httpx

BLOCKBEATS = {
    "blockbeats-newsflash": "https://api.theblockbeats.news/v2/rss/newsflash",
    "blockbeats-articles": "https://api.theblockbeats.news/v2/rss/article",
    "panews": "https://www.panewslab.com/rss.xml?lang=en&type=NORMAL%2CNEWS",
}
BINANCE_TICKERS = "https://api.binance.com/api/v3/ticker/24hr"
BTC_ETF_FLOWS = "https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")


def _plain(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())


def parse_rss(data: bytes, *, source: str, now: datetime) -> list[dict]:
    if len(data) > 512_000 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("RSS is oversized or contains XML entities")
    result = []
    for node in ET.fromstring(data).iter():
        if node.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        fields = {child.tag.split("}")[-1]: child for child in node}
        def value(*names):
            return next(("".join(fields[name].itertext()) for name in names if name in fields), "")
        link = value("link")
        if not link and "link" in fields:
            link = fields["link"].attrib.get("href", "")
        date = value("pubDate", "published", "updated")
        try:
            stamp = (parsedate_to_datetime(date) if "," in date else
                     datetime.fromisoformat(date.replace("Z", "+00:00")))
            if stamp.utcoffset() is None or stamp > now:
                continue
        except (ValueError, TypeError):
            continue
        title = _plain(value("title"))[:300]
        if not title or not link.startswith("https://"):
            continue
        result.append({
            "source": source, "kind": "news", "identity": value("guid", "id") or link,
            "title": title,
            "summary": _plain(value("description", "summary", "content"))[:1800] or title,
            "url": link[:1000], "published_at": stamp.astimezone(timezone.utc).isoformat(),
            "metrics": {},
        })
        if len(result) >= 40:
            break
    return result


async def read_rss(client: httpx.AsyncClient, *, name: str, url: str, now: datetime):
    response = await client.get(url)
    response.raise_for_status()
    return parse_rss(response.content, source=name, now=now)


async def read_binance(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(BINANCE_TICKERS, params={
        "symbols": json.dumps(SYMBOLS, separators=(",", ":")),
    })
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Binance ticker response is not an array")
    rows = {row["symbol"]: row for row in payload if isinstance(row, dict) and "symbol" in row}
    if not set(SYMBOLS).issubset(rows):
        raise ValueError("Binance ticker response is missing requested pairs")
    result = []
    for symbol in SYMBOLS:
        row = rows[symbol]
        change, price, volume = (float(row["priceChangePercent"]), float(row["lastPrice"]),
                                 float(row["quoteVolume"]))
        if not all(math.isfinite(x) for x in (change, price, volume)) or price <= 0 or volume < 0:
            raise ValueError("Binance ticker contains invalid numbers")
        stamp = datetime.fromtimestamp(int(row["closeTime"]) / 1000, timezone.utc)
        result.append({"symbol": symbol, "change_pct": change, "last_price": price,
                       "quote_volume": volume, "published_at": stamp.isoformat()})
    return result


def significant_moves(tickers: list[dict], previous: dict, *, threshold=3.0) -> list[dict]:
    """Only a crossing of a larger absolute 24h change band creates an event."""
    result = []
    for row in tickers:
        symbol, value = row["symbol"], row["change_pct"]
        old = previous.get(symbol)
        if old is None or row["quote_volume"] < 10_000_000:
            continue
        before = int(abs(old) // threshold)
        after = int(abs(value) // threshold)
        if after <= before or after == 0:
            continue
        asset = symbol.removesuffix("USDT")
        result.append({
            "source": "binance-spot", "kind": "market_move",
            "identity": f"{symbol}:{row['published_at'][:13]}:{int(after * (1 if value >= 0 else -1))}",
            "title": f"{asset} 24小时价格波动超过 {after * threshold:g}% 区间",
            "summary": (f"Binance {symbol} 24小时涨跌 {value:+.2f}%；最新价 "
                        f"{row['last_price']:g} USDT；24小时成交额 {row['quote_volume']:,.0f} USDT。"
                        "这只是价格观察，不代表波动成因已被证实。"),
            "url": f"https://www.binance.com/en/trade/{asset}_USDT",
            "published_at": row["published_at"], "metrics": dict(row),
        })
    return result


async def read_etf(client: httpx.AsyncClient, key: str) -> list[dict]:
    response = await client.get(BTC_ETF_FLOWS, headers={"CG-API-KEY": key})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise ValueError("CoinGlass ETF request returned an error")
    if not isinstance(payload.get("data"), list):
        raise ValueError("CoinGlass ETF response is missing the data array")
    output = []
    for item in sorted(payload["data"], key=lambda row: row["timestamp"])[-10:]:
        stamp = datetime.fromtimestamp(int(item["timestamp"]) / 1000, timezone.utc)
        flow = float(item["flow_usd"])
        if not math.isfinite(flow):
            continue
        output.append({
            "source": "coinglass-btc-etf", "kind": "etf_flow",
            "identity": stamp.date().isoformat(),
            "title": f"BTC ETF {stamp.date()} 资金净{'流入' if flow >= 0 else '流出'}",
            "summary": (f"{stamp.date()} BTC现货ETF净资金流 {flow:+,.0f} 美元。"
                        "这是按交易日发布的数据，不能据此推断同一时刻的价格因果。"),
            "url": "https://www.coinglass.com/bitcoin-etf",
            "published_at": stamp.isoformat(), "metrics": {"flow_usd": flow},
        })
    return output
