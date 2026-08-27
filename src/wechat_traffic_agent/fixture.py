"""Deterministic in-repo fixture implementing all source contracts.

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
    EngagementSnapshot,
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
        published_at=datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc),
    ),
}

FIXTURE_COMMENTS: dict[str, tuple[Comment, ...]] = {
    "wb-1": (
        Comment(content_id="wb-1", platform="weibo", comment_id="c-1", text="说的就是我", author_id="fixture-author-2", likes=12),
        Comment(content_id="wb-1", platform="weibo", comment_id="c-2", text="收藏了"),
    ),
}

FIXTURE_CONTENT.update({
    "https://fixture.example/weibo/2": ContentSnapshot(
        content_id="wb-2", platform="weibo", url="https://fixture.example/weibo/2",
        title="微博样本二", text="这是微博样本二的正文。", author_id="fixture-weibo-author-2",
        published_at=datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/4": ContentSnapshot(
        content_id="weibo-4", platform="weibo", url="https://fixture.example/weibo/4",
        title="跨平台同题：通勤效率观察", text="这是大号普通内容的正文，数据平稳。",
        author_id="fixture-weibo-author-4",
        published_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/5": ContentSnapshot(
        content_id="weibo-5", platform="weibo", url="https://fixture.example/weibo/5",
        title="weibo热点样本005", text="这是低粉爆款的正文，互动远超账号体量。",
        author_id="fixture-weibo-author-5",
        published_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/6": ContentSnapshot(
        content_id="weibo-6", platform="weibo", url="https://fixture.example/weibo/6",
        title="weibo热点样本006", text="这是无互动数据样本的正文。",
        author_id="fixture-weibo-author-6",
        published_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/7": ContentSnapshot(
        content_id="weibo-7", platform="weibo", url="https://fixture.example/weibo/7",
        title="weibo热点样本007", text="这是作者粉丝数未知样本的正文。",
        author_id="fixture-weibo-author-7",
        published_at=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
    ),
    "https://zhihu.com/fixture/1": ContentSnapshot(
        content_id="zh-1", platform="zhihu", url="https://zhihu.com/fixture/1",
        title="知乎样本一", text="这是知乎样本一的正文。", author_id="fixture-zhihu-author-1",
        published_at=datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc),
    ),
    "https://zhihu.com/fixture/2": ContentSnapshot(
        content_id="zh-2", platform="zhihu", url="https://zhihu.com/fixture/2",
        title="知乎样本二", text="这是知乎样本二的正文。", author_id="fixture-zhihu-author-2",
        published_at=datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/3": ContentSnapshot(
        content_id="zhihu-3", platform="zhihu", url="https://fixture.example/zhihu/3",
        title="zhihu热点样本003", text="这是知乎低粉爆款的正文。", author_id="fixture-zhihu-author-3",
        published_at=datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/4": ContentSnapshot(
        content_id="zhihu-4", platform="zhihu", url="https://fixture.example/zhihu/4",
        title="跨平台同题：通勤效率观察", text="这是知乎跨平台同题的正文。",
        author_id="fixture-zhihu-author-4",
        published_at=datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/5": ContentSnapshot(
        content_id="zhihu-5", platform="zhihu", url="https://fixture.example/zhihu/5",
        title="zhihu热点样本005", text="这是知乎样本五的正文。", author_id="fixture-zhihu-author-5",
        published_at=datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/6": ContentSnapshot(
        content_id="zhihu-6", platform="zhihu", url="https://fixture.example/zhihu/6",
        title="zhihu热点样本006", text="这是知乎样本六的正文。", author_id="fixture-zhihu-author-6",
        published_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/1": ContentSnapshot(
        content_id="bili-1", platform="bilibili", url="https://bilibili.com/fixture/1",
        title="B站样本一", text="这是B站样本一的正文。", author_id="fixture-bili-author-1",
        published_at=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/2": ContentSnapshot(
        content_id="bili-2", platform="bilibili", url="https://bilibili.com/fixture/2",
        title="B站样本二", text="这是B站样本二的正文。", author_id="fixture-bili-author-2",
        published_at=datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/3": ContentSnapshot(
        content_id="bili-3", platform="bilibili", url="https://bilibili.com/fixture/3",
        title="bilibili热点样本003", text="这是B站低粉爆款的正文。", author_id="fixture-bili-author-3",
        published_at=datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/4": ContentSnapshot(
        content_id="bili-4", platform="bilibili", url="https://bilibili.com/fixture/4",
        title="bilibili热点样本004", text="这是B站样本四的正文。", author_id="fixture-bili-author-4",
        published_at=datetime(2026, 8, 24, 22, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/5": ContentSnapshot(
        content_id="bili-5", platform="bilibili", url="https://bilibili.com/fixture/5",
        title="bilibili热点样本005", text="这是B站样本五的正文。", author_id="fixture-bili-author-5",
        published_at=datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/6": ContentSnapshot(
        content_id="bili-6", platform="bilibili", url="https://bilibili.com/fixture/6",
        title="bilibili热点样本006", text="这是B站样本六的正文。", author_id="fixture-bili-author-6",
        published_at=datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/7": ContentSnapshot(
        content_id="bili-7", platform="bilibili", url="https://bilibili.com/fixture/7",
        title="bilibili热点样本007", text="这是B站样本七的正文。", author_id="fixture-bili-author-7",
        published_at=datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc),
    ),
})

FIXTURE_AUTHORS: dict[str, AuthorSnapshot] = {
    "fixture-author-1": AuthorSnapshot(author_id="fixture-author-1", platform="weibo", followers=18000),
}

FIXTURE_AUTHORS.update({
    "fixture-weibo-author-2": AuthorSnapshot(author_id="fixture-weibo-author-2", platform="weibo", followers=120000),
    "fixture-weibo-author-4": AuthorSnapshot(author_id="fixture-weibo-author-4", platform="weibo", followers=2_500_000),
    "fixture-weibo-author-5": AuthorSnapshot(author_id="fixture-weibo-author-5", platform="weibo", followers=600),
    "fixture-weibo-author-6": AuthorSnapshot(author_id="fixture-weibo-author-6", platform="weibo", followers=9000),
    "fixture-weibo-author-7": AuthorSnapshot(author_id="fixture-weibo-author-7", platform="weibo", followers=None),
    "fixture-zhihu-author-1": AuthorSnapshot(author_id="fixture-zhihu-author-1", platform="zhihu", followers=5000),
    "fixture-zhihu-author-2": AuthorSnapshot(author_id="fixture-zhihu-author-2", platform="zhihu", followers=900),
    "fixture-zhihu-author-3": AuthorSnapshot(author_id="fixture-zhihu-author-3", platform="zhihu", followers=800),
    "fixture-zhihu-author-4": AuthorSnapshot(author_id="fixture-zhihu-author-4", platform="zhihu", followers=30000),
    "fixture-zhihu-author-5": AuthorSnapshot(author_id="fixture-zhihu-author-5", platform="zhihu", followers=2000),
    "fixture-zhihu-author-6": AuthorSnapshot(author_id="fixture-zhihu-author-6", platform="zhihu", followers=4500),
    "fixture-bili-author-1": AuthorSnapshot(author_id="fixture-bili-author-1", platform="bilibili", followers=220000),
    "fixture-bili-author-2": AuthorSnapshot(author_id="fixture-bili-author-2", platform="bilibili", followers=15000),
    "fixture-bili-author-3": AuthorSnapshot(author_id="fixture-bili-author-3", platform="bilibili", followers=950),
    "fixture-bili-author-4": AuthorSnapshot(author_id="fixture-bili-author-4", platform="bilibili", followers=800000),
    "fixture-bili-author-5": AuthorSnapshot(author_id="fixture-bili-author-5", platform="bilibili", followers=5200),
    "fixture-bili-author-6": AuthorSnapshot(author_id="fixture-bili-author-6", platform="bilibili", followers=2600),
    "fixture-bili-author-7": AuthorSnapshot(author_id="fixture-bili-author-7", platform="bilibili", followers=60000),
})

# PR3: deep fetch 的离线互动数据（producer 落在 deep fetch 上）。
FIXTURE_ENGAGEMENT: dict[str, EngagementSnapshot] = {
    "wb-1": EngagementSnapshot(content_id="wb-1", platform="weibo", views=250000, likes=8000, comments=900, shares=400),
    "wb-2": EngagementSnapshot(content_id="wb-2", platform="weibo", likes=12000, comments=1500, shares=600),
    "wb-3": EngagementSnapshot(content_id="wb-3", platform="weibo", likes=6000, comments=700, shares=250),
    "weibo-4": EngagementSnapshot(content_id="weibo-4", platform="weibo", likes=30000, comments=2000, shares=800),
    "weibo-5": EngagementSnapshot(content_id="weibo-5", platform="weibo", likes=52000, comments=7800, shares=3100, saves=4100),
    "weibo-7": EngagementSnapshot(content_id="weibo-7", platform="weibo", likes=1500, comments=100, shares=50),
    "zh-1": EngagementSnapshot(content_id="zh-1", platform="zhihu", likes=300, comments=40, shares=10),
    "zh-2": EngagementSnapshot(content_id="zh-2", platform="zhihu", likes=90, comments=12, shares=3),
    "zhihu-3": EngagementSnapshot(content_id="zhihu-3", platform="zhihu", likes=420, comments=60, shares=20),
    "zhihu-4": EngagementSnapshot(content_id="zhihu-4", platform="zhihu", likes=200, comments=25, shares=8),
    "zhihu-5": EngagementSnapshot(content_id="zhihu-5", platform="zhihu", likes=60, comments=8, shares=2),
    "zhihu-6": EngagementSnapshot(content_id="zhihu-6", platform="zhihu", likes=260, comments=33, shares=9),
    "bili-1": EngagementSnapshot(content_id="bili-1", platform="bilibili", likes=9000, comments=1200, shares=500),
    "bili-2": EngagementSnapshot(content_id="bili-2", platform="bilibili", likes=3000, comments=400, shares=150),
    "bili-3": EngagementSnapshot(content_id="bili-3", platform="bilibili", likes=7000, comments=900, shares=260),
    "bili-4": EngagementSnapshot(content_id="bili-4", platform="bilibili", likes=4000, comments=300, shares=90),
    "bili-5": EngagementSnapshot(content_id="bili-5", platform="bilibili", likes=2200, comments=260, shares=70),
    "bili-6": EngagementSnapshot(content_id="bili-6", platform="bilibili", likes=1500, comments=180, shares=40),
    "bili-7": EngagementSnapshot(content_id="bili-7", platform="bilibili", likes=800, comments=95, shares=20),
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

    async def engagement(
        self, content_id: str, *, platform: str
    ) -> SourceResult[EngagementSnapshot]:
        if platform in self._broken:
            return SourceResult(
                source="fixture",
                platform=platform,
                status=SourceStatus.FAILED,
                items=EngagementSnapshot(content_id="", platform=platform),
                error="fixture: platform marked broken",
                observed_at=self._observed(),
            )
        snapshot = FIXTURE_ENGAGEMENT.get(content_id)
        if snapshot is None:
            return self._unavailable(
                platform,
                f"fixture has no engagement for {content_id!r}",
                EngagementSnapshot(content_id="", platform=platform),
            )
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=snapshot,
            observed_at=self._observed(),
        )


# ---------------------------------------------------------------------------
# PR3: bulk hot data（确定性生成，离线 ≥100 条）+ 深抓场景。
# 低粉爆款：weibo-5 / zhihu-3 / bili-3；纯大号普通内容：weibo-4。
_BULK_COUNT = 40

_CROSS_PLATFORM_TITLE = "跨平台同题：通勤效率观察"
_TITLE_OVERRIDES: dict[tuple[str, int], str] = {
    ("weibo", 4): _CROSS_PLATFORM_TITLE,
    ("zhihu", 4): _CROSS_PLATFORM_TITLE,
}


def _bulk_hot(platform: str, start_rank: int, count: int) -> tuple[HotItem, ...]:
    items: list[HotItem] = []
    denominator = start_rank + count + 20
    for offset in range(count):
        rank = start_rank + offset
        raw_hot = None if rank % 7 == 0 else round(10000.0 * (1.0 - rank / denominator), 1)
        title = _TITLE_OVERRIDES.get((platform, rank), f"{platform}热点样本{rank:03d}")
        items.append(
            HotItem(
                source_id=f"{platform}-{rank}",
                platform=platform,
                title=title,
                url=f"https://fixture.example/{platform}/{rank}",
                rank=rank,
                raw_hot_value=raw_hot,
            )
        )
    return tuple(items)


FIXTURE_HOT["weibo"] = (
    FIXTURE_HOT["weibo"]
    + _bulk_hot("weibo", 4, _BULK_COUNT)
    + (
        # 同平台同题（rank 11 > weibo-10 的 rank 10 → dedup 时被丢弃）
        HotItem(
            source_id="weibo-dup", platform="weibo", title="weibo热点样本010",
            url="https://fixture.example/weibo/dup", rank=11, raw_hot_value=500.0,
        ),
    )
)
FIXTURE_HOT["zhihu"] = FIXTURE_HOT["zhihu"] + _bulk_hot("zhihu", 3, _BULK_COUNT)
FIXTURE_HOT["bilibili"] = FIXTURE_HOT["bilibili"] + _bulk_hot("bilibili", 3, _BULK_COUNT)
