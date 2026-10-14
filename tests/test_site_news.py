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
