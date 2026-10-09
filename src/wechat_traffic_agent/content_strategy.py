"""Prospective Website content-strategy binding and outcome-guided selection."""
from __future__ import annotations
from wechat_traffic_agent.publication.cycle_records import NAMES, read_cycle_record, write_cycle_record, cycle_record_exists, publication_paths

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .channel_evidence import ChannelEvidenceStore
from .content_admission import _write_once
from .memory_observation import MemoryLifeObservatory
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
    selection_mode: Literal["TRAFFIC_HEURISTIC", "CONTEXT_HEURISTIC", "EXPLORE", "EXPLOIT", "VIRTUALBRAIN_BELIEF", "MEMORY_PRIOR", "PREDICTIVE_EXPLORE", "PREDICTIVE_EXPLOIT"]
    decision_reason: str = Field(min_length=1, max_length=500)
    samples_by_profile: dict[str, int]
    mean_pageviews_24h_by_profile: dict[str, float | None]
    mind_mean_relative_reward_by_profile: dict[str, float | None] = Field(default_factory=dict)
    mind_strategy_evidence_refs: tuple[str, ...] = ()
    memory_prior_score_by_profile: dict[str, float] = Field(default_factory=dict)
    memory_prior_sample_count_by_profile: dict[str, int] = Field(default_factory=dict)
    memory_prior_memory_ids: tuple[str, ...] = ()
    memory_prior_use_id: str = ""
    memory_prior_applied: bool = False
    prediction_expected_pageviews_24h_by_profile: dict[str, float] = Field(default_factory=dict)
    prediction_information_gain_by_profile: dict[str, float] = Field(default_factory=dict)
    prediction_evidence_refs: tuple[str, ...] = ()
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


def collect_mature_strategy_outcomes(
    *, state_root: Path, account: str, as_of: datetime
) -> list[dict[str, Any]]:
    """Project attributable mature 24h Website outcomes into cognition-safe records."""
    publications: dict[str, dict[str, Any]] = {}
    root = Path(state_root) / "content" / "continuous" / "cycles"
    for path in publication_paths(root):
        raw = read_cycle_record(path)
        attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        profile = strategy.get("profile") if isinstance(strategy, dict) else None
        publication_id = str(raw.get("revision") or "")
        if (
            publication_id
            and isinstance(profile, dict)
            and profile.get("name") in PROFILE_BY_NAME
            and isinstance(strategy.get("topic"), str)
            and isinstance(strategy.get("strategy_id"), str)
        ):
            publications[publication_id] = {
                "topic": strategy["topic"],
                "profile_name": profile["name"],
                "strategy_id": strategy["strategy_id"],
                "cycle": path.parent.name,
            }
    store = ChannelEvidenceStore(Path(state_root) / "content" / "channel_evidence")
    outcomes: list[dict[str, Any]] = []
    for row in store.as_of(as_of):
        if not (
            row.channel == "website"
            and row.account.rstrip("/") == account.rstrip("/")
            and row.metric == "http_pageviews"
            and row.source == "nginx-access-log:v1"
            and row.value is not None
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == timedelta(hours=24)
        ):
            continue
        binding = publications.get(row.publication_id)
        if binding is None or row.topic != binding["topic"]:
            continue
        identity = json.dumps({
            "publication_id": row.publication_id,
            "strategy_id": binding["strategy_id"],
            "window_start": row.window_start.astimezone(timezone.utc).isoformat(),
            "window_end": row.window_end.astimezone(timezone.utc).isoformat(),
            "source": row.source,
        }, sort_keys=True, separators=(",", ":"))
        outcomes.append({
            "outcome_id": "strategy-outcome:" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            "publication_id": row.publication_id,
            "topic": binding["topic"],
            "profile_name": binding["profile_name"],
            "cycle": binding["cycle"],
            "pageviews_24h": float(row.value),
            "observed_at": row.observed_at.astimezone(timezone.utc).isoformat(),
            "evidence_refs": list(dict.fromkeys([*row.evidence_refs, binding["strategy_id"]])),
        })
    outcomes.sort(key=lambda row: (row["observed_at"], row["outcome_id"]))
    return outcomes


def _historical_pageviews(*, state_root: Path, account: str, topic: str, as_of: datetime) -> dict[str, list[float]]:
    publications = {}
    root = Path(state_root) / "content" / "continuous" / "cycles"
    for path in publication_paths(root):
        raw = read_cycle_record(path)
        attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
        strategy = attr.get("content_strategy") if isinstance(attr, dict) else None
        if isinstance(strategy, dict) and isinstance(strategy.get("strategy_id"), str):
            publications[str(raw.get("revision"))] = strategy["strategy_id"]
    grouped: dict[str, list[float]] = {profile.name: [] for profile in PROFILES}
    strategy_to_profile = {}
    for path in publication_paths(root):
        raw = read_cycle_record(path)
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
                            opening_mode: str | None = None,
                            preferred_profile_name: str | None = None,
                            mind_strategy_context: Mapping[str, Any] | None = None,
                            action_forecasts: Mapping[str, Any] | None = None,
                            record_memory_use: bool = True) -> ContentStrategyBinding:
    if as_of.utcoffset() is None:
        raise ValueError("strategy cutoff must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    history = _historical_pageviews(state_root=state_root, account=account, topic=topic, as_of=as_of)
    counts = {name: len(values) for name, values in history.items()}
    means = {name: (sum(values) / len(values) if values else None) for name, values in history.items()}
    mind_counts = {profile.name: 0 for profile in PROFILES}
    mind_rewards = {profile.name: None for profile in PROFILES}
    mind_refs: tuple[str, ...] = ()
    memory_prior_scores: dict[str, float] = {}
    memory_prior_counts: dict[str, int] = {}
    memory_prior_ids_by_profile: dict[str, tuple[str, ...]] = {}
    memory_prior_ids: tuple[str, ...] = ()
    memory_prior_use_id = ""
    memory_prior_applied = False
    if mind_strategy_context is not None:
        if str(mind_strategy_context.get("topic") or "") != topic:
            raise ValueError("VirtualBrain strategy context topic mismatch")
        raw_counts = mind_strategy_context.get("samples_by_profile") or {}
        raw_rewards = mind_strategy_context.get("mean_relative_reward_by_profile") or {}
        for candidate in PROFILES:
            value = raw_counts.get(candidate.name, 0) if isinstance(raw_counts, Mapping) else 0
            mind_counts[candidate.name] = max(0, int(value))
            score = raw_rewards.get(candidate.name) if isinstance(raw_rewards, Mapping) else None
            mind_rewards[candidate.name] = None if score is None else float(score)
        mind_refs = tuple(dict.fromkeys(
            str(ref).strip() for ref in (mind_strategy_context.get("evidence_refs") or ())
            if str(ref).strip()
        ))
        raw_prior = mind_strategy_context.get("memory_prior")
        if isinstance(raw_prior, Mapping):
            raw_profiles = raw_prior.get("profiles") if isinstance(raw_prior.get("profiles"), Mapping) else {}
            for candidate in PROFILES:
                item = raw_profiles.get(candidate.name) if isinstance(raw_profiles, Mapping) else None
                if not isinstance(item, Mapping):
                    continue
                memory_prior_scores[candidate.name] = max(0.0, min(1.0, float(item.get("score") or 0.0)))
                memory_prior_counts[candidate.name] = max(0, int(item.get("sample_count") or 0))
                memory_prior_ids_by_profile[candidate.name] = tuple(dict.fromkeys(
                    str(mid).strip() for mid in (item.get("memory_ids") or ()) if str(mid).strip()
                ))
            memory_prior_ids = tuple(dict.fromkeys(
                str(item).strip() for item in (raw_prior.get("memory_ids") or ()) if str(item).strip()
            ))
            memory_prior_use_id = str(raw_prior.get("memory_use_id") or "")
    def prior_score(name: str) -> float | None:
        if memory_prior_counts.get(name, 0) < MIN_SAMPLES:
            return None
        return memory_prior_scores.get(name)
    forecast_expected: dict[str, float] = {}
    forecast_information: dict[str, float] = {}
    forecast_utility: dict[str, float] = {}
    forecast_refs: list[str] = []
    if action_forecasts is not None:
        for candidate in PROFILES:
            raw = action_forecasts.get(candidate.name) if isinstance(action_forecasts, Mapping) else None
            if not isinstance(raw, Mapping) or raw.get("profile_name") != candidate.name:
                raise ValueError("action forecast context must cover every strategy profile")
            metrics = raw.get("metrics")
            if not isinstance(metrics, (list, tuple)) or len(metrics) != 3:
                raise ValueError("action forecast must contain 1h, 4h, and 24h metrics")
            mature = metrics[-1]
            if not isinstance(mature, Mapping) or mature.get("window") != "24h":
                raise ValueError("action forecast mature metric must be 24h")
            forecast_expected[candidate.name] = float(mature["expected_pageviews"])
            forecast_information[candidate.name] = float(raw["information_gain"])
            forecast_utility[candidate.name] = float(raw["expected_utility"])
            for metric in metrics:
                if isinstance(metric, Mapping):
                    forecast_refs.extend(str(ref) for ref in (metric.get("evidence_refs") or ()) if str(ref).strip())
    if preferred_profile_name is not None:
        if preferred_profile_name not in PROFILE_BY_NAME:
            raise ValueError("unknown preferred content strategy profile")
        profile = PROFILE_BY_NAME[preferred_profile_name]
        mode = "CONTEXT_HEURISTIC"
        reason = f"context requires {preferred_profile_name} structure"
    else:
        heuristic = _traffic_profile(traffic_signal)
    if preferred_profile_name is not None:
        pass
    else:
        seed = int(hashlib.sha256(f"{cycle}|{topic}".encode()).hexdigest()[:8], 16)
        if action_forecasts is not None:
            sample_counts = mind_counts if any(mind_counts.values()) else counts
            under = [candidate for candidate in PROFILES if sample_counts[candidate.name] < MIN_SAMPLES]
            tie = lambda item: -((PROFILES.index(item) + seed) % len(PROFILES))
            if under:
                profile = max(under, key=lambda item: (forecast_information[item.name], tie(item)))
                mode = "PREDICTIVE_EXPLORE"
                reason = (
                    "candidate forecasts were formed before action; choosing the under-sampled profile "
                    "with highest expected information gain"
                )
            elif seed % 100 < EXPLORATION_PERCENT:
                profile = max(PROFILES, key=lambda item: (forecast_information[item.name], tie(item)))
                mode = "PREDICTIVE_EXPLORE"
                reason = (
                    f"candidate forecasts are mature; retaining {EXPLORATION_PERCENT}% exploration "
                    "through highest expected information gain"
                )
            else:
                def predictive_value(item):
                    score = float(forecast_utility[item.name])
                    prior = prior_score(item.name)
                    return score + (0.12 * (prior - 0.5) if prior is not None else 0.0)
                profile = max(PROFILES, key=lambda item: (predictive_value(item), tie(item)))
                memory_prior_applied = prior_score(profile.name) is not None
                mode = "PREDICTIVE_EXPLOIT"
                reason = (
                    "candidate forecasts were formed before action; choosing highest expected utility"
                    + (" with bounded lived-memory prior" if memory_prior_applied else "")
                )
        else:
            mind_has_outcomes = any(mind_counts.values())
            mind_under = [candidate for candidate in PROFILES if mind_counts[candidate.name] < MIN_SAMPLES]
            mind_ready = (
                mind_has_outcomes
                and not mind_under
                and all(mind_rewards[candidate.name] is not None for candidate in PROFILES)
            )
            if mind_ready and seed % 100 >= EXPLORATION_PERCENT:
                def belief_value(item):
                    base = float(mind_rewards[item.name])
                    prior = prior_score(item.name)
                    return (0.8 * base + 0.2 * prior) if prior is not None else base
                profile = max(
                    PROFILES,
                    key=lambda item: (belief_value(item), -PROFILES.index(item)),
                )
                memory_prior_applied = prior_score(profile.name) is not None
                mode = "VIRTUALBRAIN_BELIEF"
                reason = (
                    "VirtualBrain audience-outcome belief has mature samples for every profile; "
                    "exploiting highest relative reward"
                    + (" with bounded lived-memory prior" if memory_prior_applied else "")
                )
            elif mind_ready:
                profile = PROFILES[seed % len(PROFILES)]
                mode = "EXPLORE"
                reason = f"VirtualBrain belief is mature; retaining deterministic {EXPLORATION_PERCENT}% exploration"
            else:
                prior_eligible = [
                    candidate for candidate in PROFILES if prior_score(candidate.name) is not None
                ]
                if len(prior_eligible) >= 2 and seed % 100 >= EXPLORATION_PERCENT:
                    profile = max(
                        prior_eligible,
                        key=lambda item: (float(prior_score(item.name)), -PROFILES.index(item)),
                    )
                    memory_prior_applied = True
                    mode = "MEMORY_PRIOR"
                    reason = (
                        "repeated lived-memory outcomes provide a bounded strategy prior; "
                        "using the highest prior score while VirtualBrain belief is still sparse"
                    )
                elif heuristic is not None and counts[heuristic.name] < MIN_SAMPLES:
                    profile = heuristic
                    mode = "TRAFFIC_HEURISTIC"
                    reason = f"traffic signal {traffic_signal.exposure_status}; profile still under-sampled"
                else:
                    under = [candidate for candidate in PROFILES if counts[candidate.name] < MIN_SAMPLES]
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
                            reason = (
                                "highest associated mean 24h pageviews; observational fallback because "
                                "no mature VirtualBrain strategy belief is available"
                            )
    selected_memory_ids = memory_prior_ids_by_profile.get(profile.name, ()) if memory_prior_applied else ()
    binding = ContentStrategyBinding(
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
        mind_mean_relative_reward_by_profile=mind_rewards if any(mind_counts.values()) else {},
        mind_strategy_evidence_refs=mind_refs,
        memory_prior_score_by_profile=memory_prior_scores,
        memory_prior_sample_count_by_profile=memory_prior_counts,
        memory_prior_memory_ids=selected_memory_ids,
        memory_prior_use_id=memory_prior_use_id,
        memory_prior_applied=memory_prior_applied,
        prediction_expected_pageviews_24h_by_profile=forecast_expected,
        prediction_information_gain_by_profile=forecast_information,
        prediction_evidence_refs=tuple(dict.fromkeys(forecast_refs)),
    )
    if record_memory_use and memory_prior_applied and selected_memory_ids:
        MemoryLifeObservatory(Path(state_root)).record_decision_use(
            query=f"select content strategy for {topic}",
            retrieved_memory_ids=memory_prior_ids,
            used_memory_ids=selected_memory_ids,
            decision_ref=binding.strategy_id,
            evidence_refs=binding.mind_strategy_evidence_refs,
            metadata={
                "cycle": cycle,
                "topic": topic,
                "profile_name": binding.profile.name,
                "selection_mode": binding.selection_mode,
                "memory_prior_use_id": memory_prior_use_id,
            },
        )
    return binding


def bind_content_strategy(path: Path, *, state_root: Path, account: str, cycle: str,
                          topic: str, traffic_signal: TopicTrafficSignal, decided_at: datetime,
                          opening_mode: str | None = None,
                          preferred_profile_name: str | None = None,
                          mind_strategy_context: Mapping[str, Any] | None = None,
                          action_forecasts: Mapping[str, Any] | None = None) -> ContentStrategyBinding:
    path = Path(path)
    if path.exists():
        saved = ContentStrategyBinding.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.cycle != cycle or saved.topic != topic or saved.opening_mode != opening_mode:
            raise ValueError("persisted content strategy disagrees with prospective context")
        if preferred_profile_name is not None and saved.profile.name != preferred_profile_name:
            raise ValueError("persisted content strategy disagrees with preferred profile")
        return saved
    binding = choose_content_strategy(
        state_root=state_root, account=account, cycle=cycle, topic=topic,
        traffic_signal=traffic_signal, as_of=decided_at, opening_mode=opening_mode,
        preferred_profile_name=preferred_profile_name,
        mind_strategy_context=mind_strategy_context, action_forecasts=action_forecasts,
    )
    _write_once(path, binding.model_dump_json(indent=2) + "\n")
    return binding


__all__ = [
    "ContentStrategyBinding", "StrategyProfile", "PROFILES", "bind_content_strategy",
    "choose_content_strategy", "collect_mature_strategy_outcomes", "strategy_instruction",
]
