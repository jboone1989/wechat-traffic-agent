"""Outcome maturity: pending / partially mature / mature / missing.

A shorter window snapshot must never be read as a completed longer-window
outcome of zero. Clock and metrics are classified separately from reward.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from enum import Enum

from .models import ContentObjective, ObservationWindow, PerformanceObservation
from .objectives import ObjectiveMetric, policy_for

WINDOW_DURATIONS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "48h": timedelta(hours=48),
    "7d": timedelta(days=7),
}


class OutcomeMaturity(str, Enum):
    PENDING = "pending"
    PARTIALLY_MATURE = "partially_mature"
    MATURE = "mature"
    MISSING = "missing"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def window_elapsed(
    published_at: datetime | None,
    window: ObservationWindow,
    as_of: datetime,
) -> bool:
    if published_at is None:
        return False
    return _as_utc(as_of) >= _as_utc(published_at) + WINDOW_DURATIONS[window]


def _primary_present(obs: PerformanceObservation, objective: ContentObjective) -> bool:
    metric = policy_for(objective).primary_metric
    raw = {
        ObjectiveMetric.READS: obs.reads,
        ObjectiveMetric.FOLLOWS: obs.follows,
        ObjectiveMetric.DISCUSSIONS: obs.comments,
        ObjectiveMetric.SHARES: obs.shares,
        ObjectiveMetric.CONVERSIONS: obs.conversions,
    }[metric]
    return raw is not None


def _any_metric_present(obs: PerformanceObservation) -> bool:
    return any(
        value is not None
        for value in (
            obs.impressions,
            obs.reads,
            obs.shares,
            obs.follows,
            obs.comments,
            obs.conversions,
        )
    )


def window_maturity(
    history: Sequence[PerformanceObservation],
    *,
    post_id: str,
    window: ObservationWindow,
    as_of: datetime,
    objective: ContentObjective = ContentObjective.READS,
) -> OutcomeMaturity:
    """Classify one post's outcome for one documented window at ``as_of``.

    Records labeled for a different window are not evidence for this one.
    A 24h snapshot therefore cannot become a 7d zero.
    """
    matching = [
        obs
        for obs in history
        if obs.post_id == post_id and obs.observation_window == window
    ]
    published_at = next(
        (obs.published_at for obs in matching if obs.published_at is not None),
        next(
            (obs.published_at for obs in history if obs.post_id == post_id and obs.published_at is not None),
            None,
        ),
    )
    elapsed = window_elapsed(published_at, window, as_of)
    has_primary = any(_primary_present(obs, objective) for obs in matching)
    has_any = any(_any_metric_present(obs) for obs in matching)
    if not elapsed:
        return OutcomeMaturity.PARTIALLY_MATURE if has_any else OutcomeMaturity.PENDING
    if has_primary:
        return OutcomeMaturity.MATURE
    return OutcomeMaturity.MISSING


def outcome_is_scorable(
    obs: PerformanceObservation,
    as_of: datetime,
    *,
    objective: ContentObjective | None = None,
) -> bool:
    """Mature labeled windows may score; unknown clocks keep legacy UNKNOWN rules.

    A labeled observation with a known publish time is scored only after its
    own window has elapsed with a primary metric. Missing timestamps or
    unlabeled windows are not newly excluded here.
    """
    if obs.observation_window is None or obs.published_at is None:
        return True
    resolved = objective or obs.objective or ContentObjective.READS
    return (
        window_maturity(
            [obs],
            post_id=obs.post_id,
            window=obs.observation_window,
            as_of=as_of,
            objective=resolved,
        )
        is OutcomeMaturity.MATURE
    )
