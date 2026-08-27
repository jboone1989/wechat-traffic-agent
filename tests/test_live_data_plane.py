"""PR7 live data plane: router dispatch, CLI stack wiring, E2E scoring.

The E2E test is the PR7 proof: a real ViralRadar run where the hot feed
is genuine-shaped DailyHot items and the deep fetch goes through
ReaderRouter -> BilibiliCliReader. engagement/breakout/freshness must be
present for the routed platform while the unrouted platform honestly
reports UNAVAILABLE — no fixture filling the gaps.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.agent_reach import AgentReachProvider, SourceStatus  # noqa: F401 (re-export check)
from wechat_traffic_agent.bilibili import BilibiliCliReader
from wechat_traffic_agent.cli_workflows import _build_source_stack
from wechat_traffic_agent.dailyhot import DEFAULT_BASE_URL, DailyHotApiProvider
from wechat_traffic_agent.reader_router import ReaderRouter
from wechat_traffic_agent.sources import HotItem, SourceResult
from wechat_traffic_agent.viral_radar import ViralRadar

from test_bilibili_reader import FakeRunner


# --------------------------------------------------------------------- #
# Router dispatch                                                        #
# --------------------------------------------------------------------- #

class RecordingReader:
    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[tuple[str, tuple, dict]] = []

    async def read(self, url, *, platform="generic_web"):
        self.seen.append(("read", (url,), {"platform": platform}))
        return "ok"

    async def comments(self, content_id, *, platform):
        self.seen.append(("comments", (content_id,), {"platform": platform}))
        return "ok"

    async def author(self, author_id, *, platform):
        self.seen.append(("author", (author_id,), {"platform": platform}))
        return "ok"

    async def engagement(self, content_id, *, platform):
        self.seen.append(("engagement", (content_id,), {"platform": platform}))
        return "ok"


@pytest.mark.asyncio
async def test_router_dispatches_by_platform_with_default_fallback() -> None:
    bili = RecordingReader("bili")
    generic = RecordingReader("generic")
    router = ReaderRouter(default=generic, routes={"bilibili": bili})

    assert await router.read("u", platform="bilibili") == "ok"
    assert await router.comments("c", platform="bilibili") == "ok"
    assert await router.author("a", platform="bilibili") == "ok"
    assert await router.engagement("e", platform="bilibili") == "ok"
    assert await router.read("u2", platform="zhihu") == "ok"

    assert {method for method, _, _ in bili.seen} == {
        "read",
        "comments",
        "author",
        "engagement",
    }
    assert all(kwargs == {"platform": "bilibili"} for _, _, kwargs in bili.seen)
    # 未配置平台落到 default，且不串到任何专路
    assert [entry for entry in generic.seen if entry[0] == "read"]
    assert bili.seen[-1][1] == ("e",)  # 最后一次 bili 调用是 engagement("e")


def test_router_without_routes_uses_default_for_everything() -> None:
    generic = RecordingReader("generic")
    router = ReaderRouter(default=generic)
    asyncio.run(router.read("u", platform="weibo"))
    assert generic.seen[0][1] == ("u",)


# --------------------------------------------------------------------- #
# CLI source-stack wiring                                                #
# --------------------------------------------------------------------- #

def _args(**overrides) -> SimpleNamespace:
    base = {"source": None, "dailyhot_url": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_fixture_mode_keeps_offline_defaults() -> None:
    stack = _build_source_stack(_args())
    assert stack.mode == "fixture"
    assert stack.hot_provider is None and stack.reader is None and stack.closer is None


def test_dailyhot_url_alone_implies_live_mode() -> None:
    stack = _build_source_stack(_args(dailyhot_url="http://127.0.0.1:6688"))
    assert stack.mode == "live"
    assert isinstance(stack.hot_provider, DailyHotApiProvider)
    assert isinstance(stack.reader, ReaderRouter)
    assert isinstance(stack.reader.routes["bilibili"], BilibiliCliReader)
    asyncio.run(stack.closer())  # httpx client 可正常关闭


def test_live_without_url_falls_back_to_documented_default_deployment() -> None:
    stack = _build_source_stack(_args(source="live"))
    assert stack.mode == "live"
    assert stack.hot_provider.base_url == DEFAULT_BASE_URL
    asyncio.run(stack.closer())


def test_dailyhot_url_conflicts_with_explicit_fixture() -> None:
    with pytest.raises(SystemExit, match="冲突"):
        _build_source_stack(
            _args(source="fixture", dailyhot_url="http://127.0.0.1:6688")
        )


def test_live_reader_never_touches_fixture_provider() -> None:
    from wechat_traffic_agent.fixture import FixtureProvider

    stack = _build_source_stack(_args(source="live"))
    router = stack.reader
    # 路由树里不允许出现 fixture：缺能力必须是 unavailable，不是测试数据
    assert not isinstance(router.default, FixtureProvider)
    assert not any(isinstance(r, FixtureProvider) for r in router.routes.values())
    asyncio.run(stack.closer())


# --------------------------------------------------------------------- #
# E2E: radar over the routed live stack (offline via FakeRunner)         #
# --------------------------------------------------------------------- #

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


class StubHotFeed:
    """DailyHot-shaped feed: BV-url 视频条目 + 无 URL 的微博词条。"""

    def __init__(self) -> None:
        self._items = {
            "bilibili": (
                HotItem(
                    source_id="6688",
                    platform="bilibili",
                    title="十分钟看懂三线小城的早餐江湖",
                    url="https://www.bilibili.com/video/BV1xx411c7mD",
                    rank=3,
                    raw_hot_value=950.0,
                    observed_at=NOW,
                ),
                HotItem(
                    source_id="6689",
                    platform="bilibili",
                    title="第二条视频标题",
                    url="https://www.bilibili.com/video/BV1yy411c7mD",
                    rank=7,
                    raw_hot_value=210.0,
                    observed_at=NOW,
                ),
            ),
            "weibo": (
                HotItem(
                    source_id="wb1",
                    platform="weibo",
                    title="某热搜词条",
                    url=None,
                    rank=1,
                    observed_at=NOW,
                ),
            ),
        }

    async def fetch(self, platform: str) -> SourceResult:
        return SourceResult(
            source="dailyhot",
            platform=platform,
            status=SourceStatus.OK,
            items=self._items[platform],
            observed_at=NOW,
        )


def _live_radar() -> tuple[ViralRadar, ReaderRouter]:
    runner = FakeRunner()
    reach = AgentReachProvider(runner=runner)
    router = ReaderRouter(
        default=reach, routes={"bilibili": BilibiliCliReader(inner=reach)}
    )
    radar = ViralRadar(
        hot_provider=StubHotFeed(),
        reader=router,
        platforms=("bilibili", "weibo"),
        top_n=5,
        now=lambda: NOW,
    )
    return radar, router


def test_radar_over_routed_live_stack_produces_real_subscores() -> None:
    radar, _router = _live_radar()
    report = asyncio.run(radar.scan())

    by_platform = {scan.platform: scan for scan in report.platforms}
    assert by_platform["bilibili"].source == "dailyhot"

    # FakeRunner 对任意视频 id 返回同一份载荷，content_id 会同值；
    # 按平台+原始榜位定位，而不是按 content_id 建字典。
    first = next(
        c for c in report.candidates if c.platform == "bilibili" and c.rank == 3
    )
    # 深抓补齐了作者与发布时间 → 作者数据随之发生
    assert first.author_followers == 321
    assert first.published_at is not None
    # 三项曾经永远缺失的分数如今真实存在
    assert first.engagement_score is not None
    assert first.breakout_score is not None
    assert first.freshness_score is not None
    assert first.final_score is not None
    assert not first.errors
    # 证据链包含 read/author/engagement 全部三段
    kinds = {ref.split(":")[0] for ref in first.evidence_refs}
    assert {"read", "author", "engagement"} <= kinds


def test_unrouted_platform_reports_unavailable_not_fixture() -> None:
    radar, _router = _live_radar()
    report = asyncio.run(radar.scan())

    weibo = next(c for c in report.candidates if c.platform == "weibo")
    # 无 URL 不读正文；engagement 走默认通道，明确报不可用——绝不拿 fixture 填
    assert any(err.startswith("engagement:") for err in weibo.errors)
    assert not any(ref.startswith("read:") for ref in weibo.evidence_refs)
    assert weibo.views is None and weibo.likes is None
    assert weibo.engagement_score is None and weibo.breakout_score is None
    assert weibo.freshness_score is None
