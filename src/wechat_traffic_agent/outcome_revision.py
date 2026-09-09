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
    seen: dict[tuple[str, str | None, object], set[tuple]] = {}
    seen_revisions: dict[tuple[str, str | None, object], set[str]] = {}
    for obs in history:
        key = _revision_key(obs)
        fingerprint = _metric_fingerprint(obs)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = [obs]
            seen[key] = {fingerprint}
            seen_revisions[key] = {obs.source_revision} if obs.source_revision else set()
            ordered_keys.append(key)
            continue
        if obs.source_revision and obs.source_revision in seen_revisions[key]:
            continue
        if fingerprint in seen[key]:
            continue
        seen[key].add(fingerprint)
        if obs.source_revision:
            seen_revisions[key].add(obs.source_revision)
        bucket.append(obs)
    return [
        OutcomeRevision(current=bucket[-1], superseded=tuple(bucket[:-1]))
        for key in ordered_keys
        for bucket in (buckets[key],)
    ]


def current_active_observations(
    history: Sequence[PerformanceObservation],
) -> list[PerformanceObservation]:
    """Collapsed current revisions that have not been withdrawn."""
    return [
        revision.current
        for revision in collapse_outcome_revisions(history)
        if revision.current.withdrawn_at is None
    ]
