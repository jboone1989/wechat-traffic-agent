from __future__ import annotations

import asyncio

from wechat_traffic_agent.fixture import FixtureProvider
from wechat_traffic_agent.sources import EngagementSnapshot, SourceStatus


def test_fixture_supports_at_least_three_platforms() -> None:
    provider = FixtureProvider()

    async def fetch_all():
        return {platform: await provider.fetch(platform) for platform in ("weibo", "zhihu", "bilibili")}

    results = asyncio.run(fetch_all())
    assert set(results) == {"weibo", "zhihu", "bilibili"}
    for platform, result in results.items():
        assert result.status is SourceStatus.OK
        assert result.source == "fixture"
        assert result.platform == platform
        assert result.observed_at is not None
        assert len(result.items) >= 2
        assert all(item.platform == platform for item in result.items)


def test_fixture_broken_platform_fails_in_isolation() -> None:
    provider = FixtureProvider(broken_platforms=frozenset({"weibo"}))

    async def fetch():
        weibo = await provider.fetch("weibo")
        zhihu = await provider.fetch("zhihu")
        return weibo, zhihu

    weibo, zhihu = asyncio.run(fetch())
    assert weibo.status is SourceStatus.FAILED
    assert weibo.items == ()
    assert weibo.error is not None
    # 单平台失败绝不中断其他平台
    assert zhihu.status is SourceStatus.OK


def test_fixture_unknown_platform_is_unavailable() -> None:
    provider = FixtureProvider()
    result = asyncio.run(provider.fetch("douyin"))
    assert result.status is SourceStatus.UNAVAILABLE
    assert result.items == ()
    assert result.error is not None


def test_fixture_read_comments_author() -> None:
    provider = FixtureProvider()

    async def run():
        content = await provider.read("https://fixture.example/weibo/1")
        comments = await provider.comments("wb-1", platform="weibo")
        author = await provider.author("fixture-author-1", platform="weibo")
        return content, comments, author

    content, comments, author = asyncio.run(run())
    assert content.status is SourceStatus.OK
    assert content.items.text
    assert comments.status is SourceStatus.OK
    assert len(comments.items) >= 1
    assert author.status is SourceStatus.OK
    assert author.items.followers is not None
    # 数据外的 id → unavailable
    missing = asyncio.run(provider.comments("not-in-fixture", platform="weibo"))
    assert missing.status is SourceStatus.UNAVAILABLE
    assert asyncio.run(provider.read("https://not-in-fixture.example/x")).status is SourceStatus.UNAVAILABLE
    assert asyncio.run(provider.author("not-in-fixture", platform="weibo")).status is SourceStatus.UNAVAILABLE


def test_fixture_deep_fetch_walk_connects_hot_to_content() -> None:
    provider = FixtureProvider()

    async def walk():
        feed = await provider.fetch("weibo")
        item = feed.items[0]
        content = await provider.read(item.url or "", platform="weibo")
        comments = await provider.comments(content.items.content_id, platform="weibo")
        author = await provider.author(content.items.author_id or "", platform="weibo")
        return feed, content, comments, author

    feed, content, comments, author = asyncio.run(walk())
    assert feed.status is SourceStatus.OK
    assert content.status is SourceStatus.OK
    assert content.items.text
    assert comments.status is SourceStatus.OK
    assert len(comments.items) >= 1
    assert author.status is SourceStatus.OK
    assert author.items.followers is not None


def test_fixture_broken_platform_fails_leaf_methods_too() -> None:
    provider = FixtureProvider(broken_platforms=frozenset({"weibo"}))

    async def run():
        read = await provider.read("https://fixture.example/weibo/1", platform="weibo")
        comments = await provider.comments("wb-1", platform="weibo")
        author = await provider.author("fixture-author-1", platform="weibo")
        return read, comments, author

    read, comments, author = asyncio.run(run())
    for result in (read, comments, author):
        assert result.status is SourceStatus.FAILED
        assert result.error is not None


def test_fixture_engagement_reads_canned_data() -> None:
    provider = FixtureProvider()
    result = asyncio.run(provider.engagement("wb-1", platform="weibo"))
    assert result.status is SourceStatus.OK
    assert result.source == "fixture"
    assert result.platform == "weibo"
    assert result.observed_at is not None
    assert result.items.likes == 8000


def test_fixture_engagement_missing_is_unavailable_and_broken_is_failed() -> None:
    provider = FixtureProvider()
    missing = asyncio.run(provider.engagement("not-in-fixture", platform="weibo"))
    assert missing.status is SourceStatus.UNAVAILABLE
    assert missing.error is not None
    assert missing.items == EngagementSnapshot(content_id="", platform="weibo")  # 退化实例纪律
    broken_provider = FixtureProvider(broken_platforms=frozenset({"weibo"}))
    failed = asyncio.run(broken_provider.engagement("wb-1", platform="weibo"))
    assert failed.status is SourceStatus.FAILED
    assert failed.error is not None
    assert failed.items == EngagementSnapshot(content_id="", platform="weibo")


def test_fixture_bulk_hot_reaches_100_items() -> None:
    from wechat_traffic_agent.fixture import FIXTURE_AUTHORS, FIXTURE_ENGAGEMENT, FIXTURE_HOT
    from wechat_traffic_agent.scoring import normalize_title

    total = sum(len(items) for items in FIXTURE_HOT.values())
    assert total >= 100
    # rank % 7 == 0 的 bulk 条目刻意缺失 raw_hot_value（绝对热度缺失路径）
    assert any(item.raw_hot_value is None for item in FIXTURE_HOT["weibo"])


def test_fixture_crafted_deep_fetch_scenarios_exist() -> None:
    from wechat_traffic_agent.fixture import FIXTURE_AUTHORS, FIXTURE_ENGAGEMENT, FIXTURE_HOT
    from wechat_traffic_agent.scoring import normalize_title

    # 低粉爆款 vs 纯大号普通内容
    assert FIXTURE_ENGAGEMENT["weibo-5"].likes == 52000
    assert FIXTURE_AUTHORS["fixture-weibo-author-5"].followers == 600
    assert FIXTURE_AUTHORS["fixture-weibo-author-4"].followers == 2_500_000
    # weibo-6 无互动数据（重归一化）；weibo-7 作者无粉丝数（breakout 缺失）
    assert "weibo-6" not in FIXTURE_ENGAGEMENT
    assert FIXTURE_AUTHORS["fixture-weibo-author-7"].followers is None
    # wb-3 无 url：engagement 需要走 source_id 兜底
    wb3 = next(item for item in FIXTURE_HOT["weibo"] if item.source_id == "wb-3")
    assert wb3.url is None
    assert "wb-3" in FIXTURE_ENGAGEMENT
    # 同平台重复标题 + 跨平台同题
    weibo_titles = [normalize_title(item.title) for item in FIXTURE_HOT["weibo"]]
    assert len(weibo_titles) > len(set(weibo_titles))
    assert normalize_title("跨平台同题：通勤效率观察") in {
        normalize_title(item.title) for item in FIXTURE_HOT["zhihu"]
    }
