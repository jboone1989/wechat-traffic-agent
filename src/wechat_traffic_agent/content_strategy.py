"""Prospective Website content-strategy binding and outcome-guided selection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .channel_evidence import ChannelEvidenceStore
from .content_admission import _write_once
from .traffic_learning import TopicTrafficSignal

SCHEMA = "website-content-strategy.v1"
MIN_SAMPLES = 2
EXPLORATION_PERCENT = 25


class StrategyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    reader_intent: Literal["learn", "evaluate", "compare", "troubleshoot", "update"]
    angle: Literal["mechanism", "evidence", "tradeoffs", "practical", "update", "map"]
    title_pattern: Literal["question", "direct", "evidence", "comparison", "update"]
    structure_pattern: Literal[
        "answer-mechanism-evidence", "evidence-interpretation-limitations",
        "comparison-tradeoffs", "problem-steps-checks", "what-changed-why-next",
        "overview-subtopics-links",
    ]
    coverage_action: Literal["standalone", "pillar", "support", "follow-up"]


PROFILES = (
    StrategyProfile(name="explainer-question", reader_intent="learn", angle="mechanism",
                    title_pattern="question", structure_pattern="answer-mechanism-evidence",
                    coverage_action="support"),
    StrategyProfile(name="evidence-brief", reader_intent="evaluate", angle="evidence",
                    title_pattern="evidence", structure_pattern="evidence-interpretation-limitations",
                    coverage_action="support"),
    StrategyProfile(name="comparison-guide", reader_intent="compare", angle="tradeoffs",
                    title_pattern="comparison", structure_pattern="comparison-tradeoffs",
                    coverage_action="standalone"),
    StrategyProfile(name="practical-guide", reader_intent="troubleshoot", angle="practical",
                    title_pattern="direct", structure_pattern="problem-steps-checks",
                    coverage_action="support"),
    StrategyProfile(name="follow-up-update", reader_intent="update", angle="update",
                    title_pattern="update", structure_pattern="what-changed-why-next",
                    coverage_action="follow-up"),
    StrategyProfile(name="topic-pillar", reader_intent="learn", angle="map",
                    title_pattern="direct", structure_pattern="overview-subtopics-links",
                    coverage_action="pillar"),
)
PROFILE_BY_NAME = {profile.name: profile for profile in PROFILES}


class ContentStrategyBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-content-strategy.v1"] = SCHEMA
    strategy_id: str = Field(pattern=r"^strategy:[0-9a-f]{24}$")
    cycle: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    decided_at: datetime
    profile: StrategyProfile
    opening_mode: Literal["reader_question_first", "evidence_first"] | None = None
    selection_mode: Literal["TRAFFIC_HEURISTIC", "EXPLORE", "EXPLOIT"]
    decision_reason: str = Field(min_length=1, max_length=500)
    samples_by_profile: dict[str, int]
    mean_pageviews_24h_by_profile: dict[str, float | None]
    generation_authority: Literal[True] = True
    publication_authority: Literal[False] = False
    causal_claim: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("strategy decision timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def identity_matches(self):
        expected = _strategy_id(topic=self.topic, profile=self.profile, opening_mode=self.opening_mode)
        if self.strategy_id != expected:
            raise ValueError("content strategy identity mismatch")
        return self


def _strategy_id(*, topic: str, profile: StrategyProfile, opening_mode: str | None) -> str:
    payload = json.dumps({
        "schema": SCHEMA, "topic": topic, "profile": profile.model_dump(mode="json"),
        "opening_mode": opening_mode,
    }, sort_keys=True, separators=(",", ":"))
    return "strategy:" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def strategy_instruction(binding: ContentStrategyBinding) -> str:
    p = binding.profile
    return (
        f"Content strategy: reader_intent={p.reader_intent}; angle={p.angle}; "
        f"title_pattern={p.title_pattern}; structure={p.structure_pattern}; "
        f"coverage_action={p.coverage_action}. Apply this as writing strategy only; "
        "it is not factual evidence and cannot relax grounding, counter-evidence, or safety checks."
    )


def _historical_pageviews(*, state_root: Path, account: str, topic: str, as_of: datetime) -> dict[str, list[float]]:
    publications = {}
    root = Path(state_root) / "content" / "continuous" / "cycles"
    for path in sorted(root.glob("*/publication.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        if isinstance(strategy, dict) and isinstance(strategy.get("strategy_id"), str):
            publications[str(raw.get("revision"))] = strategy["strategy_id"]
    grouped: dict[str, list[float]] = {profile.name: [] for profile in PROFILES}
    strategy_to_profile = {}
    for path in sorted(root.glob("*/publication.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        if isinstance(strategy, dict) and strategy.get("topic") == topic:
            profile = strategy.get("profile")
            if isinstance(profile, dict) and profile.get("name") in grouped:
                strategy_to_profile[str(raw.get("revision"))] = profile["name"]
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    for row in store.as_of(as_of):
        if not (
            row.channel == "website" and row.account.rstrip("/") == account.rstrip("/")
            and row.metric == "http_pageviews" and row.source == "nginx-access-log:v1"
            and row.value is not None and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == timedelta(hours=24)
        ):
            continue
        profile_name = strategy_to_profile.get(row.publication_id)
        if profile_name:
            grouped[profile_name].append(float(row.value))
    return grouped


def _traffic_profile(signal: TopicTrafficSignal) -> StrategyProfile | None:
    if signal.exposure_status == "NO_CLICKS" and signal.impressions > 0:
        return PROFILE_BY_NAME["explainer-question"]
    if signal.exposure_status == "HAS_CLICKS":
        return PROFILE_BY_NAME["follow-up-update"]
    if signal.article_count <= 1 or signal.orphan_article_count == signal.article_count:
        return PROFILE_BY_NAME["topic-pillar"]
    return None


def choose_content_strategy(*, state_root: Path, account: str, cycle: str, topic: str,
                            traffic_signal: TopicTrafficSignal, as_of: datetime,
                            opening_mode: str | None = None) -> ContentStrategyBinding:
    if as_of.utcoffset() is None:
        raise ValueError("strategy cutoff must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    history = _historical_pageviews(state_root=state_root, account=account, topic=topic, as_of=as_of)
    counts = {name: len(values) for name, values in history.items()}
    means = {name: (sum(values) / len(values) if values else None) for name, values in history.items()}
    heuristic = _traffic_profile(traffic_signal)
    if heuristic is not None and counts[heuristic.name] < MIN_SAMPLES:
        profile = heuristic
        mode = "TRAFFIC_HEURISTIC"
        reason = f"traffic signal {traffic_signal.exposure_status}; profile still under-sampled"
    else:
        under = [profile for profile in PROFILES if counts[profile.name] < MIN_SAMPLES]
        seed = int(hashlib.sha256(f"{cycle}|{topic}".encode()).hexdigest()[:8], 16)
        if under:
            profile = under[seed % len(under)]
            mode = "EXPLORE"
            reason = f"{counts[profile.name]} mature 24h samples < {MIN_SAMPLES}"
        else:
            explore = seed % 100 < EXPLORATION_PERCENT
            if explore:
                profile = PROFILES[seed % len(PROFILES)]
                mode = "EXPLORE"
                reason = f"deterministic {EXPLORATION_PERCENT}% exploration"
            else:
                profile = max(PROFILES, key=lambda item: (means[item.name] or 0.0, -PROFILES.index(item)))
                mode = "EXPLOIT"
                reason = "highest associated mean 24h pageviews; observational, not causal"
    return ContentStrategyBinding(
        strategy_id=_strategy_id(topic=topic, profile=profile, opening_mode=opening_mode),
        cycle=cycle,
        topic=topic,
        decided_at=as_of,
        profile=profile,
        opening_mode=opening_mode,
        selection_mode=mode,
        decision_reason=reason,
        samples_by_profile=counts,
        mean_pageviews_24h_by_profile=means,
    )


def bind_content_strategy(path: Path, *, state_root: Path, account: str, cycle: str,
                          topic: str, traffic_signal: TopicTrafficSignal, decided_at: datetime,
                          opening_mode: str | None = None) -> ContentStrategyBinding:
    path = Path(path)
    if path.exists():
        saved = ContentStrategyBinding.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.cycle != cycle or saved.topic != topic or saved.opening_mode != opening_mode:
            raise ValueError("persisted content strategy disagrees with prospective context")
        return saved
    binding = choose_content_strategy(
        state_root=state_root, account=account, cycle=cycle, topic=topic,
        traffic_signal=traffic_signal, as_of=decided_at, opening_mode=opening_mode,
    )
    _write_once(path, binding.model_dump_json(indent=2) + "\n")
    return binding


__all__ = [
    "ContentStrategyBinding", "StrategyProfile", "PROFILES", "bind_content_strategy",
    "choose_content_strategy", "strategy_instruction",
]
