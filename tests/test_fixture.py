from __future__ import annotations

import asyncio

from wechat_traffic_agent.fixture import FixtureProvider
from wechat_traffic_agent.sources import SourceStatus


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
