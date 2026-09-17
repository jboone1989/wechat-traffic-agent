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


def test_virtualbrain_belief_history_changes_next_strategy_under_same_current_input(tmp_path):
    import hashlib
    profiles = [
        "explainer-question", "evidence-brief", "comparison-guide",
        "practical-guide", "follow-up-update", "topic-pillar",
    ]
    cycle = next(
        f"mind-control-{i}" for i in range(1000)
        if int(hashlib.sha256(f"mind-control-{i}|crypto-market".encode()).hexdigest()[:8], 16) % 100 >= 25
    )
    counts = {name: 2 for name in profiles}
    base = {name: 0.5 for name in profiles}
    evidence_history = dict(base)
    evidence_history["evidence-brief"] = 0.9
    evidence_history["practical-guide"] = 0.2
    practical_history = dict(base)
    practical_history["evidence-brief"] = 0.2
    practical_history["practical-guide"] = 0.9
    common = dict(
        state_root=tmp_path, account="https://example.com", cycle=cycle,
        topic="crypto-market", traffic_signal=signal(), as_of=NOW,
    )
    first = choose_content_strategy(**common, mind_strategy_context={
        "topic": "crypto-market", "samples_by_profile": counts,
        "mean_relative_reward_by_profile": evidence_history, "evidence_refs": ["history:a"],
    })
    second = choose_content_strategy(**common, mind_strategy_context={
        "topic": "crypto-market", "samples_by_profile": counts,
        "mean_relative_reward_by_profile": practical_history, "evidence_refs": ["history:b"],
    })
    assert first.selection_mode == second.selection_mode == "VIRTUALBRAIN_BELIEF"
    assert first.profile.name == "evidence-brief"
    assert second.profile.name == "practical-guide"
    assert first.profile.name != second.profile.name
