"""Deterministic reader-value projection from independently collected Website evidence.

Reach and engagement quality stay separate until both are known. Missing first-party
engagement coverage is UNKNOWN, never synthetic zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .channel_evidence import ChannelEvidence, ChannelEvidenceStore

PAGEVIEW_SOURCE = "nginx-access-log:v1"
ENGAGEMENT_SOURCE = "first-party-engagement:v1"
MATURE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class AudienceObservation:
    publication_id: str
    pageviews: float
    tracked_pageviews: float
    engaged_readers_30s: float
    engagement_rate: float
    published_at: datetime
    observed_at: datetime
    evidence_refs: tuple[str, ...]


def engagement_rate(*, engaged_readers_30s: float, tracked_pageviews: float) -> float | None:
    engaged = float(engaged_readers_30s)
    tracked = float(tracked_pageviews)
    if engaged < 0 or tracked < 0:
        raise ValueError("audience metrics must be non-negative")
    if tracked <= 0:
        return None
    return min(1.0, engaged / tracked)


def reach_score(*, pageviews: float, baseline_pageviews: float | None) -> float | None:
    views = float(pageviews)
    if views < 0:
        raise ValueError("pageviews must be non-negative")
    if baseline_pageviews is None:
        return None
    baseline = float(baseline_pageviews)
    if baseline < 0:
        raise ValueError("baseline pageviews must be non-negative")
    if baseline == 0:
        return 1.0 if views > 0 else 0.5
    return views / (views + baseline)


def audience_reward(
    *, pageviews: float, baseline_pageviews: float | None, engagement_rate_value: float | None
) -> float | None:
    """Combine reach and reader quality without double-counting raw traffic volume."""
    reach = reach_score(pageviews=pageviews, baseline_pageviews=baseline_pageviews)
    if reach is None or engagement_rate_value is None:
        return None
    quality = float(engagement_rate_value)
    if not 0.0 <= quality <= 1.0:
        raise ValueError("engagement rate must be between zero and one")
    if reach + quality == 0:
        return 0.0
    return 2.0 * reach * quality / (reach + quality)


def collect_mature_audience_observations(
    *, state_root: Path, account: str, as_of: datetime
) -> dict[str, AudienceObservation]:
    """Return complete 24h audience observations keyed by publication id."""
    if as_of.utcoffset() is None:
        raise ValueError("audience cutoff must be timezone-aware")
    cutoff = as_of.astimezone(timezone.utc)
    account = str(account).strip().rstrip("/")
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    grouped: dict[str, dict[str, ChannelEvidence]] = {}
    for row in store.as_of(cutoff):
        if not (
            row.channel == "website"
            and row.account.rstrip("/") == account
            and row.value is not None
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == MATURE_WINDOW
        ):
            continue
        metric_key = None
        if row.metric == "http_pageviews" and row.source == PAGEVIEW_SOURCE:
            metric_key = "pageviews"
        elif row.source == ENGAGEMENT_SOURCE and row.metric in {
            "tracked_pageviews", "engaged_readers_30s"
        }:
            metric_key = row.metric
        if metric_key is not None:
            grouped.setdefault(row.publication_id, {})[metric_key] = row

    output: dict[str, AudienceObservation] = {}
    for publication_id, metrics in grouped.items():
        if set(metrics) != {"pageviews", "tracked_pageviews", "engaged_readers_30s"}:
            continue
        pageviews = metrics["pageviews"]
        tracked = metrics["tracked_pageviews"]
        engaged = metrics["engaged_readers_30s"]
        if not (
            pageviews.window_start == tracked.window_start == engaged.window_start
            and pageviews.window_end == tracked.window_end == engaged.window_end
        ):
            continue
        rate = engagement_rate(
            engaged_readers_30s=float(engaged.value),
            tracked_pageviews=float(tracked.value),
        )
        if rate is None:
            continue
        refs: list[str] = []
        for row in (pageviews, tracked, engaged):
            refs.extend(row.evidence_refs)
        output[publication_id] = AudienceObservation(
            publication_id=publication_id,
            pageviews=float(pageviews.value),
            tracked_pageviews=float(tracked.value),
            engaged_readers_30s=float(engaged.value),
            engagement_rate=rate,
            published_at=pageviews.published_at.astimezone(timezone.utc),
            observed_at=max(
                pageviews.observed_at, tracked.observed_at, engaged.observed_at
            ).astimezone(timezone.utc),
            evidence_refs=tuple(dict.fromkeys(refs)),
        )
    return output


__all__ = [
    "AudienceObservation", "audience_reward", "collect_mature_audience_observations",
    "engagement_rate", "reach_score",
]
