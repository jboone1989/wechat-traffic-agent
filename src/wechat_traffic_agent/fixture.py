"""Deterministic in-repo fixture implementing all four source contracts.

Used by CI and unit tests; also lets PR3 build a scan pipeline before any
real service is reachable. One fixture platform can be marked broken to
prove failure isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    HotItem,
    SourceResult,
    SourceStatus,
)

FIXTURE_HOT: dict[str, tuple[HotItem, ...]] = {
    "weibo": (
        HotItem(source_id="wb-1", platform="weibo", title="微博样本一", url="https://fixture.example/weibo/1", rank=1, raw_hot_value=9500.0),
        HotItem(source_id="wb-2", platform="weibo", title="微博样本二", url="https://fixture.example/weibo/2", rank=2, raw_hot_value=7200.0),
        HotItem(source_id="wb-3", platform="weibo", title="微博样本三", rank=3, raw_hot_value=4100.0),
    ),
    "zhihu": (
        HotItem(source_id="zh-1", platform="zhihu", title="知乎样本一", url="https://zhihu.com/fixture/1", rank=1, raw_hot_value=3200.0),
        HotItem(source_id="zh-2", platform="zhihu", title="知乎样本二", url="https://zhihu.com/fixture/2", rank=2),
    ),
    "bilibili": (
        HotItem(source_id="bili-1", platform="bilibili", title="B站样本一", url="https://bilibili.com/fixture/1", rank=1, raw_hot_value=8800.0),
        HotItem(source_id="bili-2", platform="bilibili", title="B站样本二", url="https://bilibili.com/fixture/2", rank=2, raw_hot_value=5100.0),
    ),
}

FIXTURE_CONTENT: dict[str, ContentSnapshot] = {
    "https://fixture.example/weibo/1": ContentSnapshot(
        content_id="wb-1",
        platform="weibo",
        url="https://fixture.example/weibo/1",
        title="微博样本一",
        text="这是微博样本一的正文，包含一个真实观察。",
        author_id="fixture-author-1",
    ),
}

FIXTURE_COMMENTS: dict[str, tuple[Comment, ...]] = {
    "wb-1": (
        Comment(content_id="wb-1", platform="weibo", comment_id="c-1", text="说的就是我", author_id="fixture-author-2", likes=12),
        Comment(content_id="wb-1", platform="weibo", comment_id="c-2", text="收藏了"),
    ),
}

FIXTURE_AUTHORS: dict[str, AuthorSnapshot] = {
    "fixture-author-1": AuthorSnapshot(author_id="fixture-author-1", platform="weibo", followers=18000),
}


class FixtureProvider:
    """Offline fixture with canned data and per-platform failure injection."""

    def __init__(self, *, broken_platforms: frozenset[str] = frozenset()) -> None:
        self._broken = set(broken_platforms)

    def _observed(self) -> datetime:
        return datetime.now(timezone.utc)

    def _unavailable(self, platform: str, reason: str, items) -> SourceResult:
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.UNAVAILABLE,
            items=items,
            error=reason,
            observed_at=self._observed(),
        )

    async def fetch(self, platform: str) -> SourceResult[tuple[HotItem, ...]]:
        if platform in self._broken:
            return SourceResult(
                source="fixture",
                platform=platform,
                status=SourceStatus.FAILED,
                items=(),
                error="fixture: platform marked broken",
                observed_at=self._observed(),
            )
        items = FIXTURE_HOT.get(platform)
        if items is None:
            return self._unavailable(platform, f"fixture has no hot data for {platform!r}", ())
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=items,
            observed_at=self._observed(),
        )

    async def read(self, url: str, *, platform: str = "generic_web") -> SourceResult[ContentSnapshot]:
        if platform in self._broken:
            return SourceResult(
                source="fixture",
                platform=platform,
                status=SourceStatus.FAILED,
                items=ContentSnapshot(content_id="", platform=platform, url="", text=""),
                error="fixture: platform marked broken",
                observed_at=self._observed(),
            )
        snapshot = FIXTURE_CONTENT.get(url)
        if snapshot is None:
            return self._unavailable(
                platform,
                f"fixture has no content for {url!r}",
                ContentSnapshot(content_id="", platform=platform, url="", text=""),
            )
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=snapshot,
            observed_at=self._observed(),
        )

    async def comments(self, content_id: str, *, platform: str) -> SourceResult[tuple[Comment, ...]]:
        if platform in self._broken:
            return SourceResult(
                source="fixture",
                platform=platform,
                status=SourceStatus.FAILED,
                items=(),
                error="fixture: platform marked broken",
                observed_at=self._observed(),
            )
        items = FIXTURE_COMMENTS.get(content_id)
        if items is None:
            return self._unavailable(platform, f"fixture has no comments for {content_id!r}", ())
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=items,
            observed_at=self._observed(),
        )

    async def author(self, author_id: str, *, platform: str) -> SourceResult[AuthorSnapshot]:
        if platform in self._broken:
            return SourceResult(
                source="fixture",
                platform=platform,
                status=SourceStatus.FAILED,
                items=AuthorSnapshot(author_id="", platform=platform),
                error="fixture: platform marked broken",
                observed_at=self._observed(),
            )
        snapshot = FIXTURE_AUTHORS.get(author_id)
        if snapshot is None:
            return self._unavailable(
                platform,
                f"fixture has no author for {author_id!r}",
                AuthorSnapshot(author_id="", platform=platform),
            )
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=snapshot,
            observed_at=self._observed(),
        )
