"""Isolated source tests: can run without Ferro/Agent Runtime dependencies."""
import asyncio
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sys
import types

import httpx

SOURCE_DIR = Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent/sources"
package = types.ModuleType("standalone_market")
package.__path__ = [str(SOURCE_DIR)]
sys.modules.setdefault("standalone_market", package)
providers = importlib.import_module("standalone_market.market_signals")
pipeline = importlib.import_module("standalone_market.market_pipeline")
bridge = importlib.import_module("standalone_market.market_bridge")
NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def rss(*titles):
    return ("<rss><channel>" + "".join(
        f"<item><title>{title}</title><description>Supported evidence</description>"
        f"<link>https://news.example/{i}</link><pubDate>Thu, 08 Oct 2026 11:00:00 +0000</pubDate>"
        f"</item>" for i, title in enumerate(titles)
    ) + "</channel></rss>").encode()


def api(change=1.0, include_new=False, fail=None, etf=False):
    def fetch(request):
        url = str(request.url)
        if fail and fail in url:
            return httpx.Response(502, text="bad gateway")
        if "ticker/24hr" in url:
            return httpx.Response(200, json=[
                {"symbol": symbol, "priceChangePercent": str(change if symbol == "BTCUSDT" else 0.1),
                 "lastPrice": "65000", "quoteVolume": "300000000",
                 "closeTime": int(NOW.timestamp() * 1000)}
                for symbol in providers.SYMBOLS
            ])
        if "bitcoin/flow-history" in url:
            data = [{"timestamp": int((NOW-timedelta(days=1)).timestamp()*1000),
                     "flow_usd": 100000000}]
            if include_new:
                data.append({"timestamp": int(NOW.timestamp()*1000), "flow_usd": -70000000})
            return httpx.Response(200, json={"code": "0", "data": data})
        if "theblockbeats" in url or "panewslab" in url:
            return httpx.Response(200, content=rss("Baseline", *(["Fresh"] if include_new else [])))
        raise AssertionError(url)
    return httpx.MockTransport(fetch)


def run(collector, when=NOW):
    return asyncio.run(collector.run_once(now=when))


def test_first_poll_is_baseline(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    with httpx.Client():
        pass
    async def initial():
        async with httpx.AsyncClient(transport=api()) as client:
            return await pipeline.MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    first = asyncio.run(initial())
    assert first["status"] == "OK"
    assert first["new_research_events"] == 0
    assert pipeline.MarketSignalStore(tmp_path).recent(now=NOW) == []


def test_crossing_then_dedup(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    async def poll(change, newer):
        async with httpx.AsyncClient(transport=api(change, newer)) as client:
            return await pipeline.MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert asyncio.run(poll(1.0, False))["new_research_events"] == 0
    second = asyncio.run(poll(4.0, True))
    assert second["new_research_events"] == 4
    rows = bridge.market_research_candidates(state_root=tmp_path, now=NOW)
    assert len(rows) == 4 and any(row["source_kind"] == "public-api" for row in rows)
    assert asyncio.run(poll(4.0, True))["new_research_events"] == 0


def test_provider_error_does_not_block_others(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    async def poll():
        async with httpx.AsyncClient(transport=api(fail="newsflash")) as client:
            return await pipeline.MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    value = asyncio.run(poll())
    assert value["status"] == "PARTIAL"
    assert any(row["name"] == "binance-spot" and row["status"] == "OK"
               for row in value["providers"])


def test_optional_daily_etf(tmp_path, monkeypatch):
    monkeypatch.setenv("COINGLASS_API_KEY", "test-token")
    async def poll(newer):
        async with httpx.AsyncClient(transport=api(include_new=newer)) as client:
            return await pipeline.MarketSignalCollector(tmp_path, client=client).run_once(now=NOW)
    assert asyncio.run(poll(False))["new_research_events"] == 0
    result = asyncio.run(poll(True))
    assert any(row["name"] == "coinglass-btc-etf" and row["eligible"] == 1
               for row in result["providers"])


def test_source_contract_isolated(tmp_path):
    assert bridge.market_research_candidates(state_root=tmp_path, now=NOW) == []
    parsed = providers.parse_rss(rss("example"), source="test", now=NOW)
    assert parsed[0]["title"] == "example"
    assert providers.significant_moves([], {}) == []
