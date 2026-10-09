"""Causal historical replay for Content predictions.

Only publications that already carried a prospective strategy binding at decision
time and later received attributable mature 24h pageviews are replayable. Legacy
publications without that binding stay excluded rather than being hindsight-labeled.
"""
from __future__ import annotations
from wechat_traffic_agent.publication.cycle_records import NAMES, read_cycle_record, write_cycle_record, cycle_record_exists, publication_paths

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from virtualbrain.research.predictive_learning import PredictiveLearningEngine, PredictionAssessment

from .channel_evidence import ChannelEvidenceStore
from .content_action_prediction import forecast_strategy_candidates

TARGET = "website:http_pageviews:24h"


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("historical replay timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _metric(prediction: Mapping[str, Any], observed: float) -> PredictionAssessment:
    expected = float(prediction["expected"]); actual = float(observed)
    denom = actual + expected + 1.0
    signed = max(-1.0, min(1.0, (actual - expected) / denom))
    return PredictionAssessment(min(1.0, abs(actual - expected) / denom), signed)


def _replay_context(engine: PredictiveLearningEngine, *, topic: str) -> dict[str, Any]:
    context = engine.calibration_context(domain="content", target=TARGET)
    by_profile = {}
    for row in context["calibration"]:
        by_profile[str(row["action_key"])] = {
            "mature_samples": int(row.get("prospective_count", 0)),
            "prospective_samples": int(row.get("prospective_count", 0)),
            "historical_prior_samples": int(row.get("historical_prior_count", 0)),
            "effective_learning_samples": float(row.get("effective_learning_samples", 0.0)),
            "mean_error": float(row.get("mean_error", 0.0)),
            "mean_signed_error": float(row.get("mean_signed_error", 0.0)),
            "mean_reliability": float(row.get("mean_reliability", 0.0)),
        }
    return {
        "topic": topic, "samples_by_profile": {}, "mean_relative_reward_by_profile": {},
        "prediction_calibration_by_profile": by_profile, "evidence_refs": [],
    }


def build_historical_content_priors(*, state_root: Path, account: str, as_of: datetime) -> dict[str, Any]:
    if as_of.utcoffset() is None:
        raise ValueError("historical replay cutoff must be timezone-aware")
    state_root = Path(state_root)
    publications: dict[str, dict[str, Any]] = {}
    for path in publication_paths((state_root / "content/continuous/cycles")):
        # A cycle that already had a true prospective action prediction must never
        # be replayed as historical prior; that would count the same outcome twice.
        if (path.parent / "action_prediction.json").exists():
            continue
        raw = read_cycle_record(path)
        attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        if not isinstance(strategy, dict) or not isinstance(strategy.get("profile"), dict):
            continue
        revision = str(raw.get("revision") or "").strip()
        decided = strategy.get("decided_at"); published = attr.get("published_at")
        if not revision or not decided or not published:
            continue
        decided_at, published_at = _dt(decided), _dt(published)
        if not decided_at <= published_at:
            raise ValueError("historical strategy decision must not follow publication")
        publications[revision] = {
            "cycle": path.parent.name, "publication_id": revision,
            "topic": str(attr.get("topic") or strategy.get("topic") or ""),
            "profile_name": str(strategy["profile"].get("name") or ""),
            "strategy_id": str(strategy.get("strategy_id") or ""),
            "decided_at": decided_at, "published_at": published_at,
        }

    mature = []
    store = ChannelEvidenceStore(state_root / "content/channel_evidence")
    for row in store.as_of(as_of.astimezone(timezone.utc)):
        binding = publications.get(row.publication_id)
        if not binding:
            continue
        if not (
            row.channel == "website" and row.account.rstrip("/") == account.rstrip("/")
            and row.metric == "http_pageviews" and row.source == "nginx-access-log:v1"
            and row.value is not None and row.agent_candidate_sha256 == row.published_content_sha256
            and (row.window_end - row.window_start).total_seconds() == 24 * 3600
            and row.topic == binding["topic"]
        ):
            continue
        mature.append((binding, row))
    mature.sort(key=lambda item: (item[0]["decided_at"], item[0]["publication_id"]))

    replay = PredictiveLearningEngine(subject_id="content-historical-replay")
    records = []
    for binding, outcome in mature:
        cutoff = binding["decided_at"]
        context = _replay_context(replay, topic=binding["topic"])
        forecasts = forecast_strategy_candidates(
            state_root=state_root, account=account, topic=binding["topic"], as_of=cutoff,
            mind_strategy_context=context,
        )
        candidate = forecasts.get(binding["profile_name"])
        if not isinstance(candidate, Mapping):
            continue
        metric = next((row for row in candidate["metrics"] if row["window"] == "24h"), None)
        if not isinstance(metric, Mapping):
            continue
        prediction_id = f"content-historical:{binding['publication_id']}:24h"
        prediction_refs = list(dict.fromkeys([
            binding["strategy_id"], *[str(ref) for ref in metric.get("evidence_refs", ()) if str(ref).strip()]
        ]))
        prediction_refs = [ref for ref in prediction_refs if ref]
        replay.register(
            prediction_id=prediction_id, domain="content", context_key=binding["topic"],
            action_key=binding["profile_name"], target=TARGET,
            expected=float(metric["expected_pageviews"]), lower=float(metric["lower_bound"]),
            upper=float(metric["upper_bound"]), confidence=float(metric["confidence"]),
            created_at=cutoff.isoformat(), evidence_cutoff_at=cutoff.isoformat(),
            due_at=outcome.window_end.astimezone(timezone.utc).isoformat(),
            evidence_refs=prediction_refs, evidence_class="HISTORICAL_PRIOR",
        )
        settled = replay.settle(
            prediction_id=prediction_id, outcome_id=f"{prediction_id}:outcome",
            observed=float(outcome.value), observed_at=outcome.observed_at.astimezone(timezone.utc).isoformat(),
            evidence_refs=list(outcome.evidence_refs), maturity="MATURE", reliability=1.0,
            learning_authority=True, metric=_metric, inquiry=None,
        )
        records.append({
            "prediction_id": prediction_id, "outcome_id": settled["outcome_id"],
            "topic": binding["topic"], "profile_name": binding["profile_name"],
            "strategy_id": binding["strategy_id"], "created_at": cutoff.isoformat(),
            "due_at": outcome.window_end.astimezone(timezone.utc).isoformat(),
            "observed_at": outcome.observed_at.astimezone(timezone.utc).isoformat(),
            "expected": float(metric["expected_pageviews"]), "lower": float(metric["lower_bound"]),
            "upper": float(metric["upper_bound"]), "confidence": float(metric["confidence"]),
            "actual": float(outcome.value), "normalized_error": float(settled["normalized_error"]),
            "signed_error": float(settled["signed_error"]),
            "expected_utility": float(candidate["expected_utility"]),
            "information_gain": float(candidate["information_gain"]),
            "prediction_evidence_refs": prediction_refs,
            "outcome_evidence_refs": list(outcome.evidence_refs),
        })
    return {
        "status": "REPLAYED" if records else "NO_CAUSALLY_REPLAYABLE_SAMPLES",
        "evidence_class": "HISTORICAL_PRIOR", "records": records,
        "scorecard": replay.learning_scorecard(domain="content"),
        "prospective_validation_authority": False,
    }


def import_historical_content_priors(*, mind: Any, replay: Mapping[str, Any]) -> dict[str, Any]:
    engine = mind.session.predictive_learning
    applied = 0; duplicates = 0
    for row in replay.get("records", ()):
        pid = str(row["prediction_id"])
        if pid in engine.predictions:
            duplicates += 1
            continue
        engine.register(
            prediction_id=pid, domain="content", context_key=str(row["topic"]),
            action_key=str(row["profile_name"]), target=TARGET,
            expected=float(row["expected"]), lower=float(row["lower"]), upper=float(row["upper"]),
            confidence=float(row["confidence"]), created_at=str(row["created_at"]),
            evidence_cutoff_at=str(row["created_at"]), due_at=str(row["due_at"]),
            evidence_refs=list(row["prediction_evidence_refs"]), evidence_class="HISTORICAL_PRIOR",
        )
        engine.settle(
            prediction_id=pid, outcome_id=str(row["outcome_id"]), observed=float(row["actual"]),
            observed_at=str(row["observed_at"]), evidence_refs=list(row["outcome_evidence_refs"]),
            maturity="MATURE", reliability=1.0, learning_authority=True, metric=_metric, inquiry=None,
        )
        applied += 1
    if applied:
        mind._persist()
    return {
        "status": "IMPORTED" if applied else "NO_NEW_PRIORS", "applied": applied,
        "duplicates": duplicates, "prospective_validation_authority": False,
        "scorecard": engine.learning_scorecard(domain="content"),
    }


__all__ = ["TARGET", "build_historical_content_priors", "import_historical_content_priors"]
