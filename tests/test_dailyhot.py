from __future__ import annotations

import asyncio

import httpx
import pytest

from wechat_traffic_agent.dailyhot import DailyHotApiProvider
from wechat_traffic_agent.sources import SourceStatus


def _provider(by_platform: dict[str, object]) -> DailyHotApiProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if path in by_platform:
            return httpx.Response(200, json=by_platform[path])
        return httpx.Response(500, json={"error": "boom"})

    return DailyHotApiProvider(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )


def test_fetch_maps_items_with_rank_and_observed_at() -> None:
    provider = _provider(
        {
            "weibo": {
                "code": 200,
                "data": [
                    {"id": 11, "title": "热搜一", "url": "https://weibo.com/11", "hot": 100},
                    {"id": 12, "title": "热搜二", "hot": "98"},
                ],
            }
        }
    )
    result = asyncio.run(provider.fetch("weibo"))
    assert result.status is SourceStatus.OK
    assert result.source == "dailyhot"
    assert result.platform == "weibo"
    assert result.observed_at is not None
    first, second = result.items
    assert first.source_id == "11"
    assert first.title == "热搜一"
    assert first.url == "https://weibo.com/11"
    assert first.rank == 1
    assert first.raw_hot_value == pytest.approx(100.0)
    assert second.rank == 2
    assert second.raw_hot_value == pytest.approx(98.0)  # 字符串热度可转浮点
    assert second.url is None


def test_platform_failure_returns_failed_result_not_exception() -> None:
    provider = _provider(
        {
            "zhihu": {
                "code": 200,
                "data": [{"id": 1, "title": "知乎热搜", "url": "https://zhihu.com/1", "hot": 10}],
            }
        }
    )

    async def run():
        weibo = await provider.fetch("weibo")  # 500
        zhihu = await provider.fetch("zhihu")
        return weibo, zhihu

    weibo, zhihu = asyncio.run(run())
    assert weibo.status is SourceStatus.FAILED
    assert weibo.items == ()
    assert weibo.error is not None
    # 微博失败 ≠ 整个 scan 失败
    assert zhihu.status is SourceStatus.OK
    assert len(zhihu.items) == 1


def test_malformed_payload_returns_failed() -> None:
    provider = _provider({"weibo": {"code": 200, "data": "not-a-list"}})
    result = asyncio.run(provider.fetch("weibo"))
    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert result.items == ()


def test_missing_fields_stay_none_and_bad_rows_skipped() -> None:
    provider = _provider(
        {
            "weibo": {
                "code": 200,
                "data": [
                    {"title": "只有标题"},
                    "not-a-dict",
                    {"id": 3, "title": "热度第三", "hot": "一万"},
                    {"id": 4, "hot": 5},  # 缺标题 → 跳过
                ],
            }
        }
    )
    result = asyncio.run(provider.fetch("weibo"))
    assert result.status is SourceStatus.OK
    assert len(result.items) == 2  # 非 dict 行被跳过；缺标题行被跳过
    first = result.items[0]
    assert first.title == "只有标题"
    assert first.raw_hot_value is None
    assert result.items[1].raw_hot_value is None  # "一万" 不可转浮点 → None


def test_transport_and_parse_errors_return_failed() -> None:
    def raising_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def run_transport():
        provider = DailyHotApiProvider(
            base_url="http://test", transport=httpx.MockTransport(raising_handler)
        )
        return await provider.fetch("weibo")

    def html_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async def run_parse():
        provider = DailyHotApiProvider(
            base_url="http://test", transport=httpx.MockTransport(html_handler)
        )
        return await provider.fetch("weibo")

    transport_result = asyncio.run(run_transport())
    parse_result = asyncio.run(run_parse())
    assert transport_result.status is SourceStatus.FAILED
    assert "ConnectError" in (transport_result.error or "")
    assert parse_result.status is SourceStatus.FAILED
    assert parse_result.error is not None
