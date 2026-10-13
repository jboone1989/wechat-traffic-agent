"""Descriptive social-performance context for expression strategy only.

Outcome data may guide format/pacing choices, but it never becomes factual evidence,
causal proof, publication authority, or permission to weaken grounding constraints.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from .channel_evidence import ChannelEvidenceStore
from .okx_orbit_outcomes import SOURCE
from .x_outcomes import SOURCE as X_SOURCE

MIN_SAMPLES_PER_VARIANT = 2
# A reply costs the reader more than a repost, a repost more than a like.
X_ENGAGEMENT_WEIGHTS = {"likes": 1.0, "reposts": 2.0, "replies": 3.0}
X_AUDIENCE_BASELINE_POSTS = 30
# Short enough that applied outcome ids stay inside the mind's recent-event dedup window:
# if the worker is down longer, an outcome is skipped rather than counted twice.
X_AUDIENCE_LOOKBACK = timedelta(hours=24)
MAX_TOPIC_OBSERVATIONS = 8


def okx_social_performance_context(*, state_root: Path, as_of: datetime) -> dict[str, Any]:
    if as_of.utcoffset() is None:
        raise ValueError("social performance cutoff must be timezone-aware")
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    grouped: dict[str, dict[str, list[float]]] = {}
    refs: dict[str, set[str]] = {}
    for row in store.as_of(as_of):
        if not (
            row.channel == "okx-orbit"
            and row.source == SOURCE
            and row.value is not None
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == timedelta(hours=24)
            and row.metric in {"views", "likes", "comments", "quotes"}
        ):
            continue
        metrics = grouped.setdefault(row.structure_version, {})
        metrics.setdefault(row.metric, []).append(float(row.value))
        refs.setdefault(row.structure_version, set()).update(row.evidence_refs)

    variants = []
    for structure_version, metrics in sorted(grouped.items()):
        sample_count = len(metrics.get("views", []))
        variants.append({
            "structure_version": structure_version,
            "samples": sample_count,
            "eligible_for_style_guidance": sample_count >= MIN_SAMPLES_PER_VARIANT,
            "mean_views_24h": _mean(metrics.get("views", [])),
            "mean_likes_24h": _mean(metrics.get("likes", [])),
            "mean_comments_24h": _mean(metrics.get("comments", [])),
            "mean_quotes_24h": _mean(metrics.get("quotes", [])),
            "evidence_refs": sorted(refs.get(structure_version, set())),
        })
    eligible = [row for row in variants if row["eligible_for_style_guidance"]]
    return {
        "schema_version": "okx-social-performance-context.v1",
        "status": "DESCRIPTIVE_ONLY" if eligible else "INSUFFICIENT_SAMPLES",
        "as_of": as_of.isoformat(),
        "min_samples_per_variant": MIN_SAMPLES_PER_VARIANT,
        "variants": variants,
        "generation_authority": "STYLE_ONLY" if eligible else "NONE",
        "channel_scope": "OKX_ONLY",
        "factual_authority": False,
        "evidence_gate_authority": False,
        "publication_authority": False,
        "causal_claim": False,
        "limitations": [
            "Observed engagement differences do not establish causal writing improvement.",
            "Use only to choose among evidence-compatible expression forms; never alter factual certainty.",
            "Unknown or missed outcome windows stay unknown and are not treated as zero.",
        ],
    }


def x_social_performance_context(*, state_root: Path, as_of: datetime) -> dict[str, Any]:
    if as_of.utcoffset() is None:
        raise ValueError("social performance cutoff must be timezone-aware")
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    grouped: dict[str, dict[str, list[float]]] = {}
    refs: dict[str, set[str]] = {}
    for row in store.as_of(as_of):
        if not (
            row.channel == "x"
            and row.source == X_SOURCE
            and row.value is not None
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == timedelta(hours=24)
            and row.metric in {"views", "likes", "replies", "reposts"}
        ):
            continue
        metrics = grouped.setdefault(row.structure_version, {})
        metrics.setdefault(row.metric, []).append(float(row.value))
        refs.setdefault(row.structure_version, set()).update(row.evidence_refs)

    variants = []
    for structure_version, metrics in sorted(grouped.items()):
        sample_count = len(metrics.get("views", []))
        variants.append({
            "structure_version": structure_version,
            "samples": sample_count,
            "eligible_for_style_guidance": sample_count >= MIN_SAMPLES_PER_VARIANT,
            "mean_views_24h": _mean(metrics.get("views", [])),
            "mean_likes_24h": _mean(metrics.get("likes", [])),
            "mean_replies_24h": _mean(metrics.get("replies", [])),
            "mean_reposts_24h": _mean(metrics.get("reposts", [])),
            "evidence_refs": sorted(refs.get(structure_version, set())),
        })
    eligible = [row for row in variants if row["eligible_for_style_guidance"]]
    topic_variants = _x_topic_variants(_x_24h_publications(state_root=state_root, as_of=as_of))
    return {
        "schema_version": "x-social-performance-context.v1",
        "status": "DESCRIPTIVE_ONLY" if eligible else "INSUFFICIENT_SAMPLES",
        "as_of": as_of.isoformat(),
        "min_samples_per_variant": MIN_SAMPLES_PER_VARIANT,
        "variants": variants,
        "topic_variants": topic_variants,
        "sample_count": sum(row["samples"] for row in variants),
        "observations": _x_observations(variants, topic_variants),
        "generation_authority": "STYLE_ONLY" if eligible else "NONE",
        "channel_scope": "X_ONLY",
        "factual_authority": False,
        "evidence_gate_authority": False,
        "publication_authority": False,
        "causal_claim": False,
        "limitations": [
            "Observed engagement differences do not establish causal writing improvement.",
            "Use only to choose among evidence-compatible X expression forms; never alter factual certainty.",
            "Unknown or missed outcome windows stay unknown and are not treated as zero.",
        ],
    }


def x_engagement_score(metrics: dict[str, float | None]) -> float | None:
    """Weighted public engagement; unknown when any counter is unavailable."""
    if any(metrics.get(name) is None for name in X_ENGAGEMENT_WEIGHTS):
        return None
    return sum(weight * float(metrics[name]) for name, weight in X_ENGAGEMENT_WEIGHTS.items())


def _x_24h_publications(*, state_root: Path, as_of: datetime) -> list[dict[str, Any]]:
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    grouped: dict[str, dict[str, Any]] = {}
    for row in store.as_of(as_of):
        if not (
            row.channel == "x"
            and row.source == X_SOURCE
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == timedelta(hours=24)
            and row.metric in {"views", "likes", "replies", "reposts"}
        ):
            continue
        item = grouped.setdefault(row.publication_id, {
            "publication_id": row.publication_id, "account": row.account, "topic": row.topic,
            "observed_at": row.observed_at, "metrics": {}, "evidence_refs": set(),
        })
        item["metrics"][row.metric] = row.value
        item["observed_at"] = max(item["observed_at"], row.observed_at)
        item["evidence_refs"].update(row.evidence_refs)
    rows = []
    for item in grouped.values():
        metrics = {name: item["metrics"].get(name) for name in ("views", "likes", "replies", "reposts")}
        rows.append({**item, "metrics": metrics, "engagement_score": x_engagement_score(metrics),
                     "evidence_refs": sorted(item["evidence_refs"])})
    return sorted(rows, key=lambda row: (row["observed_at"], row["publication_id"]))


def _x_topic_variants(publications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in publications:
        grouped.setdefault(row["topic"], []).append(row)
    variants = []
    for topic, rows in sorted(grouped.items()):
        scores = [row["engagement_score"] for row in rows if row["engagement_score"] is not None]
        views = [float(row["metrics"]["views"]) for row in rows if row["metrics"]["views"] is not None]
        variants.append({
            "topic": topic,
            "samples": len(scores),
            "eligible_for_topic_guidance": len(scores) >= MIN_SAMPLES_PER_VARIANT,
            "mean_engagement_score_24h": _mean(scores),
            "mean_views_24h": _mean(views),
        })
    return variants


def _x_observations(variants: list[dict[str, Any]], topic_variants: list[dict[str, Any]]) -> list[str]:
    """Compact descriptive lines that survive the L1 decision-context projection."""
    lines = [
        f"format {row['structure_version']}: {row['samples']} posts, "
        f"mean 24h views {_fmt(row['mean_views_24h'])}, likes {_fmt(row['mean_likes_24h'])}, "
        f"replies {_fmt(row['mean_replies_24h'])}"
        for row in variants if row["eligible_for_style_guidance"]
    ]
    ranked = sorted(
        (row for row in topic_variants if row["eligible_for_topic_guidance"]),
        key=lambda row: (-(row["mean_engagement_score_24h"] or 0.0), row["topic"]),
    )[:MAX_TOPIC_OBSERVATIONS]
    lines.extend(
        f"topic {row['topic']}: {row['samples']} posts, "
        f"mean 24h engagement score {_fmt(row['mean_engagement_score_24h'])}, views {_fmt(row['mean_views_24h'])}"
        for row in ranked
    )
    return lines


def x_mature_audience_outcomes(
    *, state_root: Path, as_of: datetime, lookback: timedelta = X_AUDIENCE_LOOKBACK,
) -> list[dict[str, Any]]:
    """Recently matured 24h X outcomes, each scored against the account's own earlier posts."""
    if as_of.utcoffset() is None:
        raise ValueError("audience outcome cutoff must be timezone-aware")
    prior_scores: dict[str, list[float]] = {}
    outcomes = []
    for row in _x_24h_publications(state_root=state_root, as_of=as_of):
        score = row["engagement_score"]
        if score is None:
            continue
        history = prior_scores.setdefault(row["account"], [])
        baseline = float(median(history[-X_AUDIENCE_BASELINE_POSTS:])) if history else None
        if row["observed_at"] >= as_of - lookback:
            outcomes.append({
                "outcome_id": f"x-audience-24h:{row['publication_id']}",
                "publication_id": row["publication_id"], "account": row["account"],
                "topic": row["topic"], "metrics": row["metrics"],
                "engagement_score": score, "baseline_engagement_score": baseline,
                "observed_at": row["observed_at"].isoformat(),
                "evidence_refs": row["evidence_refs"],
            })
        history.append(score)
    return outcomes


def _fmt(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


__all__ = [
    "MIN_SAMPLES_PER_VARIANT", "X_ENGAGEMENT_WEIGHTS", "okx_social_performance_context",
    "x_engagement_score", "x_mature_audience_outcomes", "x_social_performance_context",
]
