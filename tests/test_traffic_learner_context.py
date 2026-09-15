"""TrafficLearner must isolate emotion evidence by audience and window."""
from __future__ import annotations

from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import ContentObjective, EmotionAngle, PerformanceObservation


def _saturated(
    *,
    audience_key: str,
    observation_window: str,
    winner: EmotionAngle,
    winner_reads: int,
    loser_reads: int = 10,
    objective: ContentObjective | None = ContentObjective.READS,
) -> list[PerformanceObservation]:
    history: list[PerformanceObservation] = []
    for angle in EmotionAngle:
        for sample in range(2):
            history.append(
                PerformanceObservation(
                    post_id=f"{audience_key}-{observation_window}-{angle.value}-{sample}",
                    angle=angle,
                    impressions=100,
                    reads=winner_reads if angle is winner else loser_reads,
                    audience_key=audience_key,
                    objective=objective,
                    observation_window=observation_window,
                    **{
                        key: (winner_reads if angle is winner else loser_reads)
                        for key, _ in []
                    },
                )
            )
    return history


def test_unscoped_recommend_does_not_pool_labeled_foreign_audiences() -> None:
    # Audience B's nostalgia (90 reads) dominates audience A's resonance (50).
    # If histories are pooled, TrafficLearner exploits B's winner for everyone.
    history_a = _saturated(
        audience_key="audience-a",
        observation_window="24h",
        winner=EmotionAngle.RESONANCE,
        winner_reads=50,
    )
    history_b = _saturated(
        audience_key="audience-b",
        observation_window="24h",
        winner=EmotionAngle.NOSTALGIA,
        winner_reads=90,
    )
    learner = TrafficLearner()
    pooled = history_a + history_b
    # Labeled history is invisible to an unscoped call, so B's winner
    # cannot leak even when the caller passes only B's rows.
    assert learner.recommend(history_b, ContentObjective.READS) is not EmotionAngle.NOSTALGIA
    assert learner.recommend(pooled, ContentObjective.READS) is not EmotionAngle.NOSTALGIA
    assert (
        learner.recommend(
            history_b,
            ContentObjective.READS,
            audience_key="audience-b",
            observation_window="24h",
        )
        is EmotionAngle.NOSTALGIA
    )


def test_recommend_for_audience_a_ignores_audience_b_history() -> None:
    history_a = _saturated(
        audience_key="audience-a",
        observation_window="24h",
        winner=EmotionAngle.RESONANCE,
        winner_reads=50,
    )
    history_b = _saturated(
        audience_key="audience-b",
        observation_window="24h",
        winner=EmotionAngle.NOSTALGIA,
        winner_reads=90,
    )
    learner = TrafficLearner()
    pooled = history_a + history_b
    assert (
        learner.recommend(
            pooled,
            ContentObjective.READS,
            audience_key="audience-a",
            observation_window="24h",
        )
        is EmotionAngle.RESONANCE
    )
    assert (
        learner.recommend(
            pooled,
            ContentObjective.READS,
            audience_key="audience-b",
            observation_window="24h",
        )
        is EmotionAngle.NOSTALGIA
    )


def test_incompatible_observation_windows_do_not_leak() -> None:
    history_24h = _saturated(
        audience_key="audience-a",
        observation_window="24h",
        winner=EmotionAngle.RESONANCE,
        winner_reads=50,
    )
    history_7d = _saturated(
        audience_key="audience-a",
        observation_window="7d",
        winner=EmotionAngle.NOSTALGIA,
        winner_reads=90,
    )
    learner = TrafficLearner()
    pooled = history_24h + history_7d
    assert (
        learner.recommend(
            pooled,
            ContentObjective.READS,
            audience_key="audience-a",
            observation_window="24h",
        )
        is EmotionAngle.RESONANCE
    )
    assert (
        learner.recommend(
            pooled,
            ContentObjective.READS,
            audience_key="audience-a",
            observation_window="7d",
        )
        is EmotionAngle.NOSTALGIA
    )


def test_estimates_count_only_matching_audience_and_window() -> None:
    history_a = _saturated(
        audience_key="audience-a",
        observation_window="24h",
        winner=EmotionAngle.RESONANCE,
        winner_reads=50,
    )
    history_b = _saturated(
        audience_key="audience-b",
        observation_window="24h",
        winner=EmotionAngle.NOSTALGIA,
        winner_reads=90,
    )
    learner = TrafficLearner()
    estimates = learner.estimates(
        history_a + history_b,
        ContentObjective.READS,
        audience_key="audience-a",
        observation_window="24h",
    )
    by_angle = {item.angle: item for item in estimates}
    assert by_angle[EmotionAngle.RESONANCE].samples == 2
    assert by_angle[EmotionAngle.NOSTALGIA].samples == 2
    assert by_angle[EmotionAngle.RESONANCE].scored_samples == 2
    # Foreign audience samples must not inflate impressions in A's context.
    assert by_angle[EmotionAngle.NOSTALGIA].impressions == 200
    assert by_angle[EmotionAngle.RESONANCE].impressions == 200
    assert (by_angle[EmotionAngle.RESONANCE].reward or 0) > (
        by_angle[EmotionAngle.NOSTALGIA].reward or 0
    )


def test_unlabeled_objective_is_legacy_reads_not_a_wildcard() -> None:
    """Pre-PR6 rows have objective=None. That equals READS, never other objectives."""
    history: list[PerformanceObservation] = []
    for angle in EmotionAngle:
        for sample in range(2):
            history.append(
                PerformanceObservation(
                    post_id=f"legacy-{angle.value}-{sample}",
                    angle=angle,
                    impressions=100,
                    reads=50 if angle is EmotionAngle.RESONANCE else 10,
                    follows=90 if angle is EmotionAngle.NOSTALGIA else 1,
                    comments=90 if angle is EmotionAngle.NOSTALGIA else 1,
                    conversions=90 if angle is EmotionAngle.NOSTALGIA else 1,
                    audience_key="audience-a",
                    objective=None,
                    observation_window="24h",
                )
            )
    learner = TrafficLearner()
    scoped = dict(
        audience_key="audience-a",
        observation_window="24h",
    )
    assert (
        learner.recommend(history, ContentObjective.READS, **scoped)
        is EmotionAngle.RESONANCE
    )
    assert (
        learner.recommend(history, ContentObjective.FOLLOWS, **scoped)
        is not EmotionAngle.NOSTALGIA
    )
    assert (
        learner.recommend(history, ContentObjective.DISCUSSION, **scoped)
        is not EmotionAngle.NOSTALGIA
    )
    assert (
        learner.recommend(history, ContentObjective.CONVERSION, **scoped)
        is not EmotionAngle.NOSTALGIA
    )
