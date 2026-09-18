from wechat_traffic_agent.audience_reward import (
    audience_reward,
    engagement_rate,
    reach_score,
)


def test_missing_engagement_denominator_stays_unknown():
    assert engagement_rate(engaged_readers_30s=0, tracked_pageviews=0) is None
    assert audience_reward(
        pageviews=100, baseline_pageviews=80, engagement_rate_value=None
    ) is None


def test_missing_reach_baseline_stays_unknown():
    assert reach_score(pageviews=100, baseline_pageviews=None) is None
    assert audience_reward(
        pageviews=100, baseline_pageviews=None, engagement_rate_value=0.5
    ) is None


def test_reward_combines_reach_and_quality_without_double_counting_volume():
    low_quality = audience_reward(
        pageviews=120, baseline_pageviews=100, engagement_rate_value=0.10
    )
    high_quality = audience_reward(
        pageviews=120, baseline_pageviews=100, engagement_rate_value=0.70
    )
    high_reach = audience_reward(
        pageviews=240, baseline_pageviews=100, engagement_rate_value=0.70
    )
    assert low_quality is not None and high_quality is not None and high_reach is not None
    assert low_quality < high_quality < high_reach
    assert 0.0 <= high_reach <= 1.0
