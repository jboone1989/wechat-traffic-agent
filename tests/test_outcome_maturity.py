"""W0.3: outcome maturity must be explicit; 24h must not become 7d zero."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.models import ContentObjective, EmotionAngle, PerformanceObservation
from wechat_traffic_agent.outcome_maturity import OutcomeMaturity, window_maturity
from wechat_traffic_agent.planner import VARIABLES
from wechat_traffic_agent.strategy_learning import StrategyLearner


PUBLISHED = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _obs(
    *,
    post_id: str = "post-1",
    window: str,
    reads: int | None,
    published_at: datetime = PUBLISHED,
) -> PerformanceObservation:
    return PerformanceObservation(
        post_id=post_id,
        angle=EmotionAngle.RELIEF,
        experiment_id=f"{post_id}-exp",
        variable_under_test="emotion",
        treatment_value="relief",
        audience_key="silver",
        objective=ContentObjective.READS,
        observation_window=window,
        reads=reads,
        published_at=published_at,
    )


def test_24h_snapshot_is_pending_for_7d_not_a_zero_outcome() -> None:
    as_of = PUBLISHED + timedelta(hours=24)
    history = [_obs(window="24h", reads=500)]

    assert (
        window_maturity(
            history,
            post_id="post-1",
            window="7d",
            as_of=as_of,
        )
        is OutcomeMaturity.PENDING
    )

    learner = StrategyLearner(min_samples=1)
    estimates = learner.estimates(history, as_of=as_of)
    seven_day = [row for row in estimates if row.context_key.endswith("7d)")]
    assert seven_day == []
    assert all(row.avg_reward != 0.0 or row.scored_samples == 0 for row in estimates)

    decision = learner.recommend(
        history,
        candidate_variables=VARIABLES,
        audience_key="silver",
        objective=ContentObjective.READS,
        observation_window="7d",
        as_of=as_of,
    )
    assert decision.exploration
    assert "min_samples" in decision.reason


def test_elapsed_7d_without_7d_record_is_missing_not_zero() -> None:
    as_of = PUBLISHED + timedelta(days=8)
    history = [_obs(window="24h", reads=500)]

    assert (
        window_maturity(
            history,
            post_id="post-1",
            window="7d",
            as_of=as_of,
        )
        is OutcomeMaturity.MISSING
    )

    estimates = StrategyLearner().estimates(history, as_of=as_of)
    assert [row for row in estimates if row.context_key.endswith("7d)")] == []


def test_premature_7d_labeled_zero_is_partially_mature_and_unscored() -> None:
    as_of = PUBLISHED + timedelta(hours=24)
    history = [_obs(window="7d", reads=0)]

    assert (
        window_maturity(
            history,
            post_id="post-1",
            window="7d",
            as_of=as_of,
        )
        is OutcomeMaturity.PARTIALLY_MATURE
    )

    estimate = StrategyLearner().estimates(history, as_of=as_of)[0]
    assert estimate.samples == 1
    assert estimate.scored_samples == 0
    assert estimate.avg_reward is None
    assert estimate.lift_vs_trailing_baseline is None


def test_mature_24h_after_window_elapses_with_primary_metric() -> None:
    as_of = PUBLISHED + timedelta(hours=24)
    history = [_obs(window="24h", reads=500)]

    assert (
        window_maturity(
            history,
            post_id="post-1",
            window="24h",
            as_of=as_of,
        )
        is OutcomeMaturity.MATURE
    )

    estimate = StrategyLearner().estimates(history, as_of=as_of)[0]
    assert estimate.scored_samples == 1
    assert estimate.avg_reward is not None


def test_early_24h_metrics_are_partially_mature_not_scored() -> None:
    as_of = PUBLISHED + timedelta(hours=6)
    history = [_obs(window="24h", reads=40)]

    assert (
        window_maturity(
            history,
            post_id="post-1",
            window="24h",
            as_of=as_of,
        )
        is OutcomeMaturity.PARTIALLY_MATURE
    )

    estimate = StrategyLearner().estimates(history, as_of=as_of)[0]
    assert estimate.scored_samples == 0
    assert estimate.avg_reward is None


def test_elapsed_window_without_primary_metric_is_missing() -> None:
    as_of = PUBLISHED + timedelta(hours=24)
    history = [_obs(window="24h", reads=None)]

    assert (
        window_maturity(
            history,
            post_id="post-1",
            window="24h",
            as_of=as_of,
        )
        is OutcomeMaturity.MISSING
    )
