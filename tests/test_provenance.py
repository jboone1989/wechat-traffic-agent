"""W1: extend existing observation records with provenance, no new evidence DB."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import ContentObjective, EmotionAngle, PerformanceObservation
from wechat_traffic_agent.outcome_revision import (
    collapse_outcome_revisions,
    current_active_observations,
)
from wechat_traffic_agent.strategy_learning import StrategyLearner

PUBLISHED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
DECIDED = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
FIRST_SEEN = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
SOURCE_PUBLISHED = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
AS_OF = PUBLISHED + timedelta(hours=30)


def _obs(*, reads: int, **extra) -> PerformanceObservation:
    payload = {
        "post_id": "post-1",
        "angle": EmotionAngle.RELIEF,
        "experiment_id": "post-1-exp",
        "variable_under_test": "emotion",
        "treatment_value": "relief",
        "audience_key": "silver",
        "objective": ContentObjective.READS,
        "observation_window": "24h",
        "reads": reads,
        "published_at": PUBLISHED,
    }
    payload.update(extra)
    return PerformanceObservation(**payload)


def test_legacy_observation_still_validates_without_provenance_fields() -> None:
    obs = PerformanceObservation(post_id="legacy", angle=EmotionAngle.RELIEF, reads=10)
    restored = PerformanceObservation.model_validate_json(obs.model_dump_json())
    assert restored.decision_time is None
    assert restored.first_observed_at is None
    assert restored.source_published_at is None
    assert restored.source_revision is None
    assert restored.supersedes is None
    assert restored.withdrawn_at is None
    assert restored.upstream_dependency_refs == ()


def test_provenance_fields_round_trip_on_existing_observation_record() -> None:
    obs = _obs(
        reads=400,
        decision_time=DECIDED,
        first_observed_at=FIRST_SEEN,
        source_published_at=SOURCE_PUBLISHED,
        source_revision="rev-b",
        supersedes="rev-a",
        withdrawn_at=None,
        upstream_dependency_refs=("experiment:post-1-exp", "topic:weibo-5"),
    )
    restored = PerformanceObservation.model_validate_json(obs.model_dump_json())
    assert restored.decision_time == DECIDED
    assert restored.first_observed_at == FIRST_SEEN
    assert restored.source_published_at == SOURCE_PUBLISHED
    assert restored.source_revision == "rev-b"
    assert restored.supersedes == "rev-a"
    assert restored.withdrawn_at is None
    assert restored.upstream_dependency_refs == ("experiment:post-1-exp", "topic:weibo-5")
    assert restored.published_at == PUBLISHED
    assert restored.first_observed_at != restored.published_at
    assert restored.decision_time != restored.published_at


def test_replayed_source_revision_does_not_add_an_independent_sample() -> None:
    first = _obs(reads=100, source_revision="rev-a")
    replay = _obs(reads=9000, source_revision="rev-a")
    collapsed = collapse_outcome_revisions([first, replay])
    assert len(collapsed) == 1
    assert collapsed[0].current == first
    assert collapsed[0].superseded == ()


def test_named_correction_keeps_supersedes_lineage() -> None:
    original = _obs(reads=100, source_revision="rev-a")
    correction = _obs(reads=400, source_revision="rev-b", supersedes="rev-a")
    collapsed = collapse_outcome_revisions([original, correction])
    assert collapsed[0].current.source_revision == "rev-b"
    assert collapsed[0].current.supersedes == "rev-a"
    assert collapsed[0].superseded[0].source_revision == "rev-a"


def test_withdrawn_current_revision_is_not_scored_as_agent_evidence() -> None:
    live = _obs(reads=400, post_id="post-live")
    withdrawn = _obs(
        reads=9000,
        post_id="post-withdrawn",
        source_revision="rev-w",
        withdrawn_at=FIRST_SEEN,
    )
    as_of = AS_OF
    strategy = StrategyLearner(min_samples=1).estimates([live, withdrawn], as_of=as_of)
    assert len(strategy) == 1
    assert strategy[0].samples == 1
    assert strategy[0].scored_samples == 1

    traffic = TrafficLearner().estimates(
        [live, withdrawn],
        ContentObjective.READS,
        audience_key="silver",
        observation_window="24h",
        as_of=as_of,
    )
    relief = next(item for item in traffic if item.angle is EmotionAngle.RELIEF)
    assert relief.samples == 1
    assert relief.scored_samples == 1


def test_same_metric_named_revision_supersedes_and_keeps_lineage() -> None:
    original = _obs(reads=400, source_revision="rev-a")
    correction = _obs(reads=400, source_revision="rev-b", supersedes="rev-a")
    collapsed = collapse_outcome_revisions([original, correction])
    assert collapsed[0].current.source_revision == "rev-b"
    assert collapsed[0].current.supersedes == "rev-a"
    assert collapsed[0].superseded[0].source_revision == "rev-a"


def test_same_metric_withdrawal_is_not_scored_as_live_evidence() -> None:
    original = _obs(reads=400, source_revision="rev-a")
    withdrawn = _obs(
        reads=400,
        source_revision="rev-b",
        supersedes="rev-a",
        withdrawn_at=FIRST_SEEN,
    )
    collapsed = collapse_outcome_revisions([original, withdrawn])
    assert collapsed[0].current.source_revision == "rev-b"
    assert collapsed[0].current.withdrawn_at == FIRST_SEEN
    assert current_active_observations([original, withdrawn]) == []

    strategy = StrategyLearner(min_samples=1).estimates(
        [original, withdrawn], as_of=AS_OF
    )
    assert strategy == []
    traffic = TrafficLearner().estimates(
        [original, withdrawn],
        ContentObjective.READS,
        audience_key="silver",
        observation_window="24h",
        as_of=AS_OF,
    )
    relief = next(item for item in traffic if item.angle is EmotionAngle.RELIEF)
    assert relief.samples == 0
    assert relief.scored_samples == 0
