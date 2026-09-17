"""Descriptive social-performance context for expression strategy only.

Outcome data may guide format/pacing choices, but it never becomes factual evidence,
causal proof, publication authority, or permission to weaken grounding constraints.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .channel_evidence import ChannelEvidenceStore
from .okx_orbit_outcomes import SOURCE

MIN_SAMPLES_PER_VARIANT = 2


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


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


__all__ = ["MIN_SAMPLES_PER_VARIANT", "okx_social_performance_context"]
