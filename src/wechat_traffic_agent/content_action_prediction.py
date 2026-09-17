"""Prospective predictions for Content Agent actions and their later outcomes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .channel_evidence import ChannelEvidence, ChannelEvidenceStore
from .content_admission import _write_once

SCHEMA = "content-action-prediction.v1"
WINDOWS = (("1h", 1), ("4h", 4), ("24h", 24))
WINDOW_HOURS = dict(WINDOWS)
MATURITY = {"1h": "EARLY", "4h": "PROVISIONAL", "24h": "MATURE"}
RELIABILITY = {"1h": 0.35, "4h": 0.65, "24h": 1.0}
ERROR_INQUIRY_THRESHOLD = {"1h": 0.55, "4h": 0.40, "24h": 0.25}
PROFILE_NAMES = (
    "explainer-question", "evidence-brief", "comparison-guide",
    "practical-guide", "follow-up-update", "topic-pillar",
)


class MetricForecast(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window: Literal["1h", "4h", "24h"]
    expected_pageviews: float = Field(ge=0.0)
    lower_bound: float = Field(ge=0.0)
    upper_bound: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_mode: Literal[
        "TOPIC_PROFILE", "TOPIC", "PROFILE_GLOBAL", "GLOBAL",
        "SCALED_24H", "WEAK_PRIOR",
    ]
    sample_count: int = Field(ge=0)
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bounds_contain_expectation(self):
        if not self.lower_bound <= self.expected_pageviews <= self.upper_bound:
            raise ValueError("forecast bounds must contain expected_pageviews")
        return self


class StrategyCandidateForecast(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_name: str
    metrics: tuple[MetricForecast, ...] = Field(min_length=3, max_length=3)
    expected_utility: float = Field(ge=0.0, le=1.0)
    information_gain: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def complete_windows(self):
        if tuple(item.window for item in self.metrics) != tuple(label for label, _ in WINDOWS):
            raise ValueError("candidate forecast must contain 1h, 4h, and 24h in order")
        return self

    @property
    def mature(self) -> MetricForecast:
        return self.metrics[-1]


class ContentActionPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA] = SCHEMA
    prediction_id: str = Field(pattern=r"^content-action-prediction:[0-9a-f]{24}$")
    cycle: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=200)
    strategy_id: str = Field(min_length=1, max_length=120)
    selected_profile_name: str = Field(min_length=1, max_length=80)
    created_at: datetime
    candidates: tuple[StrategyCandidateForecast, ...] = Field(min_length=6, max_length=6)
    decision_rule: Literal["EXPECTED_UTILITY_PLUS_INFORMATION_GAIN"] = "EXPECTED_UTILITY_PLUS_INFORMATION_GAIN"
    publication_authority: Literal[False] = False
    causal_claim: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("action prediction timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_identity(self):
        names = tuple(item.profile_name for item in self.candidates)
        if names != PROFILE_NAMES:
            raise ValueError("action prediction must contain canonical strategy profiles in order")
        if self.selected_profile_name not in names:
            raise ValueError("selected profile must exist in candidate forecasts")
        expected = _prediction_id(
            cycle=self.cycle, topic=self.topic, strategy_id=self.strategy_id,
            selected_profile_name=self.selected_profile_name,
        )
        if self.prediction_id != expected:
            raise ValueError("action prediction identity mismatch")
        return self

    @property
    def selected(self) -> StrategyCandidateForecast:
        return next(item for item in self.candidates if item.profile_name == self.selected_profile_name)


class ActionPredictionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prediction_id: str
    publication_id: str
    topic: str
    profile_name: str
    window: Literal["1h", "4h", "24h"]
    maturity: Literal["EARLY", "PROVISIONAL", "MATURE"]
    expected_pageviews: float = Field(ge=0.0)
    actual_pageviews: float = Field(ge=0.0)
    prediction_error: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    observed_at: datetime
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("observed_at")
    @classmethod
    def aware_outcome(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("action outcome timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)


def _prediction_id(*, cycle: str, topic: str, strategy_id: str, selected_profile_name: str) -> str:
    identity = json.dumps({
        "cycle": cycle, "topic": topic, "strategy_id": strategy_id,
        "selected_profile_name": selected_profile_name,
    }, sort_keys=True, separators=(",", ":"))
    return "content-action-prediction:" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _publication_strategy_index(state_root: Path) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    root = Path(state_root) / "content" / "continuous" / "cycles"
    for path in sorted(root.glob("*/publication.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        profile = strategy.get("profile") if isinstance(strategy, dict) else None
        revision = str(raw.get("revision") or "")
        if revision and isinstance(strategy, dict) and isinstance(profile, dict):
            name = str(profile.get("name") or "")
            topic = str(strategy.get("topic") or "")
            if name in PROFILE_NAMES and topic:
                index[revision] = {"topic": topic, "profile_name": name}
    return index


def _window_label(row: ChannelEvidence) -> str | None:
    delta = row.window_end - row.window_start
    for label, hours in WINDOWS:
        if delta == timedelta(hours=hours):
            return label
    return None


def _samples(*, state_root: Path, account: str, as_of: datetime) -> list[dict[str, Any]]:
    bindings = _publication_strategy_index(state_root)
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    rows: list[dict[str, Any]] = []
    for row in store.as_of(as_of):
        label = _window_label(row)
        binding = bindings.get(row.publication_id)
        if not (
            label and binding and row.channel == "website"
            and row.account.rstrip("/") == account.rstrip("/")
            and row.metric == "http_pageviews" and row.source == "nginx-access-log:v1"
            and row.value is not None
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.topic == binding["topic"]
        ):
            continue
        rows.append({
            "topic": binding["topic"], "profile_name": binding["profile_name"],
            "window": label, "value": float(row.value),
            "evidence_refs": tuple(row.evidence_refs),
        })
    return rows


def _matching(rows: list[dict[str, Any]], *, topic: str | None = None,
              profile: str | None = None, window: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["window"] == window
            and (topic is None or row["topic"] == topic)
            and (profile is None or row["profile_name"] == profile)]


def _estimate(rows: list[dict[str, Any]], *, topic: str, profile: str, window: str) -> MetricForecast:
    tiers = (
        ("TOPIC_PROFILE", _matching(rows, topic=topic, profile=profile, window=window), 0.45),
        ("TOPIC", _matching(rows, topic=topic, window=window), 0.35),
        ("PROFILE_GLOBAL", _matching(rows, profile=profile, window=window), 0.30),
        ("GLOBAL", _matching(rows, window=window), 0.25),
    )
    mode = "WEAK_PRIOR"
    chosen: list[dict[str, Any]] = []
    base_confidence = 0.10
    scale = 1.0
    for candidate_mode, values, confidence in tiers:
        if values:
            mode, chosen, base_confidence = candidate_mode, values, confidence
            break
    if not chosen and window != "24h":
        for _candidate_mode, values, _confidence in (
            ("TOPIC_PROFILE", _matching(rows, topic=topic, profile=profile, window="24h"), 0.0),
            ("TOPIC", _matching(rows, topic=topic, window="24h"), 0.0),
            ("PROFILE_GLOBAL", _matching(rows, profile=profile, window="24h"), 0.0),
            ("GLOBAL", _matching(rows, window="24h"), 0.0),
        ):
            if values:
                mode, chosen, base_confidence = "SCALED_24H", values, 0.18
                scale = WINDOW_HOURS[window] / 24.0
                break
    values = [float(row["value"]) * scale for row in chosen]
    expected = float(median(values)) if values else (WINDOW_HOURS[window] / 24.0)
    deviations = [abs(value - expected) for value in values]
    mad = float(median(deviations)) if deviations else 0.0
    if values and len(values) >= 2:
        width = max(1.0, 2.0 * mad)
    elif values:
        width = max(2.0, expected)
    else:
        width = max(2.0, expected * 3.0)
    confidence = min(0.85, base_confidence + 0.08 * min(len(values), 5))
    refs: list[str] = []
    for row in chosen:
        refs.extend(row["evidence_refs"])
    return MetricForecast(
        window=window, expected_pageviews=expected,
        lower_bound=max(0.0, expected - width), upper_bound=expected + width,
        confidence=confidence, evidence_mode=mode, sample_count=len(values),
        evidence_refs=tuple(dict.fromkeys(refs)),
    )


def forecast_strategy_candidates(*, state_root: Path, account: str, topic: str,
                                 as_of: datetime,
                                 mind_strategy_context: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    if as_of.utcoffset() is None:
        raise ValueError("forecast cutoff must be timezone-aware")
    rows = _samples(state_root=state_root, account=account, as_of=as_of.astimezone(timezone.utc))
    global_24h = [float(row["value"]) for row in rows if row["window"] == "24h"]
    global_baseline = float(median(global_24h)) if global_24h else 1.0
    mind_rewards: Mapping[str, Any] = {}
    mind_counts: Mapping[str, Any] = {}
    if mind_strategy_context is not None:
        if str(mind_strategy_context.get("topic") or "") != topic:
            raise ValueError("VirtualBrain forecast context topic mismatch")
        raw_rewards = mind_strategy_context.get("mean_relative_reward_by_profile") or {}
        raw_counts = mind_strategy_context.get("samples_by_profile") or {}
        if isinstance(raw_rewards, Mapping):
            mind_rewards = raw_rewards
        if isinstance(raw_counts, Mapping):
            mind_counts = raw_counts
    output: dict[str, dict[str, Any]] = {}
    for profile in PROFILE_NAMES:
        metrics = tuple(_estimate(rows, topic=topic, profile=profile, window=label) for label, _ in WINDOWS)
        mature = metrics[-1]
        traffic_utility = mature.expected_pageviews / (
            mature.expected_pageviews + global_baseline + 1.0
        )
        mind_reward = mind_rewards.get(profile)
        mind_count = max(0, int(mind_counts.get(profile, 0) or 0))
        if isinstance(mind_reward, (int, float)) and not isinstance(mind_reward, bool) and mind_count > 0:
            belief_weight = min(0.65, 0.20 + 0.15 * min(mind_count, 3))
            expected_utility = (1.0 - belief_weight) * traffic_utility + belief_weight * float(mind_reward)
        else:
            expected_utility = traffic_utility
        information_gain = min(
            1.0,
            0.70 * (1.0 - mature.confidence) + 0.30 / (1.0 + mature.sample_count),
        )
        candidate = StrategyCandidateForecast(
            profile_name=profile, metrics=metrics,
            expected_utility=expected_utility, information_gain=information_gain,
        )
        output[profile] = candidate.model_dump(mode="json")
    return output


def bind_action_prediction(path: Path, *, cycle: str, topic: str, strategy_id: str,
                           selected_profile_name: str, created_at: datetime,
                           candidate_forecasts: Mapping[str, Any]) -> ContentActionPrediction:
    candidates = tuple(
        StrategyCandidateForecast.model_validate(candidate_forecasts[name]) for name in PROFILE_NAMES
    )
    row = ContentActionPrediction(
        prediction_id=_prediction_id(
            cycle=cycle, topic=topic, strategy_id=strategy_id,
            selected_profile_name=selected_profile_name,
        ),
        cycle=cycle, topic=topic, strategy_id=strategy_id,
        selected_profile_name=selected_profile_name, created_at=created_at,
        candidates=candidates,
    )
    path = Path(path)
    if path.exists():
        existing = ContentActionPrediction.model_validate_json(path.read_text(encoding="utf-8"))
        if existing.model_dump(mode="json") != row.model_dump(mode="json"):
            raise ValueError("persisted action prediction disagrees with prospective decision")
        return existing
    _write_once(path, row.model_dump_json(indent=2) + "\n")
    return row


def _symmetric_error(expected: float, actual: float) -> float:
    return min(1.0, abs(float(actual) - float(expected)) / (float(actual) + float(expected) + 1.0))


def settle_action_predictions(*, state_root: Path, account: str, as_of: datetime,
                              mind: Any | None = None) -> dict[str, Any]:
    if as_of.utcoffset() is None:
        raise ValueError("settlement cutoff must be timezone-aware")
    cutoff = as_of.astimezone(timezone.utc)
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    evidence: dict[tuple[str, str], ChannelEvidence] = {}
    for row in store.as_of(cutoff):
        label = _window_label(row)
        if not (
            label and row.channel == "website" and row.account.rstrip("/") == account.rstrip("/")
            and row.metric == "http_pageviews" and row.source == "nginx-access-log:v1"
            and row.value is not None and row.agent_candidate_sha256 == row.published_content_sha256
        ):
            continue
        evidence[(row.publication_id, label)] = row

    outcomes: list[dict[str, Any]] = []
    newly_recorded: list[dict[str, Any]] = []
    root = Path(state_root) / "content" / "continuous" / "cycles"
    for prediction_path in sorted(root.glob("*/action_prediction.json")):
        cycle_root = prediction_path.parent
        publication_path = cycle_root / "publication.json"
        if not publication_path.exists():
            continue
        prediction = ContentActionPrediction.model_validate_json(prediction_path.read_text(encoding="utf-8"))
        publication = json.loads(publication_path.read_text(encoding="utf-8"))
        attr = publication.get("outcome_attribution") if isinstance(publication, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        if not isinstance(strategy, dict):
            continue
        publication_id = str(publication.get("revision") or "")
        published_at = datetime.fromisoformat(str(attr.get("published_at")))
        if published_at.utcoffset() is None or prediction.created_at > published_at.astimezone(timezone.utc):
            raise ValueError("action prediction was not prospective to publication")
        if strategy.get("strategy_id") != prediction.strategy_id or strategy.get("topic") != prediction.topic:
            raise ValueError("action prediction does not match executed content strategy")
        selected = prediction.selected
        for metric in selected.metrics:
            row = evidence.get((publication_id, metric.window))
            if row is None:
                continue
            outcome = ActionPredictionOutcome(
                prediction_id=prediction.prediction_id, publication_id=publication_id,
                topic=prediction.topic, profile_name=prediction.selected_profile_name,
                window=metric.window, maturity=MATURITY[metric.window],
                expected_pageviews=metric.expected_pageviews, actual_pageviews=float(row.value),
                prediction_error=_symmetric_error(metric.expected_pageviews, float(row.value)),
                reliability=RELIABILITY[metric.window], observed_at=row.observed_at,
                evidence_refs=tuple(dict.fromkeys([prediction.prediction_id, *row.evidence_refs])),
            )
            outcomes.append(outcome.model_dump(mode="json"))
            outcome_path = cycle_root / "action_prediction_outcomes" / f"{metric.window}.json"
            if not outcome_path.exists():
                _write_once(outcome_path, outcome.model_dump_json(indent=2) + "\n")
                newly_recorded.append(outcome.model_dump(mode="json"))

    mind_result = None
    if mind is not None and hasattr(mind, "observe_action_prediction_outcomes"):
        mind_result = mind.observe_action_prediction_outcomes(outcomes)
    return {
        "status": "SETTLED" if newly_recorded else "NO_NEW_OUTCOMES",
        "recorded": newly_recorded, "settled_count": len(outcomes),
        "mind": mind_result, "publication_authority": False,
    }


__all__ = [
    "ActionPredictionOutcome", "ContentActionPrediction", "ERROR_INQUIRY_THRESHOLD",
    "MetricForecast", "PROFILE_NAMES", "StrategyCandidateForecast", "WINDOWS",
    "bind_action_prediction", "forecast_strategy_candidates", "settle_action_predictions",
]
