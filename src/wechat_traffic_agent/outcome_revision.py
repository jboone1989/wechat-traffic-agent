"""Idempotent outcome revision: one publication/window/metric snapshot, one sample.

Repeat ingest of the same metric revision must not create an independent
sample. A later correction of those metrics supersedes the prior revision
and keeps it in lineage. Distinct windows stay independent.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .models import PerformanceObservation


@dataclass(frozen=True)
class OutcomeRevision:
    current: PerformanceObservation
    superseded: tuple[PerformanceObservation, ...] = ()


def _revision_key(obs: PerformanceObservation) -> tuple[str, str | None, object]:
    return (obs.post_id, obs.observation_window, obs.objective)


def _metric_fingerprint(obs: PerformanceObservation) -> tuple:
    return (
        obs.impressions,
        obs.reads,
        obs.shares,
        obs.follows,
        obs.comments,
        obs.conversions,
    )


def collapse_outcome_revisions(
    history: Sequence[PerformanceObservation],
) -> list[OutcomeRevision]:
    """Collapse history so each publication/window keeps one current revision."""
    ordered_keys: list[tuple[str, str | None, object]] = []
    buckets: dict[tuple[str, str | None, object], list[PerformanceObservation]] = {}
    for obs in history:
        key = _revision_key(obs)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = [obs]
            ordered_keys.append(key)
            continue
        if _metric_fingerprint(bucket[-1]) == _metric_fingerprint(obs):
            continue
        bucket.append(obs)
    return [
        OutcomeRevision(current=bucket[-1], superseded=tuple(bucket[:-1]))
        for key in ordered_keys
        for bucket in (buckets[key],)
    ]
