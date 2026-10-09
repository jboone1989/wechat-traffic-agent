"""Goal-aware Continuous Content Loop v2.

Keeps the legacy loop import-compatible while production uses goal-first evidence exploration.
"""
from __future__ import annotations

from .editorial_policy import EDITORIAL_SCOPE_INSTRUCTION

from datetime import datetime, timedelta
from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .content_loop import (
    Contract, ContinuousContentLoop as _BaseContinuousContentLoop,
    Lesson, ReceiptRuntime, SelfCheck, _put, _replace, _structured, collect_news,
)
from .content_artifact import ContentKind
from .sources.binance_web3_public import read_binance_candidates
from .sources.binance_wallet_store import read_wallet_candidates
from .content_explorer import (
    GoalEvidenceExplorer, _normalize_mission_search_query,
    collect_okx_public_market, collect_trading_evidence,
)
from .content_service import ContentAgent
from .cognition_context import owner_cognition_scope
from .content_strategy import bind_content_strategy, collect_mature_strategy_outcomes, strategy_instruction
from .content_action_prediction import bind_action_prediction, forecast_strategy_candidates
from .draft_inbox import ContentDraftInbox, ContentDraftRecord
from .evidence_floor import EvidenceFloorPlan, artifact_risk_cues, evaluate_evidence_floor, planned_risk_cues
from .memory_life import MemoryLifeRuntime
from .learning_transfer import LearningTransfer
from .models import TopicSignal
from .public_activity import emit_public_activity
from .prospective_predictions import (
    PredictionProposal, ProspectivePredictionLedger, propose_prediction, settle_predictions,
)
from .sources.runtime_web import RuntimeWebSearch
from .sources.runtime_x import RuntimeXSearch
from .social_writing import load_social_style_context
from .trading_draft_worker import default_trading_research_audience
from .traffic_learning import TrafficLearningContext, TrafficLearningContextBuilder, TopicTrafficSignal
from .web_editorial import digest, website_snapshot, _title_key
from .website_experiment import (
    bind_website_experiment, experiment_compliance, experiment_instruction,
)
from .world_feed import WorldFeed
from .x_growth_mission import x_growth_mission_context



@dataclass(frozen=True)
class _CycleGoalStage:
    inputs: dict
    snapshot: dict
    cycle_now: datetime
    strategy_sync: Any
    x_mission_context: Any
    owner_goal: Any
    owner_seed_items: Any
    owner_focus_fast_path: bool
    prefetched_evidence_pool: Any
    world_payload: Any
    experiment: Any
    experiment_payload: Any
    mind_plan: dict
    required_topic: Any
    public_goal: dict
    goal_origin: str


@dataclass(frozen=True)
class _CycleResearchStage:
    runtime: Any
    evidence: list
    evidence_pool: dict
    traffic_context: Any
    traffic_payload: dict
    effective_topic: Any
    goal_active: bool
    topic_context: Any
    explorer_runtime: Any
    learning_memory: Any
    learning_focus: Any
    exploration: dict
    selection: Any
    selected: dict
    related: list
    market_companion: Any
    existing: list


@dataclass(frozen=True)
class _CycleCognitionStage:
    selection: Any
    selected: dict
    related: list
    learning_result: Any
    belief_decision: Any
    worldview_update: Any
    prediction_record: Any
    cognition_decision: Any


@dataclass(frozen=True)
class _CycleEditorialPlan:
    prepared: Any
    strategy_result: Any
    evidence_floor: Any
    fingerprint: str
    expression_kind: str


@dataclass(frozen=True)
class _CycleDraftStage:
    plan: _CycleEditorialPlan
    generated: Any


class _CognitionRuntimeOverlay:
    """Delegate runtime state/actions while overriding only cognition."""

    def __init__(self, base_runtime, cognition_runtime):
        self._base_runtime = base_runtime
        self._cognition_runtime = cognition_runtime

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        return await self._cognition_runtime.cognitive_probe(
            task,
            max_tokens,
            output_schema=output_schema,
        )

    def __getattr__(self, name):
        return getattr(self._base_runtime, name)


from .content_goal_contracts import EvidenceSelection


def _research_supports_expression_source(learning, selected):
    """Bind researched expressions to the same source and verified original excerpts."""
    if not isinstance(learning, dict) or learning.get('progress') not in {'LEARNED', 'DEEPENED'}:
        return False
    if selected.get('source_kind') != 'web-search':
        return False
    evidence_id = selected.get('evidence_id')
    if not evidence_id or learning.get('evidence_id') != evidence_id:
        return False
    summary = str(selected.get('summary') or '')
    facts = learning.get('facts') or []
    return bool(facts) and any(
        isinstance(fact, dict) and fact.get('evidence_id') == evidence_id
        and isinstance(fact.get('quote'), str) and len(fact['quote']) >= 12
        and fact['quote'] in summary
        for fact in facts
    )


def _explicit_hypothetical_note_sections(artifact):
    """Allow source-free Note sections only when every paragraph is explicitly hypothetical."""
    if artifact.kind is not ContentKind.NOTE:
        return set()
    exempt = set()
    for index, section in enumerate(artifact.sections):
        paragraphs = [paragraph.casefold() for paragraph in section.paragraphs]
        if not paragraphs or not any('hypothetical' in paragraph for paragraph in paragraphs):
            continue
        if all(
            ('hypothetical' in paragraph or 'falsified if' in paragraph or 'illustrative' in paragraph)
            for paragraph in paragraphs
        ):
            exempt.add(index)
    return exempt


def _technique_opening_mode(technique):
    """Return a hard opening mode only when a technique explicitly requires one."""
    text = " ".join(
        str(getattr(technique, field, "") or "")
        for field in ("name", "instruction", "check")
    ).casefold().replace("’", "'")
    if re.search(
        r"\b(open|opener|opening|begin|start|lead|first paragraph)\b",
        text,
    ) is None:
        return None
    if re.search(r"\breader(?:'s)? question\b|\breader question\b", text):
        return "reader_question_first"
    if re.search(
        r"\b(source[- ]grounded )?observation\b|\bevidence[- ]first\b",
        text,
    ):
        return "evidence_first"
    return None


def _resolve_technique_requirements(lesson, experiment):
    """Hard experiment constraints outrank optional learned writing techniques."""
    treatment = (
        str(getattr(experiment, "treatment_value", "") or "").strip()
        if experiment is not None
        else ""
    )
    required = []
    deferred = []
    for index, technique in enumerate(lesson.techniques):
        opening_mode = _technique_opening_mode(technique)
        if treatment and opening_mode and opening_mode != treatment:
            deferred.append({
                "technique_index": index,
                "name": str(technique.name),
                "reason": (
                    f"requires {opening_mode} but the prospective experiment "
                    f"requires {treatment}"
                ),
            })
        else:
            required.append(index)
    return {
        "hard_opening_mode": treatment or None,
        "required_indices": required,
        "deferred_indices": [row["technique_index"] for row in deferred],
        "deferred": deferred,
    }


def _required_techniques_applied(application_indices, requirement):
    return set(requirement["required_indices"]) <= set(application_indices)


from .content_goal_contracts import WorldviewDecision


from .content_goal_contracts import CognitionDecision

class MissionDiscoveryDecision(Contract):
    topic: str = Field(min_length=2, max_length=80)
    search_query: str = Field(min_length=8, max_length=300)
    reader_need: str = Field(min_length=10, max_length=400)
    rationale: str = Field(min_length=10, max_length=600)


class DreamInsightDecision(Contract):
    should_investigate: bool
    hypothesis: str = Field(min_length=10, max_length=500)
    topic: str = Field(min_length=2, max_length=80)
    question: str = Field(min_length=10, max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=600)


def _normalize_owner_goal_context(value):
    if not isinstance(value, dict):
        return None
    goal_id = str(value.get("goal_id") or "").strip()
    description = str(value.get("description") or "").strip()
    if not goal_id or not description:
        return None
    try:
        priority = float(value.get("priority") or 0.0)
    except (TypeError, ValueError):
        priority = 0.0
    origin = str(value.get("origin") or "USER").strip().upper()
    if origin not in {"USER", "EXTERNAL_MISSION"}:
        origin = "USER"
    return {
        "goal_id": goal_id[:160],
        "origin": origin,
        "description": description[:2400],
        "reason": str(value.get("reason") or "")[:800],
        "priority": max(0.0, min(1.0, priority)),
        "created_at": str(value.get("created_at") or "")[:80],
    }


def _active_mission_discovery_context(value):
    if not isinstance(value, dict) or value.get("status") != "ACTIVE":
        return None
    roadmap = value.get("roadmap") if isinstance(value.get("roadmap"), dict) else {}
    steps = list(roadmap.get("steps") or ())
    candidates = [
        row for row in steps
        if isinstance(row, dict)
        and row.get("status") in {"RUNNING", "READY"}
        and "content_creation" in set(row.get("resource_tags") or ())
    ]
    candidates.sort(key=lambda row: (0 if row.get("status") == "RUNNING" else 1, str(row.get("step_id") or "")))
    step = candidates[0] if candidates else None
    experiment = value.get("active_experiment")
    if not isinstance(experiment, dict):
        experiment = None
    if step is None and experiment is None:
        return None
    return {
        "mission_id": str(value.get("mission_id") or ""),
        "metric": str(value.get("metric") or ""),
        "current": value.get("current"),
        "target": value.get("target"),
        "trajectory": value.get("trajectory"),
        "roadmap_step": step,
        "active_experiment": experiment,
        "constraints": list(value.get("constraints") or ()),
    }


def _recent_mission_noop_attempts(
    state_root: Path,
    mission_context: dict[str, Any],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Return bounded prior mission angles that produced no useful public trace."""
    mission_id = str(mission_context.get("mission_id") or "").strip()
    roadmap_step = mission_context.get("roadmap_step")
    step_id = (
        str(roadmap_step.get("step_id") or "").strip()
        if isinstance(roadmap_step, dict)
        else ""
    )
    if not mission_id:
        return []
    cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
    if not cycles_root.exists():
        return []

    folders: list[tuple[float, Path]] = []
    for folder in cycles_root.iterdir():
        if not folder.is_dir():
            continue
        try:
            modified = folder.stat().st_mtime
        except OSError:
            continue
        folders.append((modified, folder))
    folders.sort(key=lambda item: item[0], reverse=True)

    attempts: list[dict[str, Any]] = []
    for _modified, folder in folders:
        discovery_path = folder / "mission_discovery.json"
        result_path = folder / "result.json"
        if not discovery_path.exists() or not result_path.exists():
            continue
        try:
            discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(discovery.get("mission_id") or "") != mission_id:
            continue
        if step_id and str(discovery.get("roadmap_step_id") or "") != step_id:
            continue
        if str(result.get("status") or "").upper() != "COGNITION_ONLY":
            continue
        decision = discovery.get("decision")
        if not isinstance(decision, dict):
            continue
        cognition = result.get("cognition")
        if not isinstance(cognition, dict):
            cognition = {}
        attempts.append({
            "cycle": folder.name,
            "topic": str(decision.get("topic") or ""),
            "search_query": str(decision.get("search_query") or ""),
            "reader_need": str(decision.get("reader_need") or ""),
            "cognition_state": str(cognition.get("cognition_state") or ""),
            "rationale": str(cognition.get("rationale") or "")[:400],
            "what_matters": str(cognition.get("what_matters") or "")[:400],
        })
        if len(attempts) >= max(1, min(int(limit), 8)):
            break
    return attempts


def _normalize_mission_discovery(value: MissionDiscoveryDecision) -> MissionDiscoveryDecision:
    # Topic formatting is deterministic metadata, not a reason to spend another LLM call.
    topic = re.sub(r"[^a-z0-9]+", "-", value.topic.strip().lower()).strip("-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", topic):
        raise ValueError("mission discovery topic cannot be normalized to a valid slug")
    query = _normalize_mission_search_query(
        query=value.search_query,
        goal_topic=topic,
    )
    anti_goodhart_text = " ".join(
        [topic.replace("-", " "), query.casefold(), value.reader_need.casefold()]
    )
    prohibited_growth_phrases = (
        "follow for follow",
        "follow-for-follow",
        "reciprocal follow",
        "reciprocal engagement",
        "engagement exchange",
        "follower growth hack",
        "follower growth hacks",
        "algorithm gaming",
        "game the algorithm",
        "buy followers",
        "fake followers",
    )
    if any(phrase in anti_goodhart_text for phrase in prohibited_growth_phrases):
        raise ValueError("mission discovery must investigate useful subject matter, not metric manipulation")
    return value.model_copy(update={"topic": topic, "search_query": query})


def _normalize_cognition_consistency(value: CognitionDecision, worldview_update: object) -> CognitionDecision:
    """Durable state is authoritative when model narration disagrees with it."""
    changed = isinstance(worldview_update, dict) and worldview_update.get("status") in {"FORMED", "REVISED"}
    if value.cognition_state != "BELIEF_CHANGED" or changed:
        return value
    return value.model_copy(update={
        "cognition_state": "INVESTIGATING",
        "what_changed": "Fresh evidence was observed, but no durable worldview change was persisted.",
        "what_matters": "The question remains open until stronger evidence justifies changing the durable worldview.",
        "belief_proposition": None,
        "rationale": "Durable worldview state is authoritative, so an unsupported belief-change claim was normalized.",
    })


def _learning_has_concrete_public_trace(learning: object) -> bool:
    """An open question can still contain a specific, evidence-backed discovery."""
    if not isinstance(learning, dict):
        return False
    from .learning_expression_quality import verified_learning_facts

    facts = verified_learning_facts(learning)
    delta = str(learning.get("learning_delta") or "").strip()
    return bool(facts and len(delta) >= 10)


def _candidate_fingerprint(candidate):
    if candidate.get("source_kind") == "rss":
        return digest({"continuous_content_version": 1, "news_id": candidate["evidence_id"]})
    return digest({
        "continuous_content_version": 2,
        "evidence_id": candidate["evidence_id"],
        "source": candidate["source"],
        "source_revision": candidate["source_revision"],
    })




def _market_expression_instruction(selected):
    """Return writing guidance for fresh market evidence without forcing a format."""
    if not isinstance(selected, dict):
        return ''
    source_kind = str(selected.get('source_kind') or '')
    source = str(selected.get('source') or '').lower()
    title = str(selected.get('title') or '').lower()
    query = str(selected.get('search_query') or '').lower()
    summary = str(selected.get('summary') or '').lower()
    public_market = source_kind == 'public-api' or 'okx' in source
    live_web_market = source_kind == 'web-search' and (
        any(cue in query for cue in ('crypto market', 'market reaction', 'market catalyst', 'funding rates', 'bitcoin today'))
        or any(cue in title for cue in ('news today', 'why btc is down', 'why bitcoin is down', 'market update'))
        or ('bitcoin' in summary and any(cue in summary for cue in ('today', 'trading near', '24 hours', 'catalyst')))
    )
    publisher_feed = str(selected.get('publisher_feed') or '').lower()
    crypto_feed = source_kind == 'rss' and any(domain in publisher_feed for domain in (
        'coindesk.com', 'cointelegraph.com', 'decrypt.co', 'theblock.co',
    ))
    if not (public_market or live_web_market or crypto_feed):
        return ''
    return (
        ' Time-sensitive market evidence: treat this as a market pulse rather than defaulting to an academic research essay. '
        'Lead with a direct timely hook naming the move, event, or catalyst and the agent’s current bounded view. '
        'Then state what is happening now, why the agent interprets it that way, two or three concrete things to watch next, and the observation that would weaken or reverse the view. '
        'Prefer a direct market headline over a meta-methodology headline. Specificity and timeliness matter more than generic hype. '
        'Prefer THOUGHT or RESEARCH_NOTE when the useful information is compact; LONGFORM remains valid only when the evidence genuinely supports deeper analysis. '
        'This market-pulse guidance takes precedence over generic Website structure hints when they conflict. '
        'Do not invent a catalyst, price target, certainty, or personalized trading instruction.'
    )

def _market_companion_evidence(selected, candidates):
    """Pair a live catalyst with current market data, or vice versa, for a market pulse."""
    if not _market_expression_instruction(selected):
        return None
    selected_id = str(selected.get('evidence_id') or '')
    rows = [row for row in candidates if isinstance(row, dict) and str(row.get('evidence_id') or '') != selected_id]
    if str(selected.get('source_kind') or '') == 'public-api':
        return next((row for row in rows
                     if str(row.get('source_kind') or '') in {'rss', 'web-search'}
                     and _market_expression_instruction(row)), None)
    return next((row for row in rows
                 if str(row.get('source_kind') or '') == 'public-api'
                 and _market_expression_instruction(row)), None)


def _market_expression_fingerprint(selected, companion=None):
    """Deduplicate market pulses by catalyst plus live market state, not catalyst alone."""
    if not _market_expression_instruction(selected) or companion is None:
        return _candidate_fingerprint(selected)
    return digest({
        "continuous_content_version": 3,
        "primary_evidence_id": selected.get("evidence_id"),
        "primary_source_revision": selected.get("source_revision"),
        "market_context_evidence_id": companion.get("evidence_id"),
        "market_context_source_revision": companion.get("source_revision"),
    })


def _candidate_available_for_pool(inbox, row):
    """Evidence remains reusable; deduplication belongs to the expression, not its source."""
    return True


def _expression_fingerprint(selected, companion, selection, expression_kind):
    """Allow a different format or useful question without making title edits a new expression."""
    return digest({
        "expression_version": 1,
        "source_fingerprint": _market_expression_fingerprint(selected, companion),
        "expression_kind": expression_kind,
        "reader_question": " ".join(selection.reader_question.casefold().split()),
    })

def _normalize_owner_seed_candidate(row, *, now):
    """Adapt controller search evidence to the canonical content-candidate schema."""
    item = dict(row or {})
    evidence_id = str(item.get("evidence_id") or "").strip()
    if not evidence_id:
        raise ValueError("owner seed evidence requires evidence_id")
    refs = [
        str(ref)
        for ref in list(item.get("evidence_refs") or ())
        if str(ref).strip()
    ]
    revision = str(item.get("source_revision") or "").strip()
    if not revision:
        revision = next(
            (
                ref
                for ref in refs
                if ref.startswith("web-content-sha256:")
                or ref.startswith("web-url-sha256:")
            ),
            evidence_id,
        )
    item.update({
        "evidence_id": evidence_id,
        "source_kind": str(item.get("source_kind") or "web-search"),
        "source": str(item.get("source") or "owner-agent:search-world"),
        "source_revision": revision[:200],
        "observed_at": str(item.get("observed_at") or now.isoformat()),
        "evidence_refs": refs or [evidence_id],
    })
    item.setdefault("published_at", None)
    item.setdefault("publisher_feed", None)
    item.setdefault("search_query", None)
    item.setdefault("search_rank", None)
    item.setdefault("learning_kind", "WORLD_EVENT")
    return item


def _allow_unconstrained_output_fallback(*, goal_active, goal_origin, owner_goal):
    """Do not abandon explicit owner/external goals merely to fill an output slot."""
    if not goal_active:
        return False
    if str(goal_origin or "").upper() == "EXTERNAL_MISSION":
        return False
    if isinstance(owner_goal, dict) and str(owner_goal.get("goal_id") or "").strip():
        return False
    return True


_OWNER_FOCUS_GENERIC_TERMS = {
    "growth", "experiment", "experiments", "content", "research", "evidence",
    "goal", "goals", "task", "tasks", "current", "real", "public", "latest",
    "new", "website", "topic", "topics",
}


def _owner_focus_anchor_terms(topic):
    words = [
        value
        for value in re.findall(r"[a-z0-9]+", str(topic or "").casefold())
        if len(value) >= 4 and value not in _OWNER_FOCUS_GENERIC_TERMS
    ]
    return tuple(dict.fromkeys(words))[:4]


def _owner_focus_candidate_matches(row, topic):
    anchors = _owner_focus_anchor_terms(topic)
    if not anchors:
        return True
    text = " ".join(
        str(row.get(field) or "")
        for field in ("title", "summary", "url", "source")
    ).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", text)
    compact = normalized.replace(" ", "")
    matched = sum(
        1
        for anchor in anchors
        if anchor in normalized or anchor in compact
    )
    return matched >= min(2, len(anchors))


def _owner_focus_search_query(owner_goal, topic):
    anchors = list(_owner_focus_anchor_terms(topic))
    description = str((owner_goal or {}).get("description") or "")
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9.+/-]*", description)
    stop = {
        "goal", "draft", "growth", "experiment", "content", "current", "real",
        "task", "the", "and", "for", "with", "into", "from", "this", "that",
        "through", "existing", "value", "safe", "public", "posted",
    }
    selected = []
    for token in raw_tokens:
        folded = token.casefold().strip("./-")
        if not folded or folded in stop:
            continue
        if len(folded) < 3 and folded not in {"ai", "pi"}:
            continue
        if folded in anchors or folded in {row.casefold() for row in selected}:
            continue
        selected.append(token)
        if len(selected) >= 10:
            break
    query = " ".join([*anchors, *selected]).strip()
    return query[:180].rstrip(" ,.;:-")


def _compact_website_decision_view(snapshot, *, article_limit=80):
    """Bound Website context for semantic selection without dropping local validation data."""
    if not isinstance(snapshot, dict):
        return {"articles": [], "topics": {}, "article_count": 0, "omitted_article_count": 0}
    rows = [row for row in list(snapshot.get("articles") or ()) if isinstance(row, dict)]
    limit = max(1, min(int(article_limit), 120))
    visible = rows[-limit:]
    articles = []
    visible_ids = set()
    for row in visible:
        artifact = row.get("artifact") if isinstance(row.get("artifact"), dict) else {}
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        visible_ids.add(artifact_id)
        articles.append({
            "artifact_id": artifact_id,
            "title": str(artifact.get("title") or "")[:120],
            "summary": str(artifact.get("summary") or "")[:220],
            "tags": [str(tag)[:80] for tag in list(artifact.get("tags") or ())[:8]],
        })
    topics = {}
    for topic, artifact_ids in dict(snapshot.get("topics") or {}).items():
        bounded_ids = [
            str(value)
            for value in list(artifact_ids or ())
            if str(value) in visible_ids
        ][:24]
        if bounded_ids:
            topics[str(topic)[:80]] = bounded_ids
    return {
        "articles": articles,
        "topics": topics,
        "article_count": len(rows),
        "omitted_article_count": max(0, len(rows) - len(visible)),
    }


def _compact_evidence_decision_view(rows, *, row_limit=8):
    """Bound evidence for semantic selection; full rows remain local by evidence_id."""
    compact = []
    for row in list(rows or ())[:max(1, min(int(row_limit), 24))]:
        if not isinstance(row, dict):
            continue
        compact.append({
            "evidence_id": str(row.get("evidence_id") or "")[:160],
            "source_kind": str(row.get("source_kind") or "")[:60],
            "source": str(row.get("source") or "")[:160],
            "title": str(row.get("title") or "")[:180],
            "summary": str(row.get("summary") or "")[:450],
            "url": str(row.get("url") or "")[:300],
            "observed_at": str(row.get("observed_at") or "")[:48],
            "published_at": str(row.get("published_at") or "")[:48],
            "search_query": str(row.get("search_query") or "")[:180],
            "search_rank": row.get("search_rank"),
            "source_revision": str(row.get("source_revision") or "")[:120],
            "evidence_refs": [
                str(value)[:120]
                for value in list(row.get("evidence_refs") or ())[:3]
            ],
        })
    return compact


def _bounded_evidence_refs(value, *, limit=5):
    """Keep durable reference identifiers, never arbitrary source text."""
    return [
        str(ref)
        for ref in list((value or {}).get("evidence_refs") or ())
        if 0 < len(str(ref)) <= 200
    ][: max(1, min(int(limit), 8))]


def _compact_worldview_beliefs(
    rows,
    *,
    topic,
    reader_question,
    source_evidence,
    related_proposition=None,
    limit=12,
):
    """Keep the exact related belief first, then beliefs relevant to this decision."""
    visible = [row for row in list(rows or ()) if isinstance(row, dict)]
    related = str(related_proposition or "").strip()
    source = source_evidence if isinstance(source_evidence, dict) else {}
    query = " ".join(str(value or "") for value in (
        topic,
        reader_question,
        source.get("title"),
        str(source.get("summary") or "")[:450],
        related,
    )).casefold()
    query_terms = {
        token for token in re.findall(r"[a-z0-9]+", query)
        if len(token) >= 3
    }
    ranked = []
    for index, row in enumerate(visible):
        proposition = str(row.get("proposition") or "").strip()
        if not proposition:
            continue
        proposition_terms = set(re.findall(r"[a-z0-9]+", proposition.casefold()))
        exact_related = bool(related and proposition == related)
        try:
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            stance = abs(float(row.get("stance") or 0.0))
        except (TypeError, ValueError):
            stance = 0.0
        ranked.append((
            1000.0 if exact_related else 0.0,
            len(query_terms & proposition_terms),
            confidence,
            stance,
            -index,
            row,
        ))
    ranked.sort(reverse=True, key=lambda item: item[:5])
    compact = []
    seen = set()
    for *_score, row in ranked:
        proposition = str(row.get("proposition") or "").strip()
        if not proposition or proposition in seen:
            continue
        seen.add(proposition)
        compact.append({
            "proposition": proposition[:500],
            "stance": row.get("stance"),
            "confidence": row.get("confidence"),
            "revision_count": row.get("revision_count"),
        })
        if len(compact) >= max(1, min(int(limit), 24)):
            break
    return compact

def _compact_worldview_learning(value):
    """Project research conclusions without replaying acquired source bodies."""
    if not isinstance(value, dict):
        return None
    focus = value.get("focus") if isinstance(value.get("focus"), dict) else {}
    facts = []
    for fact in list(value.get("facts") or ())[:5]:
        if not isinstance(fact, dict):
            continue
        facts.append({
            "evidence_id": str(fact.get("evidence_id") or "")[:160],
            "quote": str(fact.get("quote") or "")[:700],
            "observation": str(fact.get("observation") or "")[:600],
        })
    sources = []
    for row in list(value.get("sources") or ())[:5]:
        if not isinstance(row, dict):
            continue
        compact = _compact_evidence_decision_view([row], row_limit=1)[0]
        compact["evidence_refs"] = _bounded_evidence_refs(row, limit=3)
        sources.append(compact)
    return {
        "cycle": str(value.get("cycle") or "")[:120],
        "topic": str(value.get("topic") or "")[:120],
        "focus": {
            "question": str(focus.get("question") or "")[:400],
            "search_query": str(focus.get("search_query") or "")[:300],
            "why_interested": str(focus.get("why_interested") or "")[:600],
            **{key: focus[key] for key in ("approach", "completion_criterion", "method_change") if key in focus},
        },
        "progress": value.get("progress"),
        "progressed": bool(value.get("progressed")),
        "learning_delta": str(value.get("learning_delta") or "")[:800],
        **{key: value[key] for key in ("answer", "reasoning_steps", "question_status", "inquiry_action", "resume_when") if key in value},
        "facts": facts,
        "related_proposition": value.get("related_proposition"),
        "next_question": str(value.get("next_question") or "")[:500],
        "next_query": str(value.get("next_query") or "")[:300],
        "rationale": str(value.get("rationale") or "")[:600],
        "sources": sources,
        "source_projection": {
            "full_source_bodies": "retained in learning_result.json and evidence archive",
            "source_count": len(value.get("sources") or ()),
        },
    }


def _worldview_decision_context(
    *,
    topic,
    reader_question,
    source_evidence,
    current_beliefs,
    mind_goal,
    learning,
):
    """Bound FORM_WORLDVIEW input while preserving canonical evidence locally."""
    compact_learning = _compact_worldview_learning(learning)
    source_view = (
        _compact_evidence_decision_view([source_evidence], row_limit=1)[0]
        if isinstance(source_evidence, dict)
        else {}
    )
    if isinstance(source_evidence, dict):
        source_view["evidence_refs"] = _bounded_evidence_refs(
            source_evidence, limit=3
        )
    related = (
        compact_learning.get("related_proposition")
        if isinstance(compact_learning, dict)
        else None
    )
    return {
        "topic": str(topic or "")[:120],
        "reader_question": str(reader_question or "")[:400],
        "source_evidence": source_view,
        "current_beliefs": _compact_worldview_beliefs(
            current_beliefs,
            topic=topic,
            reader_question=reader_question,
            source_evidence=source_evidence,
            related_proposition=related,
        ),
        "mind_goal": _compact_selection_mind_plan({"goal": mind_goal}).get(
            "goal", {}
        ),
        "learning": compact_learning,
        "durable_refs": {
            "source_evidence_id": (
                str(source_evidence.get("evidence_id") or "")[:160]
                if isinstance(source_evidence, dict)
                else ""
            ),
            "full_evidence_retained_locally": True,
        },
    }


def _compact_generation_evidence(value):
    """Give drafting only the attributed facts needed to write, never source bodies."""
    if not isinstance(value, dict) or not value:
        return None
    compact = _compact_evidence_decision_view([value], row_limit=1)[0]
    compact["summary"] = str(value.get("summary") or "")[:1600]
    compact["evidence_refs"] = _bounded_evidence_refs(value, limit=5)
    return compact


def _compact_generation_evidence_floor(value):
    """Project the evidence-floor decision without embedding counter-source bodies."""
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        return {}
    payload["counter_evidence"] = _compact_generation_evidence(
        payload.get("counter_evidence")
    )
    return payload


def _compact_generation_related_articles(rows):
    """Keep navigation context, not previously published article bodies."""
    compact = []
    for row in list(rows or ())[:3]:
        if not isinstance(row, dict):
            continue
        compact.append({
            "artifact_id": str(row.get("artifact_id") or "")[:160],
            "title": str(row.get("title") or "")[:180],
            "summary": str(row.get("summary") or "")[:600],
            "tags": [
                str(tag)[:80]
                for tag in list(row.get("tags") or ())[:8]
            ],
        })
    return compact


def _compact_selection_traffic(value, *, topic_limit=12):
    """Keep only traffic signals that can change an evidence-angle decision."""
    if not isinstance(value, dict):
        return {}
    topics = [
        dict(row)
        for row in list(value.get("topics") or ())
        if isinstance(row, dict)
    ]
    topics.sort(
        key=lambda row: (
            float(row.get("priority_score") or 0.0),
            float(row.get("demand_score") or 0.0),
            int(row.get("trend_hits") or 0),
        ),
        reverse=True,
    )
    fields = (
        "topic", "article_count", "exposure_status", "search_rows",
        "impressions", "clicks", "ctr", "average_position",
        "mean_pageviews_24h", "pageview_samples_24h", "trend_hits",
        "priority_score",
    )
    return {
        "search_data_status": value.get("search_data_status"),
        "pageview_data_status": value.get("pageview_data_status"),
        "selected_topic": value.get("selected_topic"),
        "selection_authority": value.get("selection_authority"),
        "topics": [
            {field: row.get(field) for field in fields if field in row}
            for row in topics[:max(1, min(int(topic_limit), 20))]
        ],
    }


def _compact_selection_mind_plan(value):
    """Drop the large available-topic catalog from semantic selection."""
    if not isinstance(value, dict):
        return {}
    goal = value.get("goal") if isinstance(value.get("goal"), dict) else {}
    discovery = (
        value.get("mission_discovery")
        if isinstance(value.get("mission_discovery"), dict)
        else {}
    )
    return {
        "cycle": value.get("cycle"),
        "required_topic": value.get("required_topic"),
        "external_mission_id": value.get("external_mission_id"),
        "owner_goal_id": value.get("owner_goal_id"),
        "goal": {
            "goal_id": goal.get("goal_id"),
            "origin": goal.get("origin"),
            "description": str(goal.get("description") or "")[:600],
            "confidence": goal.get("confidence"),
            "expected_value": goal.get("expected_value"),
            "interest_refs": [
                str(ref)[:160] for ref in list(goal.get("interest_refs") or ())[:8]
            ],
            "inquiry_refs": [
                str(ref)[:160] for ref in list(goal.get("inquiry_refs") or ())[:8]
            ],
            "value_refs": [
                str(ref)[:160] for ref in list(goal.get("value_refs") or ())[:8]
            ],
        },
        "mission_discovery": {
            "topic": str(discovery.get("topic") or "")[:100],
            "search_query": str(discovery.get("search_query") or "")[:500],
            "reader_need": str(discovery.get("reader_need") or "")[:600],
            "rationale": str(discovery.get("rationale") or "")[:500],
        },
    }


def _compact_selection_mission(value):
    """Project the active X mission without historical experiments or constraints."""
    if not isinstance(value, dict):
        return {}
    roadmap = (
        value.get("roadmap_step")
        if isinstance(value.get("roadmap_step"), dict)
        else {}
    )
    experiment = (
        value.get("active_experiment")
        if isinstance(value.get("active_experiment"), dict)
        else {}
    )
    return {
        "status": value.get("status"),
        "mission_id": value.get("mission_id"),
        "metric": value.get("metric"),
        "current": value.get("current"),
        "target": value.get("target"),
        "trajectory": value.get("trajectory"),
        "roadmap_step": {
            "step_id": roadmap.get("step_id"),
            "title": str(roadmap.get("title") or "")[:300],
            "status": roadmap.get("status"),
        },
        "active_experiment": {
            "experiment_id": experiment.get("experiment_id"),
            "status": experiment.get("status"),
            "strategy": str(experiment.get("strategy") or "")[:1000],
            "expected_effect": str(experiment.get("expected_effect") or "")[:800],
        },
    }


def _select_evidence_decision_context(
    *,
    evidence,
    snapshot,
    traffic_payload,
    previous_titles,
    mind_plan,
    x_mission_context,
):
    """Project selection context below the durable cognition per-message budget."""
    return {
        "evidence": _compact_evidence_decision_view(evidence),
        "website": _compact_website_decision_view(snapshot, article_limit=24),
        "traffic_context": _compact_selection_traffic(traffic_payload),
        "previous_titles": [
            str(value)[:160] for value in list(previous_titles or ())[-32:]
        ],
        "mind_plan": _compact_selection_mind_plan(mind_plan),
        "x_growth_mission": _compact_selection_mission(x_mission_context),
    }


def _compact_writing_experience(rows, *, row_limit=4):
    """Project prior cycles to the small feedback surface needed for writing practice."""
    if not isinstance(rows, list):
        return []
    compact = []
    for row in rows[-max(1, min(int(row_limit), 8)):]:
        if not isinstance(row, dict):
            continue
        lesson = row.get("lesson") if isinstance(row.get("lesson"), dict) else {}
        self_check = row.get("self_check") if isinstance(row.get("self_check"), dict) else {}
        source_techniques = (
            row.get("techniques")
            if isinstance(row.get("techniques"), list)
            else lesson.get("techniques")
        )
        techniques = []
        for technique in list(source_techniques or ())[:3]:
            if not isinstance(technique, dict):
                continue
            techniques.append({
                "name": str(technique.get("name") or "")[:100],
                "instruction": str(technique.get("instruction") or "")[:240],
                "check": str(technique.get("check") or "")[:180],
            })
        compact.append({
            "cycle": str(row.get("cycle") or "")[:100],
            "techniques": techniques,
            "adaptation": str(
                row.get("adaptation") or lesson.get("adaptation") or ""
            )[:360],
            "automated_checks_passed": bool(row.get("automated_checks_passed")),
            "expression_kind": str(row.get("expression_kind") or "")[:40],
            "word_count": int(row.get("word_count") or 0),
            "missing_technique_applications": list(
                row.get("missing_technique_applications") or ()
            )[:3],
            "missing_section_grounding": list(
                row.get("missing_section_grounding") or ()
            )[:8],
            "self_check": {
                "grounded": self_check.get("grounded"),
                "useful": self_check.get("useful"),
                "next_practice": str(self_check.get("next_practice") or "")[:500],
                "rationale": str(self_check.get("rationale") or "")[:300],
            },
        })
    return compact


def _generation_strategy_receipt(binding, mind_strategy_context):
    """Project strategy choice into drafting context without replaying its evidence ledger."""
    if binding is None:
        return None
    payload = (
        binding.model_dump(mode="json")
        if hasattr(binding, "model_dump")
        else dict(binding)
        if isinstance(binding, dict)
        else {}
    )
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    mind = mind_strategy_context if isinstance(mind_strategy_context, dict) else {}
    return {
        "strategy_id": str(payload.get("strategy_id") or "")[:120],
        "topic": str(payload.get("topic") or "")[:200],
        "profile": {
            "name": str(profile.get("name") or "")[:80],
            "reader_intent": str(profile.get("reader_intent") or "")[:80],
            "angle": str(profile.get("angle") or "")[:80],
            "title_pattern": str(profile.get("title_pattern") or "")[:80],
            "structure_pattern": str(profile.get("structure_pattern") or "")[:120],
            "coverage_action": str(profile.get("coverage_action") or "")[:80],
        },
        "opening_mode": payload.get("opening_mode"),
        "selection_mode": payload.get("selection_mode"),
        "decision_reason": str(payload.get("decision_reason") or "")[:500],
        "memory_prior_applied": bool(payload.get("memory_prior_applied")),
        "decision_evidence_receipt": {
            "mind_evidence_ref_count": len(payload.get("mind_strategy_evidence_refs") or ()),
            "prediction_evidence_ref_count": len(payload.get("prediction_evidence_refs") or ()),
            "mind_context_persisted": bool(mind),
            "mind_outcome_count": int(mind.get("outcome_count") or 0),
        },
    }


def _repair_related_ids(selection, snapshot):
    """Keep only real navigation IDs; a newly born Topic does not need a pre-existing member."""
    existing = {row['artifact']['artifact_id'] for row in snapshot['articles']}
    repaired = []
    for value in selection.related_ids:
        if value in existing and value not in repaired:
            repaired.append(value)
    return selection.model_copy(update={'related_ids': tuple(repaired[:3])})


def _fallback_evidence_selection(evidence, *, required_topic, effective_topic, existing_titles):
    """Fail closed to supplied evidence when semantic selection output is malformed."""
    if not evidence:
        raise ValueError('fallback evidence selection requires supplied evidence')
    existing_keys = {_title_key(value) for value in existing_titles}
    selected = None
    title = None
    for row in evidence:
        source_title = ' '.join(str(row.get('title') or '').split())
        if not source_title:
            continue
        candidates = [
            source_title[:80],
            ('Evidence update: ' + source_title)[:80],
        ]
        for candidate_title in candidates:
            if len(candidate_title) >= 4 and _title_key(candidate_title) not in existing_keys:
                selected, title = row, candidate_title
                break
        if selected is not None:
            break
    if selected is None:
        selected = evidence[0]
        suffix = str(selected.get('evidence_id') or 'evidence')[:8]
        title = f'Evidence update {suffix}'
    topic = str(required_topic or effective_topic or 'evidence')
    value = EvidenceSelection(
        title=title,
        evidence_id=str(selected['evidence_id']),
        topic=topic,
        related_ids=(),
        reader_question='What does the supplied evidence establish, and what remains uncertain?',
        gap='The site has fresh supplied evidence but no bounded explanation of its implications and limits.',
    )
    return value


class ContinuousContentLoop(_BaseContinuousContentLoop):
    def __init__(self, *, runtime, state_root, artifacts_path, site_root, base_url, curriculum_path,
                 feeds, interval_hours=6, fetcher=collect_news, publisher=None, mind=None, explorer=None,
                 capability_runtime=None, website_experiments=False, world_feed=None, neural_runtime=None,
                 evolution_bridge=None):
        if not 1 <= interval_hours <= 168 or not 1 <= len(feeds) <= 4:
            raise ValueError('require 1..168 hour interval and 1..4 operator feeds')
        self.runtime, self.state = runtime, Path(state_root)
        self.artifacts_path, self.site_root, self.base_url = Path(artifacts_path), Path(site_root), base_url
        self.curriculum_path, self.feeds = Path(curriculum_path), tuple(feeds)
        self.interval_hours, self.fetcher, self.publisher = interval_hours, fetcher, publisher
        self.mind = mind
        self.neural_runtime = neural_runtime
        self.evolution_bridge = evolution_bridge
        self.website_experiments = bool(website_experiments)
        self.world_feed = world_feed
        self.traffic_learning = TrafficLearningContextBuilder(state_root=self.state, account=self.base_url)
        self.predictions = ProspectivePredictionLedger(self.state)
        self.memory_life = MemoryLifeRuntime(self.state)
        self.learning_transfer = LearningTransfer(self.state)
        if explorer is None:
            web_searcher = None
            x_searcher = None
            if capability_runtime is not None and "web.search" in capability_runtime.available_capabilities():
                source = RuntimeWebSearch(capability_runtime)
                web_searcher = lambda query, now: source.search(query=query, now=now)
            if capability_runtime is not None and "x.search" in capability_runtime.available_capabilities():
                x_source = RuntimeXSearch(capability_runtime)
                x_searcher = lambda query, now: x_source.search(query=query, now=now)
            explorer = GoalEvidenceExplorer(
                state_root=self.state, feeds=self.feeds,
                rss_fetcher=lambda feeds, now: self.fetcher(feeds, now),
                market_fetcher=collect_okx_public_market,
                binance_reader=lambda now: read_binance_candidates(self.state, now),
                wallet_reader=lambda now: read_wallet_candidates(self.state, now),
                trading_fetcher=lambda now: collect_trading_evidence(self.state, now),
                web_searcher=web_searcher,
            )
            if self.world_feed is None and web_searcher is not None:
                self.world_feed = WorldFeed(state_root=self.state, web_searcher=web_searcher, x_searcher=x_searcher)
        self.explorer = explorer
        self.root = self.state / 'content/continuous'
        self.inbox = ContentDraftInbox(self.state / 'content')

    def _activity(self, cycle, phase, title, detail='', status='working', kind='progress'):
        return emit_public_activity(self.state, cycle=cycle, phase=phase, title=title, detail=detail,
                                    status=status, kind=kind)

    async def run_once(
        self,
        *,
        now=None,
        cycle_id=None,
        focus_topic=None,
        owner_goal=None,
        seed_evidence=None,
        cognition_runtime=None,
        publication_surface='AUTO',
    ):
        topic = str(focus_topic or '').strip()
        if topic and not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,79}', topic):
            raise ValueError('focus_topic must be a lowercase kebab-case slug')
        if publication_surface not in {'AUTO', 'WEBSITE', 'X_SHORT_POST', 'X_LONG_POST', 'X_ARTICLE'}:
            raise ValueError('unknown publication surface')
        self._publication_surface = publication_surface
        self._focus_topic = topic or None
        self._owner_goal = _normalize_owner_goal_context(owner_goal)
        self._seed_evidence = [
            dict(row)
            for row in list(seed_evidence or ())[:8]
            if isinstance(row, dict)
        ]
        owner_scope = (
            {
                **self._owner_goal,
                'topic': topic,
            }
            if self._owner_goal is not None
            else None
        )
        base_runtime = self.runtime
        if self._owner_goal is not None and cognition_runtime is not None:
            self.runtime = _CognitionRuntimeOverlay(
                base_runtime,
                cognition_runtime,
            )
        try:
            with owner_cognition_scope(owner_scope):
                return await super().run_once(now=now, cycle_id=cycle_id)
        finally:
            self.runtime = base_runtime
            self._focus_topic = None
            self._owner_goal = None
            self._seed_evidence = []
            self._publication_surface = 'AUTO'

    async def verify_due_predictions(self, *, now):
        due = self.predictions.due(now)
        if not due:
            return {"status": "NO_DUE_PREDICTIONS", "settlements": []}
        if self.world_feed is None:
            return {"status": "WORLD_FEED_UNAVAILABLE", "due": [row.prediction_id for row in due], "settlements": []}
        cycle = f"wakeful-prediction-{int(now.timestamp())}"
        folder = self.state / "content" / "prediction_verification" / cycle
        folder.mkdir(parents=True, exist_ok=True)
        base_pool = await self.explorer.collect(now=now, active=True)
        base_items = [row for row in base_pool["items"] if _candidate_available_for_pool(self.inbox, row)]
        world_runtime = ReceiptRuntime(self.runtime, folder / "world_model_calls")
        projection = self.mind.public_projection() if self.mind is not None else {}
        world_payload = await self.world_feed.collect(
            runtime=world_runtime, now=now, base_items=base_items,
            mind_projection=projection,
            verification_queries=tuple(row.verification_query for row in due),
        )
        world_payload["failures"] = list(base_pool.get("failures", [])) + list(world_payload.get("failures", []))
        _put(folder / "world_feed.json", world_payload)
        prediction_runtime = ReceiptRuntime(self.runtime, folder / "prediction_model_calls")
        settlements = list(await settle_predictions(
            ledger=self.predictions, runtime=prediction_runtime,
            items=list(world_payload.get("items", [])), now=now,
            mind=self.mind, cycle=cycle,
        ))
        _put(folder / "prediction_settlements.json", settlements)
        self._activity(
            cycle, "LEARN", "A prediction deadline woke me",
            f"Checked {len(due)} due prediction(s) against fresh post-prediction evidence.",
            status="completed",
        )
        return {"status": "VERIFIED", "cycle": cycle, "settlements": settlements}

    def _cycle_inputs(self, folder, cycle, now):
        inputs_path = folder / 'inputs.json'
        if inputs_path.exists():
            inputs = json.loads(inputs_path.read_text())
            if inputs.get('context_version') != 2:
                raise ValueError('cannot resume a legacy incomplete content cycle')
        else:
            inventory = self.publisher.inventory_path() if self.publisher else self.artifacts_path
            snapshot = website_snapshot(inventory, self.site_root, self.base_url)
            history = []
            for p in sorted((self.root / 'cycles').glob('*/experience.json'))[-8:]:
                experience = json.loads(p.read_text())
                if int(experience['cycle'].split('-')[-1]) < int(cycle.split('-')[-1]):
                    history.append(experience)
            curriculum = self.curriculum_path.read_text()
            if len(curriculum) > 16000:
                raise ValueError('curriculum exceeds budget')
            previous = [r.artifact.title for r in self.inbox.list_records()][-200:]
            inputs = {'context_version': 2, 'website': snapshot,
                      'prior_experience': _compact_writing_experience(history),
                      'curriculum': curriculum, 'previous_titles': previous, 'now': now.isoformat()}
            _put(inputs_path, inputs)
        return inputs

    def _sync_strategy_learning(self, folder, cycle, cycle_now):
        strategy_sync = None
        if self.mind is not None and hasattr(self.mind, 'observe_strategy_outcomes'):
            strategy_sync_path = folder / 'strategy_learning_sync.json'
            if strategy_sync_path.exists():
                strategy_sync = json.loads(strategy_sync_path.read_text(encoding='utf-8'))
            else:
                mature_outcomes = collect_mature_strategy_outcomes(
                    state_root=self.state, account=self.base_url, as_of=cycle_now,
                )
                strategy_sync = self.mind.observe_strategy_outcomes(mature_outcomes)
                _put(strategy_sync_path, strategy_sync)
                if strategy_sync.get('applied'):
                    self._activity(
                        cycle, 'LEARN', 'Audience outcomes changed my strategy memory',
                        f"Applied {len(strategy_sync['applied'])} mature 24h Website outcome(s) before choosing this cycle's action.",
                        status='completed',
                    )
        return strategy_sync

    def _owner_cycle_context(self, folder, now):
        x_mission_context = x_growth_mission_context(self.state)
        mission_context = _active_mission_discovery_context(x_mission_context)
        owner_goal = _normalize_owner_goal_context(getattr(self, '_owner_goal', None))
        owner_mission_fast_path = bool(
            owner_goal is not None
            and owner_goal.get('origin') == 'EXTERNAL_MISSION'
            and mission_context is not None
        )
        owner_focus_fast_path = bool(
            owner_goal is not None
            and str(getattr(self, '_focus_topic', None) or '').strip()
        )
        owner_seed_path = folder / 'owner_seed_evidence.json'
        if owner_seed_path.exists():
            owner_seed_payload = json.loads(
                owner_seed_path.read_text(encoding='utf-8')
            )
        else:
            owner_seed_items = [
                _normalize_owner_seed_candidate(row, now=now)
                for row in list(getattr(self, '_seed_evidence', ()) or ())[:8]
                if isinstance(row, dict)
                and str(row.get('evidence_id') or '').strip()
                and str(row.get('title') or '').strip()
                and str(row.get('summary') or '').strip()
            ]
            owner_seed_payload = {
                'schema_version': 'content-owner-seed-evidence.v1',
                'owner_goal_id': str((owner_goal or {}).get('goal_id') or ''),
                'topic': str(getattr(self, '_focus_topic', None) or ''),
                'items': owner_seed_items,
                'source': 'OWNER_AGENT_EPISODE_SEARCH_WORLD',
            }
            if owner_seed_items:
                _put(owner_seed_path, owner_seed_payload)
        owner_seed_items = [
            _normalize_owner_seed_candidate(row, now=now)
            for row in list(owner_seed_payload.get('items') or ())
            if isinstance(row, dict)
        ]
        if owner_seed_items and owner_seed_payload.get('items') != owner_seed_items:
            owner_seed_payload = {
                **owner_seed_payload,
                'items': owner_seed_items,
            }
            _put(owner_seed_path, owner_seed_payload)
        return (x_mission_context, mission_context, owner_goal,
                owner_mission_fast_path, owner_focus_fast_path, owner_seed_items)

    async def _load_world_feed(self, folder, cycle, now):
        world_path = folder / 'world_feed.json'
        if world_path.exists():
            world_payload = json.loads(world_path.read_text())
        else:
            self._activity(cycle, 'OBSERVE', 'I’m looking outside my existing interests',
                           'I’m checking seed signals, other agents, and sources I discover myself before deciding what deserves attention.')
            base_pool = await self.explorer.collect(now=now, active=True)
            base_pool['items'] = [row for row in base_pool['items']
                                  if _candidate_available_for_pool(self.inbox, row)]
            world_runtime = ReceiptRuntime(self.runtime, folder / 'world_model_calls')
            projection = self.mind.public_projection() if self.mind is not None else {}
            due_predictions = self.predictions.due(now)
            world_payload = await self.world_feed.collect(
                runtime=world_runtime, now=now, base_items=base_pool['items'],
                mind_projection=projection,
                verification_queries=tuple(row.verification_query for row in due_predictions),
            )
            world_payload['failures'] = list(base_pool.get('failures', [])) + list(world_payload.get('failures', []))
            _put(world_path, world_payload)
            self._activity(cycle, 'OBSERVE', 'The world changed what is available to think about',
                           f"I found {len(world_payload.get('items', []))} signals and marked {len(world_payload.get('observations', []))} as worth thinking about.",
                           status='completed')
            learning_candidates = [row for row in world_payload.get('observations', [])
                                   if isinstance(row, dict) and row.get('learning_kind') in {'METHOD', 'TOOL'}]
            if learning_candidates:
                lead = learning_candidates[0]
                self._activity(cycle, 'OBSERVE', 'Found a method worth testing',
                               f"{lead.get('topic') or 'A new method/tool'} is DISCOVERED evidence only; I will investigate it before adoption.",
                               status='completed', kind='learning-candidate')
        return world_payload

    async def _settle_world_predictions(self, folder, cycle, now, world_payload):
        settlement_path = folder / 'prediction_settlements.json'
        if settlement_path.exists():
            prediction_settlements = json.loads(settlement_path.read_text(encoding='utf-8'))
        else:
            prediction_runtime = ReceiptRuntime(self.runtime, folder / 'prediction_model_calls')
            prediction_settlements = list(await settle_predictions(
                ledger=self.predictions, runtime=prediction_runtime, items=list(world_payload.get('items', [])),
                now=now, mind=self.mind, cycle=cycle,
            ))
            _put(settlement_path, prediction_settlements)
            terminal = [row for row in prediction_settlements if row.get('learning_status') == 'APPLIED']
            if terminal:
                self._activity(cycle, 'LEARN', 'Past predictions met the real world',
                               f"Applied {len(terminal)} matured prediction outcome(s) to persistent experience and worldview.",
                               status='completed')

    async def _collect_world_evidence(self, folder, cycle, now,
                                      owner_mission_fast_path, owner_focus_fast_path):
        if (self.world_feed is None or owner_mission_fast_path or owner_focus_fast_path):
            return None, None
        world_payload = await self._load_world_feed(folder, cycle, now)
        await self._settle_world_predictions(folder, cycle, now, world_payload)
        prefetched_evidence_pool = {
            'items': [row for row in world_payload.get('items', [])
                      if _candidate_available_for_pool(self.inbox, row)],
            'failures': list(world_payload.get('failures', [])),
            'observed_at': world_payload.get('observed_at') or now.isoformat(),
            'signal_kind': 'world feed: seed, agent-network, and agent-discovered public evidence',
        }
        return world_payload, prefetched_evidence_pool

    def _bind_cycle_experiment(self, folder, cycle, now):
        experiment = None
        if self.website_experiments:
            experiment_path = folder / 'website_experiment.json'
            if not experiment_path.exists() and any(
                (folder / name).exists() for name in ('evidence_candidates.json', 'exploration.json', 'model_calls')
            ):
                raise ValueError('cannot bind Website experiment after evidence/model selection has begun')
            experiment = bind_website_experiment(experiment_path, cycle=cycle, decided_at=now)
        return experiment

    async def _choose_mind_plan(self, folder, cycle, now, snapshot, world_payload,
                                owner_goal, mission_context, mission_priority, plan_path,
                                plan_preexisting):
        if plan_preexisting:
            return json.loads(plan_path.read_text())
        if self.mind is None:
            mind_plan = {'cycle': cycle, 'goal': None, 'required_topic': None}
        elif world_payload is not None:
            mind_plan = self.mind.plan_for_cycle(
                cycle=cycle, available_topics=snapshot['topics'],
                world_observations=world_payload.get('observations', ()),
            )
        else:
            mind_plan = self.mind.plan_for_cycle(cycle=cycle, available_topics=snapshot['topics'])
        mind_plan = self._focused_cycle_plan(folder, owner_goal, mission_context, mind_plan)
        mind_plan = await self._dream_cycle_plan(
            folder, now, snapshot, mission_priority, mind_plan,
        )
        return self._neural_taste_plan(folder, cycle, now, snapshot, mission_priority, mind_plan)

    def _focused_cycle_plan(self, folder, owner_goal, mission_context, mind_plan):
        if getattr(self, '_focus_topic', None):
            endogenous_goal = mind_plan.get('goal')
            if owner_goal is not None:
                focused_goal = {
                    'goal_id': str(owner_goal.get('goal_id') or ''),
                    'origin': str(owner_goal.get('origin') or 'USER'),
                    'description': str(owner_goal.get('description') or '')[:2400],
                    'confidence': 1.0,
                    'expected_value': 1.0,
                    'interest_refs': [self._focus_topic],
                    'inquiry_refs': [str(owner_goal.get('goal_id') or '')],
                    'value_refs': [
                        str((mission_context or {}).get('mission_id') or '')
                    ],
                }
                focus_kind = 'OWNER_AGENT_FOCUS'
            else:
                focused_goal = {
                    'goal_id': 'bootstrap-focus:' + self._focus_topic,
                    'origin': 'BOOTSTRAP_FOCUS',
                    'description': 'Bootstrap one useful piece around ' + self._focus_topic + ' while preserving the autonomous goal for later cycles.',
                    'confidence': 1.0, 'expected_value': 1.0,
                    'interest_refs': [self._focus_topic], 'inquiry_refs': [], 'value_refs': [],
                }
                focus_kind = 'BOOTSTRAP_FOCUS'
            mind_plan = {
                **mind_plan,
                'endogenous_goal': endogenous_goal,
                'bootstrap_focus': self._focus_topic,
                'focus_kind': focus_kind,
                'owner_goal_id': (
                    str(owner_goal.get('goal_id') or '')
                    if owner_goal is not None else None
                ),
                'external_mission_id': (
                    str((mission_context or {}).get('mission_id') or '')
                    if owner_goal is not None else None
                ),
                'goal': focused_goal,
                'required_topic': self._focus_topic,
            }
            _put(folder / 'bootstrap_focus.json', {
                'topic': self._focus_topic,
                'endogenous_goal': endogenous_goal,
                'focus_kind': focus_kind,
                'owner_goal_id': (
                    str(owner_goal.get('goal_id') or '')
                    if owner_goal is not None else None
                ),
            })
        return mind_plan

    @staticmethod
    def _select_stable_attractor(dreams_payload):
        stable_candidate = None
        stable_attractor = None
        for attractor in (dreams_payload.get('attractors') or {}).values():
            if not isinstance(attractor, dict) or attractor.get('status') != 'STABLE':
                continue
            candidate = attractor.get('goal_candidate')
            if not isinstance(candidate, dict) or candidate.get('status') != 'UNINTERPRETED':
                continue
            if int(attractor.get('consolidation_epoch_count') or 0) < 3:
                continue
            pair = (candidate, attractor)
            if stable_candidate is None or (
                float(attractor.get('stability_score') or 0.0),
                int(attractor.get('recurrence_count') or 0),
            ) > (
                float(stable_attractor.get('stability_score') or 0.0),
                int(stable_attractor.get('recurrence_count') or 0),
            ):
                stable_candidate, stable_attractor = pair
        return stable_candidate, stable_attractor

    async def _dream_cycle_plan(self, folder, now, snapshot, mission_priority, mind_plan):
        if (mind_plan.get('goal') is not None
                or mind_plan.get('required_topic') is not None or mission_priority):
            return mind_plan
        dreams_payload = {}
        dreams_path = self.state / 'application' / 'neural_dreams.json'
        try:
            dreams_payload = json.loads(dreams_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, json.JSONDecodeError):
            dreams_payload = {}
        stable_candidate, stable_attractor = self._select_stable_attractor(dreams_payload)
        if stable_candidate is not None and stable_attractor is not None:
            mind_plan = await self._stable_attractor_plan(
                folder, now, snapshot, dreams_payload,
                stable_candidate, stable_attractor, mind_plan,
            )
        stable_candidate_handled = stable_candidate is not None
        preconcept = None if stable_candidate_handled else next((
            row for row in reversed(dreams_payload.get('preconcepts') or [])
            if isinstance(row, dict)
            and not bool(row.get('verbalized'))
            and float(row.get('association_strength') or 0.0) >= 0.12
        ), None)
        if preconcept is not None:
            mind_plan = await self._preconcept_plan(folder, now, snapshot, preconcept, mind_plan)
        return mind_plan

    async def _decide_stable_attractor(self, folder, snapshot, dreams_payload,
                                      stable_candidate, stable_attractor):
        candidate_id = str(stable_candidate.get('candidate_id') or '')
        attractor_id = str(stable_attractor.get('attractor_id') or '')
        source_dreams = [
            str(value) for value in stable_attractor.get('source_dreams') or ()
            if value
        ][-8:]
        supporting_preconcepts = [
            row for row in (dreams_payload.get('preconcepts') or [])
            if isinstance(row, dict)
            and (
                str(row.get('attractor_id') or '') == attractor_id
                or str(row.get('source_dream') or '') in set(source_dreams)
            )
        ][-8:]
        birth_path = folder / 'dream_goal_birth.json'
        if birth_path.exists():
            birth_payload = json.loads(birth_path.read_text(encoding='utf-8'))
            dream_goal = DreamInsightDecision.model_validate(birth_payload['decision'])
        else:
            birth_runtime = ReceiptRuntime(self.runtime, folder / 'dream_goal_birth_calls')
            task = (
                'STABLE_DREAM_ATTRACTOR_INTERPRETATION: You are the rational Ego layer. '
                'The neural system has already birthed this goal candidate after recurrent '
                'dream consolidation across multiple sleep epochs. You did NOT originate the '
                'desire. Reality-check it and, only if useful, translate it into one bounded, '
                'falsifiable evidence-seeking research goal. topic must be a lowercase '
                'kebab-case slug. Neural channel names are internal signals, not facts about '
                'the external world. Do not claim subjective consciousness.\n'
                + json.dumps({
                    'goal_candidate': stable_candidate,
                    'attractor': {
                        'attractor_id': attractor_id,
                        'channels': stable_attractor.get('channels'),
                        'recurrence_count': stable_attractor.get('recurrence_count'),
                        'consolidation_epoch_count': stable_attractor.get('consolidation_epoch_count'),
                        'stability_score': stable_attractor.get('stability_score'),
                        'source_dreams': source_dreams,
                    },
                    'supporting_preconcepts': supporting_preconcepts,
                    'available_topics': sorted(snapshot['topics'])[:80],
                    'recent_articles': [
                        {
                            'title': row['artifact']['title'],
                            'summary': row['artifact']['summary'][:220],
                        }
                        for row in snapshot['articles'][-10:]
                    ],
                }, ensure_ascii=False)
            )
            def validate_dream_goal(value):
                if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,79}', value.topic):
                    raise ValueError('dream-born goal topic must be lowercase kebab-case')
                return value
            dream_goal = await _structured(
                birth_runtime, task, DreamInsightDecision, validate=validate_dream_goal
            )
            _put(birth_path, {
                'schema_version': 'ferro-dream-goal-birth.v1',
                'candidate_id': candidate_id,
                'attractor_id': attractor_id,
                'source_dreams': source_dreams,
                'decision': dream_goal.model_dump(mode='json'),
                'authority': 'EGO_INTERPRETATION_OF_STABLE_NEURAL_ATTRACTOR',
                'origin': 'STABLE_DREAM_ATTRACTOR',
            })
        return candidate_id, attractor_id, source_dreams, dream_goal

    async def _stable_attractor_plan(self, folder, now, snapshot, dreams_payload,
                                    stable_candidate, stable_attractor, mind_plan):
        candidate_id, attractor_id, source_dreams, dream_goal = await self._decide_stable_attractor(
            folder, snapshot, dreams_payload, stable_candidate, stable_attractor,
        )
        goal_id = 'dream-attractor:' + (attractor_id or candidate_id or 'unknown')
        try:
            from .neural_dreaming import FerroDreamEngine
            FerroDreamEngine(self.state, substrate=None).mark_goal_candidate(
                candidate_id,
                status=(
                    'ACTIVATED'
                    if dream_goal.should_investigate
                    else 'DECLINED_BY_EGO_REALITY_CHECK'
                ),
                goal_id=goal_id if dream_goal.should_investigate else None,
                hypothesis=dream_goal.hypothesis,
                description=dream_goal.question,
                now=now,
            )
        except (KeyError, OSError, ValueError):
            pass
        if dream_goal.should_investigate:
            mind_plan = {
                **mind_plan,
                'endogenous_goal': mind_plan.get('goal'),
                'goal': {
                    'goal_id': goal_id,
                    'origin': 'DREAM_CONSOLIDATION',
                    'description': dream_goal.question,
                    'confidence': dream_goal.confidence,
                    'expected_value': max(
                        0.35,
                        dream_goal.confidence
                        * float(stable_attractor.get('stability_score') or 0.0),
                    ),
                    'interest_refs': [dream_goal.topic],
                    'inquiry_refs': [candidate_id, attractor_id],
                    'value_refs': source_dreams,
                },
                'required_topic': dream_goal.topic,
                'dream_goal_candidate': stable_candidate,
                'dream_attractor': {
                    'attractor_id': attractor_id,
                    'channels': stable_attractor.get('channels'),
                    'recurrence_count': stable_attractor.get('recurrence_count'),
                    'consolidation_epoch_count': stable_attractor.get('consolidation_epoch_count'),
                    'stability_score': stable_attractor.get('stability_score'),
                },
                'dream_goal_birth': dream_goal.model_dump(mode='json'),
            }
        return mind_plan

    async def _preconcept_plan(self, folder, now, snapshot, preconcept, mind_plan):
        insight_path = folder / 'dream_insight.json'
        if insight_path.exists():
            insight_payload = json.loads(insight_path.read_text(encoding='utf-8'))
            dream_insight = DreamInsightDecision.model_validate(insight_payload['decision'])
        else:
            dream_runtime = ReceiptRuntime(self.runtime, folder / 'dream_interpretation_calls')
            task = (
                'DREAM_PRECONCEPT_INTERPRETATION: You are the rational Ego layer. '
                'The neural system has already produced the supplied preconcept through replay; '
                'do not pretend the language model dreamed it. Translate the association into one '
                'bounded, falsifiable research hypothesis. It may become a Website research topic '
                'only if investigating it would produce useful evidence for this persistent agent. '
                'topic must be a lowercase kebab-case slug. Do not treat neural channel names as '
                'facts about the world and do not claim subjective consciousness.\n'
                + json.dumps({
                    'preconcept': preconcept,
                    'available_topics': sorted(snapshot['topics'])[:80],
                    'recent_articles': [
                        {
                            'title': row['artifact']['title'],
                            'summary': row['artifact']['summary'][:220],
                        }
                        for row in snapshot['articles'][-10:]
                    ],
                }, ensure_ascii=False)
            )
            def validate_dream_insight(value):
                if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,79}', value.topic):
                    raise ValueError('dream insight topic must be lowercase kebab-case')
                return value
            dream_insight = await _structured(
                dream_runtime, task, DreamInsightDecision, validate=validate_dream_insight
            )
            _put(insight_path, {
                'schema_version': 'ferro-dream-insight.v1',
                'preconcept_id': preconcept.get('preconcept_id'),
                'source_dream': preconcept.get('source_dream'),
                'decision': dream_insight.model_dump(mode='json'),
                'authority': 'EGO_INTERPRETATION_OF_NEURAL_PRECONCEPT',
            })
        try:
            from .neural_dreaming import FerroDreamEngine
            FerroDreamEngine(self.state, substrate=None).mark_verbalized(
                str(preconcept.get('preconcept_id') or ''),
                hypothesis=dream_insight.hypothesis,
                now=now,
            )
        except (KeyError, OSError, ValueError):
            pass
        if dream_insight.should_investigate:
            mind_plan = {
                **mind_plan,
                'endogenous_goal': mind_plan.get('goal'),
                'goal': {
                    'goal_id': 'dream-insight:' + str(preconcept.get('preconcept_id') or 'unknown'),
                    'origin': 'DREAM_INSIGHT',
                    'description': dream_insight.question,
                    'confidence': dream_insight.confidence,
                    'expected_value': max(0.25, dream_insight.confidence),
                    'interest_refs': [dream_insight.topic],
                    'inquiry_refs': [str(preconcept.get('preconcept_id') or '')],
                    'value_refs': [str(preconcept.get('source_dream') or '')],
                },
                'required_topic': dream_insight.topic,
                'dream_preconcept': preconcept,
                'dream_insight': dream_insight.model_dump(mode='json'),
            }
        return mind_plan

    def _neural_taste_plan(self, folder, cycle, now, snapshot, mission_priority, mind_plan):
        neural_candidates = list(mind_plan.get('available_topics') or snapshot['topics'])
        if (
            self.neural_runtime is not None
            and not getattr(self, '_focus_topic', None)
            and neural_candidates
            and not mission_priority
        ):
            goal = mind_plan.get('goal')
            origin = str(goal.get('origin') or '') if isinstance(goal, dict) else ''
            contestable = goal is None or origin in {'INTEREST', 'INQUIRY'}
            if contestable:
                from .interest_learning import topic_contexts
                contexts = topic_contexts(
                    self.state, neural_candidates,
                    self.mind.public_projection() if self.mind is not None else {},
                )
                taste = self.neural_runtime.rank_neural_taste(
                    neural_candidates, now, contexts=contexts,
                )
                neural_topic = str(taste.get('selected_topic') or '').strip() or None
                ego_topic = str(mind_plan.get('required_topic') or '').strip() or None
                contest = self.neural_runtime.choose_brain_or_cortex(
                    cycle=cycle,
                    neural_choice=neural_topic,
                    ego_choice=ego_topic,
                    now=now,
                )
                executed_topic = str(contest.get('executed_choice') or '').strip() or None
                _put(folder / 'neural_taste.json', {
                    'schema_version': 'ferro-neural-taste-cycle.v1',
                    'taste': taste,
                    'competition': contest,
                })
                if executed_topic and contest.get('authority') == 'NEURAL':
                    prior_goal = mind_plan.get('goal')
                    mind_plan = {
                        **mind_plan,
                        'endogenous_goal': prior_goal,
                        'goal': {
                            'goal_id': 'neural-taste:' + str(contest.get('contest_id') or cycle),
                            'origin': 'NEURAL_TASTE',
                            'description': 'Investigate the topic selected by Ferro neural taste: ' + executed_topic,
                            'confidence': min(1.0, max(0.25, float(taste.get('margin') or 0.0) / 10.0 + 0.35)),
                            'expected_value': 0.55,
                            'interest_refs': [executed_topic],
                            'inquiry_refs': [],
                            'value_refs': [str(contest.get('contest_id') or '')],
                        },
                        'required_topic': executed_topic,
                        'neural_taste': taste,
                        'brain_cortex_competition': contest,
                    }
                else:
                    mind_plan = {
                        **mind_plan,
                        'neural_taste': taste,
                        'brain_cortex_competition': contest,
                    }
        return mind_plan

    def _build_mission_discovery_query(self, folder, snapshot, mission_context, owner_goal):
        mission_runtime = ReceiptRuntime(
            self.runtime, folder / 'mission_discovery_calls'
        )
        recent_articles = [
            {
                'title': row['artifact']['title'],
                'summary': row['artifact']['summary'][:240],
                'tags': list(row['artifact'].get('tags') or ()),
            }
            for row in snapshot['articles'][-12:]
        ]
        recent_noop_attempts = _recent_mission_noop_attempts(
            self.state,
            mission_context,
        )
        task = (
            'MISSION_DISCOVERY: choose one concrete research subject for the next '
            'content evidence search that advances the active external mission without '
            'forcing publication. When owner_goal is present, it is the current human '
            'task constraint: if it explicitly asks to serve this mission, the chosen '
            'subject and search query must directly investigate that owner-specified '
            'subject rather than silently substituting an unrelated mission angle. '
            'The topic must describe the subject matter, not '
            'follower growth or engagement tactics. Prefer Ferro/LivingRuntime strengths '
            'and existing site topics when genuinely useful; a new durable topic is '
            'allowed. The search_query must seek current public factual evidence about '
            'that subject and must be a compact keyword-style query whose first words are '
            'the subject/core entities; do not begin it with generic words such as recent, '
            'latest, current, public, research, evidence, or case studies. Never search for '
            'follow-for-follow, engagement manipulation, '
            'spam, or ways to evade platform rules. When '
            'recent_no_material_change_attempts is non-empty, treat those rows as '
            'negative search feedback: choose a materially different reader need, '
            'subject angle, or factual search query instead of repeating a semantically '
            'equivalent attempt. Do not lower the publication/evidence bar merely to '
            'make progress. Mission/roadmap text is decision context only and is not '
            'factual evidence.\n'
            + EDITORIAL_SCOPE_INSTRUCTION + '\n'
            + json.dumps({
                'external_mission': mission_context,
                'owner_goal': owner_goal,
                'existing_topics': sorted(snapshot['topics'])[:80],
                'recent_site_coverage': recent_articles,
                'recent_no_material_change_attempts': recent_noop_attempts,
            }, ensure_ascii=False)
        )
        prior_pairs = {
            (
                str(row.get('topic') or '').strip().lower(),
                str(row.get('search_query') or '').strip().casefold(),
            )
            for row in recent_noop_attempts
        }
        return mission_runtime, task, recent_noop_attempts, prior_pairs

    def _failed_mission_discovery(self, folder, cycle, plan_path, mind_plan,
                                  mission_context, owner_goal, exc):
        mission_error = {
            'schema_version': 'content-mission-discovery-error.v1',
            'error_type': type(exc).__name__,
            'message': str(exc)[:500],
            'mission_id': str(mission_context.get('mission_id') or ''),
            'roadmap_step_id': str(
                (mission_context.get('roadmap_step') or {}).get('step_id')
                or ''
            ),
            'experiment_id': str(
                (mission_context.get('active_experiment') or {}).get('experiment_id')
                or ''
            ),
            'owner_goal_id': str((owner_goal or {}).get('goal_id') or ''),
            'retryable': True,
            'publication_authority': False,
        }
        _put(folder / 'mission_discovery_error.json', mission_error)
        if owner_goal is not None:
            mission_id = str(mission_context.get('mission_id') or '')
            owner_goal_id = str(owner_goal.get('goal_id') or '')
            mind_plan = {
                **mind_plan,
                'endogenous_goal': mind_plan.get('goal'),
                'goal': {
                    'goal_id': (
                        'external-mission-pending:'
                        + (mission_id or 'unknown')
                    ),
                    'origin': 'EXTERNAL_MISSION',
                    'description': (
                        'Resolve evidence search for the owner-directed external mission: '
                        + str(owner_goal.get('description') or '')
                    )[:2400],
                    'confidence': 1.0,
                    'expected_value': 1.0,
                    'interest_refs': [],
                    'inquiry_refs': [owner_goal_id],
                    'value_refs': [mission_id],
                },
                'required_topic': None,
                'external_mission_id': mission_id,
                'owner_goal_id': owner_goal_id,
                'mission_discovery_error': mission_error,
            }
            _put(plan_path, mind_plan)
            return mind_plan, {
                'status': 'ERROR',
                'cycle': cycle,
                'error_type': type(exc).__name__,
                'message': str(exc)[:500],
                'retryable': True,
                'mission_id': mission_id,
                'owner_goal_id': owner_goal_id,
                'mind_plan': mind_plan,
            }
        return mind_plan, None

    @staticmethod
    def _apply_mission_discovery_plan(mind_plan, mission_context, owner_goal, mission_decision):
        mission_goal_id = (
            'external-mission-discovery:'
            + str(mission_context.get('mission_id') or 'unknown')
            + ':'
            + str(
                (mission_context.get('roadmap_step') or {}).get('step_id')
                or (mission_context.get('active_experiment') or {}).get('experiment_id')
                or 'research'
            )
        )
        mind_plan = {
            **mind_plan,
            'endogenous_goal': mind_plan.get('goal'),
            'goal': {
                'goal_id': mission_goal_id,
                'origin': 'EXTERNAL_MISSION',
                'description': (
                    'Research evidence for the active external mission: '
                    + mission_decision.reader_need
                ),
                'confidence': 1.0,
                'expected_value': 1.0,
                'interest_refs': [mission_decision.topic],
                'inquiry_refs': [mission_goal_id],
                'value_refs': [str(mission_context.get('mission_id') or '')],
            },
            'required_topic': mission_decision.topic,
            'mission_discovery': mission_decision.model_dump(mode='json'),
            'external_mission_id': mission_context.get('mission_id'),
            'owner_goal_id': str((owner_goal or {}).get('goal_id') or ''),
        }
        return mind_plan

    async def _mission_discovery_plan(self, folder, cycle, snapshot, plan_path, mind_plan,
                                      mission_context, owner_goal, mission_can_claim_cycle,
                                      plan_preexisting, selection_started):
        if not (mission_can_claim_cycle and mission_context is not None
                and not plan_preexisting and not selection_started):
            return mind_plan, None
        mission_path = folder / 'mission_discovery.json'
        mission_decision = None
        if mission_path.exists():
            stored = json.loads(mission_path.read_text(encoding='utf-8'))
            decision_payload = stored.get('decision') if isinstance(stored, dict) else None
            if isinstance(decision_payload, dict):
                mission_decision = _normalize_mission_discovery(
                    MissionDiscoveryDecision.model_validate(decision_payload),
                )
        else:
            mission_runtime, task, recent_noop_attempts, prior_pairs = (
                self._build_mission_discovery_query(folder, snapshot, mission_context, owner_goal)
            )
            def validate_mission_discovery(value):
                normalized = _normalize_mission_discovery(value)
                pair = (
                    normalized.topic.strip().lower(),
                    normalized.search_query.strip().casefold(),
                )
                if pair in prior_pairs:
                    raise ValueError(
                        'mission discovery must change angle after a no-material-change attempt'
                    )
                return normalized
            try:
                mission_decision = await _structured(
                    mission_runtime,
                    task,
                    MissionDiscoveryDecision,
                    validate=validate_mission_discovery,
                )
                mission_decision = _normalize_mission_discovery(mission_decision)
                _put(mission_path, {
                    'schema_version': 'content-mission-discovery.v1',
                    'mission_id': mission_context.get('mission_id'),
                    'roadmap_step_id': (
                        (mission_context.get('roadmap_step') or {}).get('step_id')
                    ),
                    'experiment_id': (
                        (mission_context.get('active_experiment') or {}).get('experiment_id')
                    ),
                    'owner_goal_id': str((owner_goal or {}).get('goal_id') or ''),
                    'decision': mission_decision.model_dump(mode='json'),
                    'recent_no_material_change_attempts': recent_noop_attempts,
                    'generation_authority': 'DISCOVERY_ONLY',
                    'publication_authority': False,
                })
            except Exception as exc:
                return self._failed_mission_discovery(
                    folder, cycle, plan_path, mind_plan, mission_context, owner_goal, exc,
                )
        if mission_decision is not None:
            mind_plan = self._apply_mission_discovery_plan(
                mind_plan, mission_context, owner_goal, mission_decision,
            )
        return mind_plan, None

    async def _cycle_goal_phase(self, folder, cycle, now):
        self._activity(cycle, 'CYCLE', 'I’m starting a new content cycle',
                       'I’m reading the current site, my persisted experience, audience signals, and available evidence before deciding what to do.')
        inputs = self._cycle_inputs(folder, cycle, now)
        snapshot = inputs['website']
        cycle_now = datetime.fromisoformat(inputs['now'])
        strategy_sync = self._sync_strategy_learning(folder, cycle, cycle_now)
        (x_mission_context, mission_context, owner_goal,
         owner_mission_fast_path, owner_focus_fast_path,
         owner_seed_items) = self._owner_cycle_context(folder, now)
        world_payload, prefetched_evidence_pool = await self._collect_world_evidence(
            folder, cycle, now, owner_mission_fast_path, owner_focus_fast_path,
        )
        experiment = self._bind_cycle_experiment(folder, cycle, now)
        experiment_payload = experiment.model_dump(mode='json') if experiment is not None else None
        plan_path = folder / 'mind_plan.json'
        plan_preexisting = plan_path.exists()
        mission_step = (
            mission_context.get('roadmap_step')
            if isinstance(mission_context, dict)
            and isinstance(mission_context.get('roadmap_step'), dict)
            else {}
        )
        mission_experiment = (
            mission_context.get('active_experiment')
            if isinstance(mission_context, dict)
            and isinstance(mission_context.get('active_experiment'), dict)
            else {}
        )
        # A roadmap step already marked RUNNING must get a bounded chance to
        # produce the publication that starts its still-PROPOSED experiment.
        # Once the experiment is RUNNING, ordinary endogenous/dream goals regain
        # their normal opportunity to compete while the outcome window matures.
        mission_priority = bool(
            owner_mission_fast_path
            or (
                mission_context is not None
                and mission_step.get('status') == 'RUNNING'
                and mission_experiment.get('status') == 'PROPOSED'
            )
        )
        mind_plan = await self._choose_mind_plan(
            folder, cycle, now, snapshot, world_payload, owner_goal, mission_context,
            mission_priority, plan_path, plan_preexisting,
        )

        required_topic = mind_plan.get('required_topic')
        selection_started = any(
            (folder / name).exists()
            for name in ('evidence_candidates.json', 'exploration.json', 'model_calls')
        )
        goal_value = mind_plan.get('goal')
        goal_origin = (
            str(goal_value.get('origin') or '')
            if isinstance(goal_value, dict)
            else ''
        )
        mission_can_claim_cycle = (
            mind_plan.get('goal') is None and required_topic is None
        ) or (
            mission_priority and goal_origin in {'INTEREST', 'INQUIRY'}
        )
        mind_plan, mission_error_result = await self._mission_discovery_plan(
            folder, cycle, snapshot, plan_path, mind_plan, mission_context, owner_goal,
            mission_can_claim_cycle, plan_preexisting, selection_started,
        )
        if mission_error_result is not None:
            return mission_error_result
        required_topic = mind_plan.get('required_topic')

        if not plan_preexisting:
            _put(plan_path, mind_plan)
        public_goal = mind_plan.get('goal') if isinstance(mind_plan.get('goal'), dict) else {}
        self._activity(cycle, 'GOAL', 'I chose a goal',
                       str(public_goal.get('description') or ('Topic: ' + str(required_topic) if required_topic else 'No endogenous goal; traffic signals may choose the topic.')),
                       status='completed')
        if mind_plan.get('goal') is not None and required_topic is None:
            return {'status': 'NO_RELEVANT_EVIDENCE', 'cycle': cycle, 'mind_plan': mind_plan,
                    'candidate_count': 0, 'source_failures': [], 'reason': 'UNBOUND_GOAL_TOPIC'}
        mind_goal_active = required_topic is not None
        return _CycleGoalStage(
            inputs, snapshot, cycle_now, strategy_sync, x_mission_context,
            owner_goal, owner_seed_items, owner_focus_fast_path,
            prefetched_evidence_pool, world_payload,
            experiment, experiment_payload, mind_plan, required_topic, public_goal,
            goal_origin,
        )

    async def _cycle_research_phase(self, folder, cycle, now, goal):
        inputs, snapshot, cycle_now, mind_plan, required_topic = (
            goal.inputs, goal.snapshot, goal.cycle_now, goal.mind_plan, goal.required_topic,
        )
        public_goal, owner_goal, owner_seed_items = (
            goal.public_goal, goal.owner_goal, goal.owner_seed_items,
        )
        prefetched_evidence_pool = goal.prefetched_evidence_pool
        owner_focus_fast_path = goal.owner_focus_fast_path
        x_mission_context, goal_origin = goal.x_mission_context, goal.goal_origin
        from .research_evidence_service import EvidencePolicies, EvidenceRequest, ResearchEvidenceService
        research = ResearchEvidenceService(
            explorer=self.explorer, inbox=self.inbox, traffic_learning=self.traffic_learning,
            runtime=self.runtime, mind=self.mind, state_root=self.state,
            publication_surface=getattr(self, '_publication_surface', 'AUTO'),
            activity=self._activity,
            policies=EvidencePolicies(
                _candidate_available_for_pool, _owner_focus_candidate_matches,
                _owner_focus_search_query, _owner_focus_anchor_terms,
                _allow_unconstrained_output_fallback,
            ),
        )
        research_request = EvidenceRequest(
            folder, cycle, now, cycle_now, snapshot, required_topic, public_goal,
            mind_plan, owner_goal, owner_seed_items, prefetched_evidence_pool,
            owner_focus_fast_path,
        )
        research_result = await research.run(research_request)
        evidence_pool = research_result.evidence_pool
        traffic_context = research_result.traffic_context
        traffic_payload = research_result.traffic_payload
        effective_topic = research_result.effective_topic
        goal_active = research_result.goal_active
        topic_context = research_result.topic_context
        explorer_runtime = research_result.explorer_runtime
        learning_memory = research_result.learning_memory
        learning_focus = research_result.learning_focus
        learning_result = None
        exploration = research_result.exploration

        admission = await research.admit(research_request, research_result, goal_origin=goal_origin)
        if admission.early_result is not None:
            return admission.early_result
        evidence = admission.evidence
        exploration = admission.exploration
        traffic_payload = admission.traffic_payload
        effective_topic = admission.effective_topic
        goal_active = admission.goal_active
        runtime = ReceiptRuntime(self.runtime, folder / 'model_calls')
        existing = inputs['previous_titles'] + [r['artifact']['title'] for r in snapshot['articles']]
        from .evidence_choice_service import EvidenceChoicePolicies, EvidenceChoiceRequest, EvidenceChoiceService
        choice = EvidenceChoiceService(
            runtime=self.runtime, explorer=self.explorer, inbox=self.inbox,
            activity=self._activity,
            policies=EvidenceChoicePolicies(
                structured=_structured, decision_context=_select_evidence_decision_context,
                fallback=_fallback_evidence_selection, repair_related=_repair_related_ids,
                market_companion=_market_companion_evidence,
                market_instruction=_market_expression_instruction,
                candidate_available=_candidate_available_for_pool, title_key=_title_key,
                scope_instruction=EDITORIAL_SCOPE_INSTRUCTION,
            ),
        )
        chosen = await choice.choose(EvidenceChoiceRequest(
            folder, cycle, now, snapshot, evidence, evidence_pool,
            inputs['previous_titles'], traffic_payload, mind_plan, x_mission_context,
            learning_focus, required_topic, effective_topic, goal_active,
            explorer_runtime, topic_context,
        ), runtime)
        selection, selected, related, market_companion, selection_mode = (
            chosen.selection, chosen.selected, chosen.related, chosen.market_companion,
            chosen.selection_mode,
        )
        return _CycleResearchStage(
            runtime, evidence, evidence_pool, traffic_context, traffic_payload,
            effective_topic, goal_active, topic_context, explorer_runtime,
            learning_memory, learning_focus, exploration, selection, selected,
            related, market_companion, existing,
        )

    async def _cycle_cognition_phase(self, folder, cycle, now, goal, research):
        learning_result = None
        inputs, snapshot, cycle_now, mind_plan, world_payload = (
            goal.inputs, goal.snapshot, goal.cycle_now, goal.mind_plan, goal.world_payload,
        )
        (runtime, evidence, evidence_pool, learning_focus, learning_memory, exploration,
         selected, selection, related, market_companion, existing, traffic_payload) = (
            research.runtime, research.evidence, research.evidence_pool,
            research.learning_focus, research.learning_memory, research.exploration,
            research.selected, research.selection, research.related,
            research.market_companion, research.existing, research.traffic_payload,
        )
        worldview_update = None
        if learning_focus is not None:
            from .learning_research import learning_receipt
            from .learning_inquiry_service import InquiryRequest, LearningInquiryService
            inquiry = LearningInquiryService(
                state_root=self.state, explorer=self.explorer, mind=self.mind,
                neural_runtime=self.neural_runtime, activity=self._activity,
                has_public_trace=_learning_has_concrete_public_trace,
            )
            inquiry_result = await inquiry.execute(InquiryRequest(
                folder, cycle, now, selection, selected, evidence, learning_focus,
                learning_memory, mind_plan, exploration,
            ), runtime)
            selection, selected, learning_result = (
                inquiry_result.selection, inquiry_result.selected, inquiry_result.learning,
            )
            if inquiry_result.early_result is not None:
                return inquiry_result.early_result
        from .worldview_assessment_service import WorldviewAssessmentService, WorldviewRequest
        record_belief = getattr(self.mind, 'record_research_belief', None) if self.mind is not None else None
        assessment = WorldviewAssessmentService(
            mind=self.mind, predictions=self.predictions, activity=self._activity,
            structured=_structured, decision_context=_worldview_decision_context,
            propose_prediction=propose_prediction,
        )
        worldview_result = await assessment.assess(WorldviewRequest(
            folder, cycle, cycle_now, selection, selected, mind_plan, learning_result,
        ), runtime, record_belief)
        belief_decision = worldview_result.decision
        worldview_update = worldview_result.update
        prediction_record = worldview_result.prediction

        from .expression_decision_service import ExpressionDecisionService, ExpressionRequest
        expression = await ExpressionDecisionService(
            state_root=self.state, mind=self.mind, activity=self._activity,
            structured=_structured, normalize=_normalize_cognition_consistency,
            has_public_trace=_learning_has_concrete_public_trace,
            market_instruction=_market_expression_instruction,
            replace_receipt=_replace,
        ).decide(ExpressionRequest(
            folder, cycle, selected, market_companion, mind_plan, inputs,
            learning_result, belief_decision, worldview_update, prediction_record,
            exploration, evidence_pool, getattr(self, '_focus_topic', None),
        ), runtime, enabled=record_belief is not None)
        cognition_decision = expression.decision
        if expression.early_result is not None:
            return expression.early_result

        from .method_evidence_binding_service import MethodBindingRequest, MethodEvidenceBindingService
        binder = MethodEvidenceBindingService(
            activity=self._activity, structured=_structured,
            research_supports=_research_supports_expression_source,
            title_key=_title_key, repair_related=_repair_related_ids,
            website_view=_compact_website_decision_view,
        )
        bound = await binder.bind(MethodBindingRequest(
            folder, cycle, selection, selected, related, cognition_decision,
            learning_result, mind_plan, world_payload, snapshot, existing,
        ), runtime)
        if bound.early_result is not None:
            return bound.early_result
        selection, selected, related = bound.selection, bound.selected, bound.related
        if bound.authority is not None:
            traffic_payload['selected_topic'] = selection.topic
            traffic_payload['selection_authority'] = bound.authority

        if getattr(self, '_publication_surface', 'AUTO') in {'X_SHORT_POST', 'X_LONG_POST', 'X_ARTICLE'}:
            from .x_owner_execution import prepare_owner_x_expression

            if cognition_decision is None:
                raise ValueError('owner X execution requires persisted cognition')
            return prepare_owner_x_expression(
                folder=folder, source_evidence=selected,
                cognition=cognition_decision.model_dump(mode='json'),
                worldview=worldview_update, prediction=prediction_record,
                mind_plan=mind_plan,
            )
        return _CycleCognitionStage(
            selection, selected, related, learning_result, belief_decision,
            worldview_update, prediction_record, cognition_decision,
        )

    async def _cycle_editorial_phase(self, folder, cycle, now, goal, research, cognition):
        inputs, cycle_now, experiment, experiment_payload = (
            goal.inputs, goal.cycle_now, goal.experiment, goal.experiment_payload,
        )
        runtime, traffic_context = research.runtime, research.traffic_context
        selected, selection, learning_result = (
            cognition.selected, cognition.selection, cognition.learning_result,
        )
        cognition_decision = cognition.cognition_decision
        market_companion = research.market_companion
        from .writing_practice_service import WritingPracticeRequest, WritingPracticeService
        expression_kind = cognition_decision.expression_kind if cognition_decision is not None else 'LONGFORM'
        practice = WritingPracticeService(
            activity=self._activity, structured=_structured, lesson_contract=Lesson,
            compact_experience=_compact_writing_experience,
            resolve_requirements=_resolve_technique_requirements,
            experiment_instruction=experiment_instruction,
            market_instruction=_market_expression_instruction,
            social_style_loader=load_social_style_context, digest=digest,
        )
        prepared = await practice.prepare(WritingPracticeRequest(
            folder, cycle, self.curriculum_path, inputs, expression_kind, selected,
            learning_result, experiment, experiment_payload,
        ), runtime)
        lesson, lesson_payload, lesson_id = (
            prepared.lesson, prepared.lesson_payload, prepared.lesson_id,
        )
        technique_requirements, required_technique_indices = (
            prepared.technique_requirements, prepared.required_indices,
        )
        social_style_context, is_short_note, minimum_words, market_pulse = (
            prepared.style_context, prepared.is_short_note, prepared.minimum_words,
            prepared.market_pulse,
        )

        fingerprint = _expression_fingerprint(selected, market_companion, selection, expression_kind)
        if self.inbox.contains(fingerprint):
            self.inbox.load(fingerprint)
            return {'status': 'ALREADY_DRAFTED', 'cycle': cycle, 'source_fingerprint': fingerprint}

        from .editorial_strategy_service import EditorialStrategyService, StrategyDecisionRequest
        strategy_service = EditorialStrategyService(
            state_root=self.state, account=self.base_url, mind=self.mind,
            memory_life=self.memory_life, evolution_bridge=self.evolution_bridge,
            activity=self._activity, learning_transfer=self.learning_transfer,
        )
        strategy_result = strategy_service.decide(StrategyDecisionRequest(
            folder, cycle, cycle_now, selection, traffic_context, market_pulse, experiment,
        ))
        content_strategy, strategy_payload, mind_strategy_context = (
            strategy_result.strategy, strategy_result.strategy_payload,
            strategy_result.mind_context,
        )
        process_skill, process_steps, retry_verification_once = (
            strategy_result.process_skill, strategy_result.process_steps,
            strategy_result.retry_verification_once,
        )
        action_prediction_payload = strategy_result.action_prediction_payload

        from .counter_evidence_service import CounterEvidenceService, CounterEvidenceRequest
        evidence_floor = await CounterEvidenceService(
            explorer=self.explorer, activity=self._activity,
        ).prepare(CounterEvidenceRequest(
            folder, cycle, now, selection, selected,
        ), runtime)
        floor_plan, counter_payload, scope_downgraded, floor_requirement = (
            evidence_floor.plan, evidence_floor.counter_payload,
            evidence_floor.scope_downgraded, evidence_floor.writing_requirement,
        )
        experiment_requirement = experiment_instruction(experiment) if experiment is not None else None
        return _CycleEditorialPlan(
            prepared, strategy_result, evidence_floor, fingerprint, expression_kind,
        )

    async def _cycle_generate_phase(self, folder, cycle, now, goal, research, cognition, plan):
        inputs, snapshot, experiment, experiment_payload, mind_plan = (
            goal.inputs, goal.snapshot, goal.experiment,
            goal.experiment_payload, goal.mind_plan,
        )
        runtime, market_companion, existing = (
            research.runtime, research.market_companion, research.existing,
        )
        selection, selected, related, learning_result = (
            cognition.selection, cognition.selected, cognition.related,
            cognition.learning_result,
        )
        cognition_decision, belief_decision = cognition.cognition_decision, cognition.belief_decision
        worldview_update, prediction_record = cognition.worldview_update, cognition.prediction_record
        prepared, strategy_result = plan.prepared, plan.strategy_result
        evidence_floor, fingerprint, expression_kind = (
            plan.evidence_floor, plan.fingerprint, plan.expression_kind,
        )
        lesson_payload, technique_requirements = (
            prepared.lesson_payload, prepared.technique_requirements,
        )
        social_style_context, is_short_note, minimum_words, market_pulse = (
            prepared.style_context, prepared.is_short_note, prepared.minimum_words,
            prepared.market_pulse,
        )
        content_strategy, strategy_payload = strategy_result.strategy, strategy_result.strategy_payload
        mind_strategy_context = strategy_result.mind_context
        floor_plan, counter_payload = evidence_floor.plan, evidence_floor.counter_payload
        scope_downgraded, floor_requirement = (
            evidence_floor.scope_downgraded, evidence_floor.writing_requirement,
        )
        experiment_requirement = experiment_instruction(experiment) if experiment is not None else None
        from .draft_generation_service import (
            DraftGenerationPolicies, DraftGenerationRequest, DraftGenerationService,
        )
        draft_service = DraftGenerationService(
            base_url=self.base_url, activity=self._activity,
            policies=DraftGenerationPolicies(
                compact_worldview_learning=_compact_worldview_learning,
                generation_strategy_receipt=_generation_strategy_receipt,
                compact_floor=_compact_generation_evidence_floor,
                compact_evidence=_compact_generation_evidence,
                compact_related=_compact_generation_related_articles,
                market_instruction=_market_expression_instruction, title_key=_title_key,
            ),
        )
        generated = await draft_service.create(DraftGenerationRequest(
            folder, cycle, inputs, fingerprint, selection, selected, snapshot, related,
            market_companion, counter_payload, floor_plan, floor_requirement,
            scope_downgraded, expression_kind, is_short_note, minimum_words,
            market_pulse, social_style_context, lesson_payload, technique_requirements,
            experiment, experiment_payload, experiment_requirement, cognition_decision,
            belief_decision, worldview_update, prediction_record, learning_result,
            content_strategy, strategy_payload, mind_strategy_context, existing,
        ), runtime)
        artifact, brief, topics, experiment_check, text, perspective_expression = (
            generated.artifact, generated.brief, generated.topics,
            generated.experiment_check, generated.text, generated.perspective_expression,
        )
        return _CycleDraftStage(plan, generated)

    async def _cycle_finish_phase(self, folder, cycle, now, goal, research, cognition, draft):
        experiment = goal.experiment
        inputs, snapshot, strategy_sync, experiment_payload, mind_plan = (
            goal.inputs, goal.snapshot, goal.strategy_sync,
            goal.experiment_payload, goal.mind_plan,
        )
        runtime, evidence_pool, exploration, traffic_payload, existing = (
            research.runtime, research.evidence_pool, research.exploration,
            research.traffic_payload, research.existing,
        )
        market_companion = research.market_companion
        selected, selection, related = cognition.selected, cognition.selection, cognition.related
        worldview_update, prediction_record = cognition.worldview_update, cognition.prediction_record
        cognition_decision = cognition.cognition_decision
        plan, generated = draft.plan, draft.generated
        prepared, strategy_result, evidence_floor = plan.prepared, plan.strategy_result, plan.evidence_floor
        lesson, lesson_payload, lesson_id = prepared.lesson, prepared.lesson_payload, prepared.lesson_id
        technique_requirements, required_technique_indices = prepared.technique_requirements, prepared.required_indices
        is_short_note, minimum_words, expression_kind = prepared.is_short_note, prepared.minimum_words, plan.expression_kind
        strategy_payload = strategy_result.strategy_payload
        mind_strategy_context, action_prediction_payload = strategy_result.mind_context, strategy_result.action_prediction_payload
        process_skill, process_steps, retry_verification_once = (
            strategy_result.process_skill, strategy_result.process_steps,
            strategy_result.retry_verification_once,
        )
        floor_plan, counter_payload, scope_downgraded = evidence_floor.plan, evidence_floor.counter_payload, evidence_floor.scope_downgraded
        fingerprint = plan.fingerprint
        artifact, experiment_check = generated.artifact, generated.experiment_check
        text, perspective_expression = generated.text, generated.perspective_expression
        from .draft_verification_service import DraftVerificationService, VerificationRequest
        from .draft_repair_service import DraftRepairService, DraftRepairRequest
        verifier = DraftVerificationService(
            activity=self._activity, structured=_structured,
            required_applied=_required_techniques_applied,
            hypothetical_sections=_explicit_hypothetical_note_sections,
            title_key=_title_key,
        )
        verification_request = VerificationRequest(
            folder, cycle, artifact, lesson, lesson_payload, technique_requirements,
            selected, market_companion, counter_payload, related, snapshot,
            floor_plan, is_short_note, minimum_words, perspective_expression,
            cognition_decision is not None, retry_verification_once,
            process_skill, existing, selection.topic, scope_downgraded,
        )
        original_check = await verifier.verify(verification_request, runtime)
        repaired = await DraftRepairService(
            activity=self._activity, verifier=verifier,
        ).repair(DraftRepairRequest(
            generated, verification_request, inputs, fingerprint, selection,
            lesson_payload, required_technique_indices, experiment,
        ), runtime, original_check)
        artifact, experiment_check, text, perspective_expression = (
            repaired.artifact, repaired.experiment_check, repaired.text,
            repaired.perspective_expression,
        )
        outcome = repaired.verification
        check, floor_check, passes = outcome.check, outcome.floor_check, outcome.passed
        application_valid, support_valid, support_exempt, grounding_valid = (
            outcome.application_valid, outcome.support_valid,
            outcome.support_exempt, outcome.grounding_valid,
        )
        verification_attempts = original_check.attempts + (
            outcome.attempts if repaired.repair_attempts else 0
        )
        draft_repair_attempts = repaired.repair_attempts
        experiment_eligible = experiment_check is None or experiment_check['status'] == 'PASS'
        self._activity(cycle, 'CREATE', 'I finished the publication checks',
                       ('All required checks passed; the draft can proceed.' if passes else 'One or more checks failed; the draft will be held.'),
                       status='completed' if passes else 'stopped')
        from .draft_outcome_service import DraftOutcomeService, DraftOutcomeRequest
        saved, experience = DraftOutcomeService(
            inbox=self.inbox, runtime=self.runtime,
        ).complete(DraftOutcomeRequest(
            folder, cycle, inputs, artifact, fingerprint, lesson_id, lesson_payload,
            check, passes, process_skill, verification_attempts, draft_repair_attempts,
            experiment_payload, experiment_check, strategy_payload,
            action_prediction_payload, mind_strategy_context, strategy_sync,
            floor_plan, floor_check, technique_requirements, required_technique_indices,
            application_valid, support_valid, support_exempt, grounding_valid,
            perspective_expression, expression_kind, minimum_words, text,
            traffic_payload, mind_plan, worldview_update, prediction_record,
            cognition_decision, exploration, evidence_pool, selected, market_companion,
        ))
        result = saved
        from .content_cycle_completion import ContentCycleCompletion
        completion = ContentCycleCompletion(
            publisher=self.publisher, mind=self.mind, memory_life=self.memory_life,
            activity=self._activity,
            learning_transfer=getattr(self, 'learning_transfer', None),
        )
        return completion.complete(
            result, artifact=artifact, experience=experience, folder=folder,
            cycle=cycle, topic=selection.topic, passed=bool(passes),
            evidence_id=selected['evidence_id'], evidence_refs=selected['evidence_refs'],
            runtime_calls=runtime.index, process_steps=process_steps,
            failure_mode=str(floor_check.status or 'content_checks_failed'),
            observed_at=now,
        )
    async def _run(self, folder, cycle, now):
        goal = await self._cycle_goal_phase(folder, cycle, now)
        if isinstance(goal, dict):
            return goal
        research = await self._cycle_research_phase(folder, cycle, now, goal)
        if isinstance(research, dict):
            return research
        cognition = await self._cycle_cognition_phase(folder, cycle, now, goal, research)
        if isinstance(cognition, dict):
            return cognition
        editorial = await self._cycle_editorial_phase(folder, cycle, now, goal, research, cognition)
        if isinstance(editorial, dict):
            return editorial
        draft = await self._cycle_generate_phase(folder, cycle, now, goal, research, cognition, editorial)
        return await self._cycle_finish_phase(folder, cycle, now, goal, research, cognition, draft)
