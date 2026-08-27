"""Deterministic viral scoring primitives: no LLM ever touches numbers.

All sub-scores are platform-local percentiles, so raw platform metrics are
never compared cross-platform. Missing data stays missing: an unavailable
sub-score drops out of the weighted average and the remaining weights are
re-normalized (unavailable is never zero).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .sources import EngagementSnapshot, HotItem

WEIGHTS: dict[str, float] = {
    "rank": 0.35,
    "engagement": 0.25,
    "breakout": 0.25,
    "freshness": 0.15,
}


def normalize_title(title: str) -> str:
    """Whitespace-insensitive, case-insensitive dedup key."""
    return "".join(title.split()).casefold()


def percentile_score(value: float, population: Sequence[float]) -> float:
    """Average-rank percentile of ``value`` inside ``population`` (higher = better).

    Ties share the mid-percentile. A single-element population scores 1.0:
    that item is the platform-local best by definition.
    """
    n = len(population)
    if n <= 1:
        return 1.0
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    average_rank = below + (equal + 1) / 2.0  # 1-based, ascending
    return (average_rank - 1.0) / (n - 1.0)


def rank_score(rank: int | None, population_size: int) -> float | None:
    """Hot-list position strength, platform-local. Rank 1 is best."""
    if rank is None:
        return None
    if population_size <= 1:
        return 1.0
    score = (population_size - rank) / (population_size - 1)
    return max(0.0, min(1.0, score))


def engagement_value(snapshot: EngagementSnapshot) -> int | None:
    """Interaction magnitude: likes + comments + shares + saves.

    Views are excluded: reach is not engagement and not every platform
    exposes views. Partial fields sum to a lower bound; all-missing is None.
    """
    parts = [
        value
        for value in (snapshot.likes, snapshot.comments, snapshot.shares, snapshot.saves)
        if value is not None
    ]
    return sum(parts) if parts else None


def breakout_ratio(engagement: int | None, followers: int | None) -> float | None:
    """Performance versus the author's own distribution capability."""
    if engagement is None or followers is None or followers <= 0:
        return None
    return engagement / followers


def combine_final(subscores: Mapping[str, float | None]) -> float | None:
    """Weighted mean over available sub-scores, re-normalized over present weights."""
    available = {name: value for name, value in subscores.items() if value is not None}
    if not available:
        return None
    weight_sum = sum(WEIGHTS[name] for name in available)
    return sum(WEIGHTS[name] * value for name, value in available.items()) / weight_sum


def dedup_items(items: Sequence[HotItem]) -> tuple[HotItem, ...]:
    """Drop same-title duplicates within one platform, keeping the best rank."""
    best: dict[str, HotItem] = {}
    for item in items:
        key = normalize_title(item.title)
        current = best.get(key)
        if current is None or _better_rank(item, current):
            best[key] = item
    return tuple(best.values())


def _better_rank(candidate: HotItem, current: HotItem) -> bool:
    if candidate.rank is None:
        return False
    if current.rank is None:
        return True
    return candidate.rank < current.rank
