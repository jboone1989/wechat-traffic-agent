from __future__ import annotations

import asyncio

import httpx
import pytest

from wechat_traffic_agent.dailyhot import DailyHotApiProvider
from wechat_traffic_agent.fixture import FixtureProvider
from wechat_traffic_agent.sources import SourceStatus
from wechat_traffic_agent.viral_radar import ViralRadar

PLATFORMS = ("weibo", "zhihu", "bilibili")


def _scan(*, top_n: int = 20, broken: frozenset[str] = frozenset()):
    provider = FixtureProvider(broken_platforms=broken)
    radar = ViralRadar(hot_provider=provider, reader=provider, platforms=PLATFORMS, top_n=top_n)
    return asyncio.run(radar.scan())


def _by_id(report):
    return {candidate.candidate_id: candidate for candidate in report.candidates}


def test_scan_over_100_fixture_items_outputs_top_n_stably() -> None:
    report = _scan()
    assert sum(p.items_fetched for p in report.platforms) >= 100
    assert len(report.candidates) == 20
    again = _scan()
    assert [c.candidate_id for c in again.candidates] == [c.candidate_id for c in report.candidates]
    assert [c.final_score for c in again.candidates] == [c.final_score for c in report.candidates]


def test_low_follower_breakout_beats_big_account_normal_content() -> None:
    report = _scan()
    candidates = _by_id(report)
    breakout = candidates["weibo:weibo-5"]  # 600 粉丝、6.7 万互动
    big_account = candidates["weibo:weibo-4"]  # 250 万粉丝、3.28 万互动的普通内容
    assert breakout.author_followers == 600
    assert big_account.author_followers == 2_500_000
    assert breakout.breakout_score == pytest.approx(1.0)
    assert big_account.breakout_score == pytest.approx(0.0)
    assert breakout.final_score is not None and big_account.final_score is not None
    assert breakout.final_score > big_account.final_score
    assert breakout.final_score > candidates["weibo:wb-1"].final_score
    assert report.candidates[0].candidate_id == "weibo:weibo-5"
    # 全局前三都是各自平台的中低粉爆款
    assert {c.candidate_id for c in report.candidates[:3]} == {
        "weibo:weibo-5", "zhihu:zhihu-3", "bilibili:bili-3",
    }


def test_raw_engagement_is_never_compared_cross_platform() -> None:
    report = _scan()
    candidates = _by_id(report)
    small = candidates["zhihu:zhihu-3"]  # 500 互动即知乎第一
    big = candidates["weibo:weibo-5"]  # 6.7 万互动
    assert (small.likes or 0) < (big.likes or 0)
    assert small.engagement_score == pytest.approx(1.0)
    assert big.engagement_score == pytest.approx(1.0)


def test_missing_engagement_renormalizes_instead_of_zero() -> None:
    report = _scan()
    candidates = _by_id(report)
    missing = candidates["weibo:weibo-6"]  # 无互动数据
    lowest = candidates["weibo:weibo-7"]  # 平台内互动最低
    assert missing.engagement_score is None
    assert lowest.engagement_score == pytest.approx(0.0)
    assert missing.final_score is not None and lowest.final_score is not None
    assert missing.final_score > lowest.final_score


def test_platform_failure_does_not_abort_scan() -> None:
    report = _scan(broken=frozenset({"weibo"}))
    statuses = {p.platform: p.status for p in report.platforms}
    assert statuses["weibo"] is SourceStatus.FAILED
    assert statuses["zhihu"] is SourceStatus.OK
    assert report.candidates
    assert {c.platform for c in report.candidates} == {"zhihu", "bilibili"}


def test_all_platforms_broken_returns_empty_report() -> None:
    report = _scan(broken=frozenset(PLATFORMS))
    assert report.candidates == ()
    assert all(p.status is SourceStatus.FAILED for p in report.platforms)


def test_top_n_is_respected() -> None:
    report = _scan(top_n=5)
    assert len(report.candidates) == 5


def test_dedup_and_cross_platform_scores() -> None:
    report = _scan()
    candidates = _by_id(report)
    assert "weibo:weibo-dup" not in candidates  # 同平台同题被去重
    weibo_platform = next(p for p in report.platforms if p.platform == "weibo")
    assert weibo_platform.items_fetched == 44
    assert weibo_platform.items_after_dedup == 43
    # 跨平台同题：weibo-4 与 zhihu-4 同题 → 2/3
    assert candidates["weibo:weibo-4"].cross_platform_score == pytest.approx(2 / 3)
    assert candidates["zhihu:zhihu-4"].cross_platform_score == pytest.approx(2 / 3)
    assert candidates["weibo:weibo-5"].cross_platform_score == pytest.approx(0.0)


def test_candidate_carries_provenance_and_excerpt() -> None:
    report = _scan()
    candidates = _by_id(report)
    breakout = candidates["weibo:weibo-5"]
    assert breakout.text_excerpt is not None
    assert breakout.text_excerpt.startswith("这是低粉爆款")
    assert "hot:weibo:weibo-5" in breakout.evidence_refs
    assert "engagement:weibo:weibo-5" in breakout.evidence_refs
    assert breakout.saves == 4100  # views 不计入互动量，但原样保留
    wb3 = candidates["weibo:wb-3"]  # 无 url：read 跳过，engagement 走 source_id 兜底
    assert wb3.likes == 6000
    assert wb3.author_followers is None
    assert wb3.final_score is not None  # rank + engagement 重归一化


def test_scan_accepts_dailyhot_as_hot_provider() -> None:
    payload = {
        "code": 200,
        "data": [
            {"id": i, "title": f"热搜{i}", "url": f"https://weibo.com/{i}", "hot": 1000 - i}
            for i in range(1, 31)
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    hot = DailyHotApiProvider(base_url="http://test", transport=httpx.MockTransport(handler))
    radar = ViralRadar(hot_provider=hot, reader=FixtureProvider(), platforms=("weibo",), top_n=5)
    report = asyncio.run(radar.scan())
    assert report.platforms[0].source == "dailyhot"
    assert report.platforms[0].items_fetched == 30
    assert len(report.candidates) == 5
    # fixture 没有这些 url 的深数据 → 候选仍产出（rank-only 评分，缺失不归零）
    assert all(candidate.final_score is not None for candidate in report.candidates)
