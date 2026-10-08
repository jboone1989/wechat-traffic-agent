"""Offline regression tests for independent market-signal ingestion."""
from __future__ import annotations
import asyncio
from functools import wraps
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest

from wechat_traffic_agent.sources.market_bridge import market_research_candidates
from wechat_traffic_agent.sources.market_pipeline import MarketSignalCollector, MarketSignalStore
from wechat_traffic_agent.sources.market_signals import (
    BINANCE_TICKERS, BLOCKBEATS, BTC_ETF_FLOWS, parse_rss, significant_moves,
)

def run_coroutine(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapper


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
NEWS = "https://www.theblockbeats.info/flash/123"


def feed(items):
    rows = "".join(
        f"<item><title>{title}</title><description>Useful market evidence for readers</description>"
        f"<link>{url}</link><guid>{url}</guid><pubDate>{dt}</pubDate></item>"
        for title, url, dt in items
    )
    return ("<?xml version='1.0'?><rss><channel>" + rows + "</channel></rss>").encode()


def ticker_rows(change=1.5):
    timestamp = int(NOW.timestamp() * 1000)
    return [
        {"symbol": symbol, "priceChangePercent": str(change if symbol == "BTCUSDT" else 1.0),
         "lastPrice": "80000", "quoteVolume": "500000000", "closeTime": timestamp}
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
    ]


def transport(*, btc_change=1.5, newer=False, failure=None, etf=False):
    def respond(request):
        url = str(request.url)
        if failure and failure in url:
            return httpx.Response(503, text="temporary outage")
        if "/api/v3/ticker/24hr" in url:
            return httpx.Response(200, json=ticker_rows(btc_change))
        if "/api/etf/bitcoin/flow-history" in url:
            return httpx.Response(200, json={"code": "0", "data": [
                {"timestamp": int((NOW - timedelta(days=1)).timestamp()*1000),
                 "flow_usd": 100_000_000},
                *([{"timestamp": int(NOW.timestamp()*1000), "flow_usd": -200_000_000}] if newer else []),
            ]})
        if "api.theblockbeats.news" in url:
            items = [("Baseline report", NEWS, "Wed, 07 Oct 2026 11:00:00 +0000")]
            if newer:
                items.append(("New market research", NEWS + "4", "Thu, 08 Oct 2026 11:20:00 +0000"))
            return httpx.Response(200, content=feed(items))
        raise AssertionError("unexpected API request: " + url)
    return httpx.MockTransport(respond)


@run_coroutine
async def test_bootstrap_does_not_publish_existing_news(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    async with httpx.AsyncClient(transport=transport()) as client:
        report = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert report["status"] == "OK"
    assert report["new_research_events"] == 0
    assert MarketSignalStore(tmp_path).recent(now=NOW) == []


@run_coroutine
async def test_news_and_price_events_require_change_and_are_deduplicated(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    async with httpx.AsyncClient(transport=transport()) as client:
        await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    async with httpx.AsyncClient(transport=transport(btc_change=4.0, newer=True)) as client:
        report = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert report["new_research_events"] == 3
    evidence = market_research_candidates(state_root=tmp_path, now=NOW)
    assert len(evidence) == 3
    assert any(row["source_kind"] == "public-api" for row in evidence)
    assert any(row["source_kind"] == "rss" for row in evidence)
    from wechat_traffic_agent.content_explorer import EvidenceCandidate
    for row in evidence:
        EvidenceCandidate.model_validate(row)
    async with httpx.AsyncClient(transport=transport(btc_change=4.0, newer=True)) as client:
        again = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert again["new_research_events"] == 0
    assert len(MarketSignalStore(tmp_path).recent(now=NOW)) == 3


@run_coroutine
async def test_provider_outage_does_not_erase_others_or_arm_baseline(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    async with httpx.AsyncClient(transport=transport(failure="newsflash")) as client:
        failed = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert failed["status"] == "PARTIAL"
    assert any(row["name"] == "binance-spot" and row["status"] == "OK" for row in failed["providers"])
    assert any(row["name"] == "blockbeats-newsflash" and row["status"] == "FAILED" for row in failed["providers"])
    async with httpx.AsyncClient(transport=transport(newer=True)) as client:
        recovered = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    flash = next(row for row in recovered["providers"] if row["name"] == "blockbeats-newsflash")
    assert flash["baseline"] is True  # previous failure is not successful first observation


@run_coroutine
async def test_optional_coin_glass_daily_etf_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("COINGLASS_API_KEY", "offline-test")
    async with httpx.AsyncClient(transport=transport(etf=True)) as client:
        first = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert first["new_research_events"] == 0
    async with httpx.AsyncClient(transport=transport(newer=True, etf=True)) as client:
        second = await MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert any(row["name"] == "coinglass-btc-etf" and row["eligible"] == 1
               for row in second["providers"])
    assert any("不能据此推断" in row["summary"] for row in
               MarketSignalStore(tmp_path).recent(now=NOW))


def test_bad_rss_fails_without_executing_xml_entities():
    with pytest.raises(ValueError, match="XML entities"):
        parse_rss(b"<!DOCTYPE rss [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><rss/>",
                  source="test", now=NOW)


def test_trigger_does_not_fire_for_same_band():
    previous = {"BTCUSDT": 3.4}
    value = {"symbol": "BTCUSDT", "change_pct": 4.2, "last_price": 80000.0,
             "quote_volume": 100_000_000, "published_at": NOW.isoformat()}
    assert significant_moves([value], previous) == []
    assert len(significant_moves([{**value, "change_pct": 6.4}], previous)) == 1


def test_absent_database_is_an_empty_research_feed(tmp_path):
    assert market_research_candidates(state_root=tmp_path, now=NOW) == []
    assert not (Path(tmp_path) / "content/market_signals.sqlite3").exists()


def test_data_capture_never_imports_publisher_or_ferro_cognition():
    root = Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent/sources"
    for filename in ("market_signals.py", "market_pipeline.py", "market_bridge.py"):
        source = (root / filename).read_text()
        assert "publish_square" not in source
        assert "XBrowserPublisher" not in source
        assert "WakefulContentTaskRunner" not in source
