from __future__ import annotations

import pytest

from wechat_traffic_agent.scoring import (
    breakout_ratio,
    combine_final,
    dedup_items,
    engagement_value,
    normalize_title,
    percentile_score,
    rank_score,
)
from wechat_traffic_agent.sources import EngagementSnapshot, HotItem


def test_percentile_score_orders_values_and_shares_ties() -> None:
    population = [10.0, 20.0, 20.0, 30.0]
    assert percentile_score(30.0, population) == pytest.approx(1.0)
    assert percentile_score(10.0, population) == pytest.approx(0.0)
    assert percentile_score(20.0, population) == pytest.approx(0.5)
    assert percentile_score(20.0, [20.0]) == pytest.approx(1.0)  # 单元素即平台内最佳


def test_rank_score_clamps_and_handles_missing() -> None:
    assert rank_score(1, 43) == pytest.approx(1.0)
    assert rank_score(43, 43) == pytest.approx(0.0)
    assert rank_score(99, 43) == pytest.approx(0.0)  # rank 超界 → 钳到 0
    assert rank_score(1, 1) == pytest.approx(1.0)
    assert rank_score(None, 43) is None


def test_engagement_value_sums_present_and_ignores_views() -> None:
    snapshot = EngagementSnapshot(
        content_id="c", platform="weibo", views=250000, likes=100, comments=20, shares=5, saves=3
    )
    assert engagement_value(snapshot) == 128
    assert engagement_value(EngagementSnapshot(content_id="c", platform="weibo")) is None
    assert engagement_value(EngagementSnapshot(content_id="c", platform="weibo", views=10)) is None


def test_breakout_ratio_requires_engagement_and_positive_followers() -> None:
    assert breakout_ratio(6000, 600) == pytest.approx(10.0)
    assert breakout_ratio(None, 600) is None
    assert breakout_ratio(6000, None) is None
    assert breakout_ratio(6000, 0) is None


def test_combine_final_renormalizes_missing_weights() -> None:
    # 只有 rank + engagement：权重 35/25 归一化为 0.5833/0.4167
    score = combine_final({"rank": 1.0, "engagement": 0.0})
    assert score == pytest.approx(0.35 / 0.60)


def test_combine_final_missing_is_not_zero() -> None:
    missing = combine_final({"rank": 0.8, "engagement": None, "freshness": 0.5})
    zeroed = combine_final({"rank": 0.8, "engagement": 0.0, "freshness": 0.5})
    assert missing is not None and zeroed is not None
    assert missing > zeroed  # 缺失剔除权重，而非按 0 计入


def test_combine_final_all_missing_is_none() -> None:
    assert combine_final({"rank": None, "engagement": None}) is None


def test_normalize_title_is_whitespace_and_case_insensitive() -> None:
    assert normalize_title("  Hello   World ") == normalize_title("helloworld")
    assert normalize_title("微博 样本 一") == normalize_title("微博样本一")


def test_dedup_items_keeps_best_rank_and_preserves_first_seen_order() -> None:
    items = (
        HotItem(source_id="a", platform="weibo", title="同题", rank=5),
        HotItem(source_id="b", platform="weibo", title=" 同题 ", rank=2),
        HotItem(source_id="c", platform="weibo", title="另一题", rank=1),
        HotItem(source_id="d", platform="weibo", title="同题", rank=9),
        HotItem(source_id="e", platform="weibo", title="无排名", rank=None),
    )
    kept = dedup_items(items)
    assert [item.source_id for item in kept] == ["b", "c", "e"]
