"""W0.4: same publication/window/metric revision must not add independent samples."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.models import ContentObjective, EmotionAngle, PerformanceObservation
from wechat_traffic_agent.outcome_revision import collapse_outcome_revisions
from wechat_traffic_agent.strategy_learning import StrategyLearner

PUBLISHED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
AS_OF = PUBLISHED + timedelta(hours=24)


def _obs(
    *,
    post_id: str = "post-1",
    window: str = "24h",
    reads: int | None,
    treatment: str = "relief",
) -> PerformanceObservation:
    return PerformanceObservation(
        post_id=post_id,
        angle=EmotionAngle.RELIEF,
        experiment_id=f"{post_id}-exp",
        variable_under_test="emotion",
        treatment_value=treatment,
        audience_key="silver",
        objective=ContentObjective.READS,
        observation_window=window,
        reads=reads,
        published_at=PUBLISHED,
    )


def test_repeat_ingest_of_same_revision_does_not_add_an_independent_sample() -> None:
    first = _obs(reads=500)
    repeat = _obs(reads=500)
    collapsed = collapse_outcome_revisions([first, repeat])
    assert len(collapsed) == 1
    assert collapsed[0].current == first
    assert collapsed[0].superseded == ()

    estimate = StrategyLearner().estimates([first, repeat], as_of=AS_OF)[0]
    assert estimate.samples == 1
    assert estimate.scored_samples == 1


def test_late_correction_supersedes_prior_revision_and_keeps_lineage() -> None:
    original = _obs(reads=100)
    correction = _obs(reads=400)
    collapsed = collapse_outcome_revisions([original, correction])
    assert len(collapsed) == 1
    assert collapsed[0].current == correction
    assert collapsed[0].superseded == (original,)

    estimate = StrategyLearner().estimates([original, correction], as_of=AS_OF)[0]
    assert estimate.samples == 1
    assert estimate.scored_samples == 1
    only_correction = StrategyLearner().estimates([correction], as_of=AS_OF)[0]
    assert estimate.avg_reward == only_correction.avg_reward


def test_replay_of_superseded_revision_does_not_rewind_current() -> None:
    original = _obs(reads=100)
    correction = _obs(reads=400)
    replay = _obs(reads=100)
    collapsed = collapse_outcome_revisions([original, correction, replay])
    assert len(collapsed) == 1
    assert collapsed[0].current == correction
    assert collapsed[0].superseded == (original,)

    strategy = StrategyLearner().estimates(
        [original, correction, replay], as_of=AS_OF
    )[0]
    assert strategy.samples == 1
    assert strategy.scored_samples == 1
    only_correction = StrategyLearner().estimates([correction], as_of=AS_OF)[0]
    assert strategy.avg_reward == only_correction.avg_reward

    from wechat_traffic_agent.learning import TrafficLearner

    traffic = TrafficLearner().estimates(
        [original, correction, replay],
        ContentObjective.READS,
        audience_key="silver",
        observation_window="24h",
        as_of=AS_OF,
    )
    winner = next(row for row in traffic if row.angle is EmotionAngle.RELIEF)
    assert winner.samples == 1
    assert winner.scored_samples == 1


def test_distinct_windows_remain_independent_samples() -> None:
    day = _obs(window="24h", reads=500)
    week = _obs(window="7d", reads=800)
    collapsed = collapse_outcome_revisions([day, week])
    assert len(collapsed) == 2
    week_as_of = PUBLISHED + timedelta(days=7)
    keys = {row.context_key for row in StrategyLearner().estimates([day, week], as_of=week_as_of)}
    assert keys == {"(silver, reads, 24h)", "(silver, reads, 7d)"}
