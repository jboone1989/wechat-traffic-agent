"""Contract tests for routine news transferred from Ferro to AIHot."""
import asyncio
from datetime import datetime, timezone
import httpx
import pytest

from wechat_traffic_agent.site_news import collect_site_news, _site_origin


NOW = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)


def story(item_id="news-a", *, published="2026-10-14T07:00:00Z", url="https://original.example/article"):
    return {
        "id": item_id, "title": "Fed policy decision", "summary": "Official announcement describing the decision.",
        "source": {"name": "Federal Reserve"}, "publishedAt": published,
        "discoveredAt": published, "links": {"original": url, "aihot": "https://news.example/items/" + item_id},
    }


def test_selected_site_api_replaces_local_rss_and_preserves_original_source():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={"schemaVersion": 1, "items": [story()], "page": {"hasMore": False, "nextCursor": None}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_site_news("https://news.example", NOW, client=client)
    got = asyncio.run(run())
    assert not got["failures"]
    assert len(got["items"]) == 1
    assert got["items"][0]["news_id"] == "aihot:news-a"
    assert got["items"][0]["url"] == "https://original.example/article"
    assert got["items"][0]["publisher_feed"] == "Federal Reserve"
    assert requests[0].url.path == "/api/v1/items"
    assert requests[0].url.params["mode"] == "selected"
    assert requests[0].url.params["window"] == "7d"


def test_pagination_and_stale_article_filtering():
    def handler(req):
        if req.url.params.get("cursor"):
            return httpx.Response(200, json={"schemaVersion": 1, "items": [story("news-b", published="2026-01-01T00:00:00Z"), story("news-a")], "page": {"hasMore": False}})
        return httpx.Response(200, json={"schemaVersion": 1, "items": [story("news-a")], "page": {"hasMore": True, "nextCursor": "c1"}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_site_news("https://news.example", NOW, client=client)
    got = asyncio.run(run())
    assert [x["news_id"] for x in got["items"]] == ["aihot:news-a"]


def test_site_outage_is_reported_without_double_fetching_rss():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
            return await collect_site_news("https://news.example", NOW, client=client)
    result = asyncio.run(run())
    assert result["items"] == []
    assert result["failures"][0]["source"] == "aihot"


def test_endpoint_rejects_arbitrary_urls_and_credentials():
    for url in ("http://news.example", "https://news.example/api/v1/items", "https://user:pass@news.example", "https://news.example/?q=evil"):
        with pytest.raises(ValueError):
            _site_origin(url)


def test_content_loop_uses_site_with_no_local_rss(monkeypatch):
    from wechat_traffic_agent import content_loop, site_news
    called = []
    async def fake_site(base_url, now):
        called.append(base_url)
        return {"items": [], "failures": [], "observed_at": now.isoformat(), "signal_kind": "site"}
    monkeypatch.setenv("CONTENT_NEWS_AGGREGATOR_URL", "https://news.example")
    monkeypatch.setattr(site_news, "collect_site_news", fake_site)
    result = asyncio.run(content_loop.collect_news([], NOW))
    assert result["signal_kind"] == "site"
    assert called == ["https://news.example"]


def test_site_market_aggregates_binance_okx_and_preserves_provenance(monkeypatch):
    from wechat_traffic_agent.site_news import collect_site_spot_market
    from wechat_traffic_agent.content_explorer import collect_okx_public_market
    import os
    calls = []
    def handler(req):
        asset = req.url.params["asset"]
        calls.append(asset)
        return httpx.Response(200, json={
            "schemaVersion": 1, "asset": asset, "quote": "USDT", "observedAt": NOW.isoformat(),
            "observations": [
                {"exchange": "Binance", "priceUsdt": 100, "change24hPercent": 1.5,
                 "exchangeAt": NOW.isoformat(), "sourceUrl": "https://data-api.binance.vision/api/v3/ticker/24hr"},
                {"exchange": "OKX", "priceUsdt": 101, "change24hPercent": 2,
                 "exchangeAt": NOW.isoformat(), "sourceUrl": "https://www.okx.com/api/v5/market/ticker"}
            ], "errors": []
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_site_spot_market("https://news.example", NOW, client=client)
    results = asyncio.run(run())
    assert calls == ["BTC", "ETH", "SOL", "XRP", "BNB"]
    assert len(results) == 1
    assert "BTC Binance" in results[0]["summary"]
    assert "BNB OKX" in results[0]["summary"]
    assert results[0]["source_kind"] == "public-api"
    async def stub(base_url, now):
        return results
    monkeypatch.setenv("CONTENT_NEWS_AGGREGATOR_URL", "https://news.example")
    monkeypatch.setattr("wechat_traffic_agent.site_news.collect_site_spot_market", stub)
    assert asyncio.run(collect_okx_public_market(NOW)) == results


def test_historical_research_reads_originals_from_multiple_publishers():
    from wechat_traffic_agent.site_news import search_site_archive
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"schemaVersion": 1, "items": [
            story("cxmt-1", published="2026-10-12T12:00:00Z"),
            {**story("cxmt-2", published="2026-09-12T12:00:00Z", url="https://second.example/report"),
             "source": {"name": "Independent Journal"}},
        ], "page": {"hasMore": False}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_site_archive("https://news.example", "memory chip", NOW, client=client)
    leads = asyncio.run(run())
    assert len(leads) == 2
    assert {x["source"] for x in leads} == {"Federal Reserve", "Independent Journal"}
    assert leads[1]["url"] == "https://second.example/report"
    assert leads[1]["evidence_refs"] == ("aihot-public-item:cxmt-2",)
    assert all(x["source_revision"].startswith("sha256:") for x in leads)
    assert calls[0].url.params["window"] == "365d"
    assert calls[0].url.params["mode"] == "all"
    assert calls[0].url.params["q"] == "memory chip"


def test_goal_research_joins_site_archive_and_live_search_without_breaking_when_site_fails(tmp_path, monkeypatch):
    from wechat_traffic_agent.content_explorer import GoalEvidenceExplorer
    import wechat_traffic_agent.site_news as news
    monkeypatch.setenv("CONTENT_NEWS_AGGREGATOR_URL", "https://news.example")
    async def fetch(_feeds, now):
        return {"items": [], "observed_at": now.isoformat()}
    async def archive(_url, _q, _now):
        return [{"evidence_id": "aihot-archive:source-1", "source_kind": "web-search",
                 "source": "Publisher A", "title": "Archived report", "summary": "Archival context.",
                 "url": "https://source.example/1", "observed_at": NOW.isoformat(),
                 "source_revision": "rev1", "evidence_refs": ("aihot-public-item:source-1",)}]
    async def web(_query, _now):
        return [{"title": "Fresh result", "summary": "Fresh independent evidence.",
                 "source": "web", "source_revision": "freshrev", "url": "https://source.example/2",
                 "observed_at": NOW.isoformat(), "evidence_refs": ["web:fresh"]}]
    monkeypatch.setattr(news, "search_site_archive", archive)
    worker = GoalEvidenceExplorer(state_root=tmp_path, feeds=(), rss_fetcher=fetch, web_searcher=web)
    got = asyncio.run(worker.search_web(goal_topic="memory chip", now=NOW))
    assert len(got["items"]) == 2
    assert got["items"][0]["evidence_id"] == "aihot-archive:source-1"
    assert not got["failures"]

    async def failed(_url, _q, _now):
        raise OSError("site unavailable")
    monkeypatch.setattr(news, "search_site_archive", failed)
    again = asyncio.run(worker.search_web(goal_topic="memory chip", now=NOW))
    assert len(again["items"]) == 1
    assert again["failures"][0]["source"] == "aihot:archive"
