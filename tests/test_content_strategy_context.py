from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.content_strategy import bind_content_strategy, choose_content_strategy
from wechat_traffic_agent.traffic_learning import TopicTrafficSignal


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def signal():
    return TopicTrafficSignal(
        topic="crypto-market", article_count=0, orphan_article_count=0,
        search_rows=0, impressions=0, clicks=0, ctr=None, average_position=None,
        pageview_samples_24h=0, mean_pageviews_24h=None, trend_hits=0,
        demand_score=0.0, priority_score=0.0, exposure_status="UNKNOWN",
    )


def test_market_context_can_choose_follow_up_update_without_claiming_audience_effect(tmp_path):
    binding = choose_content_strategy(
        state_root=tmp_path, account="https://example.com", cycle="v2-market-1",
        topic="crypto-market", traffic_signal=signal(), as_of=NOW,
        preferred_profile_name="follow-up-update",
    )
    assert binding.profile.name == "follow-up-update"
    assert binding.profile.structure_pattern == "what-changed-why-next"
    assert binding.selection_mode == "CONTEXT_HEURISTIC"
    assert binding.publication_authority is False
    assert binding.causal_claim is False


def test_persisted_context_strategy_fails_closed_if_preference_changes(tmp_path):
    path = tmp_path / "strategy.json"
    bind_content_strategy(
        path, state_root=tmp_path, account="https://example.com", cycle="v2-market-1",
        topic="crypto-market", traffic_signal=signal(), decided_at=NOW,
        preferred_profile_name="follow-up-update",
    )
    with pytest.raises(ValueError, match="preferred profile"):
        bind_content_strategy(
            path, state_root=tmp_path, account="https://example.com", cycle="v2-market-1",
            topic="crypto-market", traffic_signal=signal(), decided_at=NOW,
            preferred_profile_name="evidence-brief",
        )
