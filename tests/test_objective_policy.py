from __future__ import annotations

import pytest

from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import (
    ContentObjective,
    EmotionAngle,
    PerformanceObservation,
)
from wechat_traffic_agent.objectives import ObjectiveMetric, policy_for


def _obs(**changes) -> PerformanceObservation:
    values = {
        "post_id": "p1",
        "angle": EmotionAngle.RESONANCE,
        "impressions": 1000,
        "reads": 500,
        "shares": 50,
        "follows": 20,
        "comments": 30,
        "conversions": 5,
    }
    values.update(changes)
    return PerformanceObservation(**values)


def test_objectives_yield_different_rewards_for_the_same_observation() -> None:
    obs = _obs()
    rewards = {
        objective: policy_for(objective).reward(obs) for objective in ContentObjective
    }
    assert all(reward is not None for reward in rewards.values())
    assert len(set(rewards.values())) == 4
    assert policy_for(ContentObjective.READS).primary_metric is ObjectiveMetric.READS
    assert policy_for(ContentObjective.FOLLOWS).primary_metric is ObjectiveMetric.FOLLOWS
    assert policy_for(ContentObjective.DISCUSSION).primary_metric is ObjectiveMetric.DISCUSSIONS
    assert policy_for(ContentObjective.CONVERSION).primary_metric is ObjectiveMetric.CONVERSIONS


def test_missing_secondary_metric_renormalizes_remaining_weights() -> None:
    obs = _obs(shares=None)
    reward = policy_for(ContentObjective.FOLLOWS).reward(obs)
    follows_rate = 20 / 1000
    reads_rate = (500 + 2.0) / (1000 + 20.0)
    expected = (0.60 * follows_rate + 0.25 * reads_rate) / 0.85
    assert reward == pytest.approx(expected)


def test_primary_only_with_all_secondaries_missing_equals_primary_rate() -> None:
    obs = _obs(reads=None, shares=None)
    reward = policy_for(ContentObjective.FOLLOWS).reward(obs)
    assert reward == pytest.approx(20 / 1000)


def test_all_metrics_missing_is_unknown_not_zero() -> None:
    obs = PerformanceObservation(post_id="p1", angle=EmotionAngle.RELIEF)
    assert policy_for(ContentObjective.READS).reward(obs) is None
    assert policy_for(ContentObjective.CONVERSION).reward(obs) is None


def test_learner_treats_unavailable_primary_metric_as_unexplored() -> None:
    learner = TrafficLearner()
    history = [
        PerformanceObservation(
            post_id=f"{angle.value}-{sample}",
            angle=angle,
            impressions=100,
            reads=10,
        )
        for angle in EmotionAngle
        for sample in range(3)
    ]
    follow_estimates = learner.estimates(history, ContentObjective.FOLLOWS)
    assert all(item.reward is None for item in follow_estimates)
    assert all(item.scored_samples == 0 for item in follow_estimates)
    # READS 目标下同一份历史有得分，会正常利用……
    assert learner.recommend(history, ContentObjective.READS) is EmotionAngle.RESONANCE
    # ……而 FOLLOWS 目标下没有任何奖励证据，必须继续探索，绝不能把缺失当 0 分利用。
    assert learner.recommend(history, ContentObjective.FOLLOWS) is not EmotionAngle.RESONANCE
