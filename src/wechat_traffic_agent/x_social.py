"""Autonomous X observation, expression and autobiographical lineage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_loop import ReceiptRuntime, _structured
from .public_activity import emit_public_activity
from .sources.runtime_x import first_post_id, first_user, normalize_posts
from .x_mcp import x_publication_enabled, x_writes_enabled
from .public_persona import public_persona_projection, record_public_interaction
from .x_growth_mission import follower_count_from_user, x_growth_mission_context
from .l1_decisions import FerroL1Decisions
from .x_article_media import ArticleImageSpec


class SocialAttentionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["IGNORE", "INVESTIGATE", "REPLY"]
    category: Literal["QUESTION", "REBUTTAL", "EVIDENCE", "SPAM", "REACTION", "OTHER"]
    valuable_evidence: bool
    summary: str = Field(min_length=5, max_length=400)
    reply_text: str | None = Field(default=None, max_length=280)
    rationale: str = Field(min_length=10, max_length=500)
    @model_validator(mode="after")
    def validate_reply(self) -> "SocialAttentionDecision":
        if self.action == "REPLY" and not (self.reply_text or "").strip():
            raise ValueError("REPLY requires reply_text")
        if self.action != "REPLY" and self.reply_text is not None:
            raise ValueError("reply_text is only valid for REPLY")
        return self


class XReplyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reply_text: str = Field(min_length=1, max_length=280)
    rationale: str = Field(min_length=10, max_length=500)


class XPostDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    post_text: str = Field(min_length=1, max_length=280)
    rationale: str = Field(min_length=10, max_length=500)


class XResearchPostDraft(XPostDraft):
    """Choose length while writing, after the actual research is available."""
    format: Literal["SHORT_POST", "LONG_POST", "ARTICLE"] = "SHORT_POST"
    title: str | None = Field(default=None, min_length=5, max_length=100)
    cover_caption: str | None = Field(default=None, max_length=64)
    images: tuple[ArticleImageSpec, ...] = Field(default=(), max_length=8)
    post_text: str = Field(min_length=20, max_length=8000)

    @model_validator(mode="after")
    def short_post_bounds(self):
        if self.format == "SHORT_POST" and len(self.post_text) > 280:
            raise ValueError("Use LONG_POST to preserve an explanation exceeding 280 characters")
        if self.format == "ARTICLE" and not self.title:
            raise ValueError("Native X Article requires a title")
        if self.images and self.format != "ARTICLE":
            raise ValueError("Inline images require ARTICLE")
        if sum(image.role == "COVER" for image in self.images) > 1:
            raise ValueError("An Article has one cover")
        for image in self.images:
            if image.role == "INLINE" and (not image.after_text or self.post_text.count(image.after_text) != 1):
                raise ValueError("Inline image needs a unique exact after_text passage in the article")
        return self


PUBLIC_RESEARCH_WRITING_INSTRUCTIONS = (
    "Organize for an interested reader who has not seen the research conversation. "
    "First decide what the reader will understand and which background they need; choose the form after that. "
    "Start with a concrete situation, puzzle or example that makes the question matter. "
    "Introduce necessary terms in ordinary language before using them in an inference. "
    "Develop a coherent explanation from relevant observations through a comparison, calculation or causal link. "
    "Use learning.answer and reasoning_steps as source-supported material, not as an internal diary to copy. "
    "Reorder, combine or omit research steps to make the idea easier to follow; the research chronology is not an article outline. "
    "Let readers see why evidence supports an interpretation, what supplied alternatives would predict, "
    "and which assumptions or limits change its use. When arithmetic matters, show matched inputs and the calculation. "
    "Distinguish sourced observations, inference and explicitly hypothetical teaching examples. "
    "Do not invent measurements, experiments, earlier beliefs, rejected alternatives or a change of mind. "
    "A useful partial investigation can teach a distinction or method without a final verdict or prediction. "
    "Do not make a search diary, missing-data checklist or generic caution the main payload. "
    "Use an appropriate lead, descriptive subheadings, paragraphs, lists, worked examples or comparisons "
    "where they help this particular reader; there is no fixed section order, paragraph count or template. "
    "Native X text supports plain-text headings and lists; avoid Markdown code fences, table syntax and link syntax. "
    "Supply relevant public source URLs next to claims or in a clearly identified source section. "
    "Choose SHORT_POST for one simple self-contained explanation, LONG_POST for a developed explanation, "
    "or ARTICLE for a piece whose sections and/or explanatory visuals help readers understand. "
    "Long posts and illustrated Articles are welcome; do not compress useful reasoning to fit a provisional short format. "
    "ARTICLE requires title and may have cover_caption and images. Choose images only when they explain something: "
    "a sequence, contrast, structure or sourced comparison, not generic decoration. "
    "Each image has role COVER/INLINE, mode GRAPHIC/GENERATE/SEARCH, caption and alt_text. "
    "For an INLINE image, after_text must be an exact unique passage from the article where the image belongs. "
    "GRAPHIC INLINE renders a flow diagram with 2-6 diagram_labels explaining source-supported steps; "
    "GRAPHIC COVER is a title cover. GENERATE needs prompt and is for conceptual illustrations, "
    "not exact data charts, measured results, real screenshots or evidence. SEARCH needs query for licensed Commons images. "
    "Explain the visual's significance in nearby prose, including its caption if needed; it must not replace a missing inference. "
    "Length, headings and images alone do not create value. In rationale, briefly explain why this organization "
    "and chosen format help the reader understand the specific idea. "
)


class XExpressionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["POST", "SKIP"]
    post_text: str | None = Field(default=None, max_length=280)
    rationale: str = Field(min_length=10, max_length=500)

    @model_validator(mode="after")
    def validate_post(self) -> "XExpressionDecision":
        if self.action == "POST" and not (self.post_text or "").strip():
            raise ValueError("POST requires post_text")
        if self.action == "SKIP" and self.post_text is not None:
            raise ValueError("SKIP must not contain post_text")
        return self


def _expression_model_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep publication facts once; execution receipts stay in the cycle ledger."""
    projected = dict(context)
    plan = context.get("mind_plan")
    if isinstance(plan, dict):
        projected["mind_plan"] = {key: plan[key] for key in (
            "cycle", "goal", "endogenous_goal", "required_topic", "focus_kind",
            "external_mission_id", "owner_goal_id",
        ) if key in plan}
        goal = plan.get("goal")
        if isinstance(goal, dict):
            projected["mind_plan"]["goal"] = {
                key: goal[key] for key in (
                    "goal_id", "description", "origin", "expected_value",
                    "confidence", "interest_refs", "inquiry_refs", "value_refs",
                ) if key in goal
            }
        endogenous_goal = plan.get("endogenous_goal")
        if isinstance(endogenous_goal, dict):
            projected["mind_plan"]["endogenous_goal"] = {
                key: endogenous_goal[key] for key in (
                    "goal_id", "description", "origin", "intrinsic_drives",
                    "expected_value", "confidence",
                ) if key in endogenous_goal
            }
    source = context.get("source_evidence")
    learning = context.get("learning") or {}
    if isinstance(source, dict) and source and isinstance(learning, dict):
        # Exact equality is required: revisions with the same ID may differ.
        if any(source == row for row in learning.get("sources") or []):
            projected["source_evidence"] = {
                "evidence_id": source.get("evidence_id"),
                "source_in": "learning.sources",
                "sha256": hashlib.sha256(json.dumps(
                    source, sort_keys=True, ensure_ascii=False,
                ).encode()).hexdigest(),
            }
    if isinstance(learning, dict) and learning:
        learning_projection = {key: learning[key] for key in (
            "schema_version", "cycle", "topic", "focus", "evidence_id", "facts",
            "learning_delta", "next_question", "next_query", "progress", "progressed",
            "related_proposition", "rationale", "answer", "reasoning_steps", "question_status", "inquiry_action", "resume_when",
        ) if key in learning}
        learning_projection["sources"] = [{
            **{key: row[key] for key in (
                "evidence_id", "title", "url", "source_kind",
            ) if key in row},
            "source_receipt_sha256": hashlib.sha256(json.dumps(
                row, sort_keys=True, ensure_ascii=False,
            ).encode()).hexdigest(),
        } for row in learning.get("sources") or []]
        application = learning.get("application")
        if isinstance(application, dict):
            learning_projection["application"] = {
                key: application[key] for key in (
                    "knowledge", "application", "assessment", "evidence", "brier_score",
                ) if key in application
            }
        learning_projection["projection"] = {
            "receipt_ref": "content/continuous/cycles/" + str(
                learning.get("cycle") or context.get("cycle")) + "/learning_result.json",
            "factual_authority": "exact verified facts retained; full acquired sources and comparison history remain in receipt_ref",
        }
        projected["learning"] = learning_projection
    return projected


def _expression_persona(state_root: Path) -> dict[str, Any]:
    """Voice and affiliation are inputs; interaction archives stay in persona storage."""
    persona = public_persona_projection(state_root)
    return {
        key: persona[key]
        for key in (
            "status", "name", "self_description", "role", "affiliation",
            "mission", "voice_traits", "identity_anchor",
        )
        if persona.get(key) is not None
    }


def _expression_mission(state_root: Path) -> dict[str, Any]:
    """The active experiment constrains writing; the roadmap belongs to its planner."""
    mission = x_growth_mission_context(state_root)
    projected = {key: mission[key] for key in (
        "status", "mission_id", "goal_id", "description", "metric", "target",
        "deadline", "generation_authority", "publication_authority",
    ) if key in mission}
    experiment = mission.get("active_experiment")
    if isinstance(experiment, dict):
        projected["active_experiment"] = {
            key: experiment[key] for key in (
                "experiment_id", "status", "strategy", "settlement_metric",
                "measurement_window_hours",
            ) if key in experiment
        }
    feedback = mission.get("audience_feedback")
    if isinstance(feedback, dict):
        projected["audience_feedback"] = {
            key: feedback[key]
            for key in ("action", "active", "current", "peak")
            if key in feedback
        }
    return projected


def _expression_recent_shares(history) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in history.recent_public_shares(limit=6):
        text = str(row.get("text") or "")
        compact = {
            "text": text[:480],
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text_truncated": len(text) > 480,
        }
        if row.get("recorded_at") is not None:
            compact["recorded_at"] = row["recorded_at"]
        rows.append(compact)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if re.match(r"^https://", value):
        return value
    return None


def _find_url(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("url", "public_url", "canonical_url", "route"):
            found = _safe_url(value.get(key))
            if found:
                return found
        for row in value.values():
            found = _find_url(row)
            if found:
                return found
    elif isinstance(value, list):
        for row in value:
            found = _find_url(row)
            if found:
                return found
    return None
class XHistoryLedger:
    def __init__(self, state_root: Path) -> None:
        self.path = Path(state_root) / "content" / "x" / "history.jsonl"

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "schema_version": "content-agent-x-history.v1",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        _append_jsonl(self.path, row)
        return row

    def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 500)):]
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema_version") == "content-agent-x-history.v1":
                rows.append(row)
        return rows

    def recent_public_shares(self, *, limit: int = 20, max_text_chars: int = 16000) -> list[dict[str, Any]]:
        # Public text supports semantic deduplication; private execution lineage
        # is retained in the ledger and must not inflate every draft request.
        keys = ("text", "x_url", "x_post_id", "recorded_at", "cycle", "expression_kind")
        projected = []
        used = 0
        for row in reversed(self.recent(limit=limit)):
            item = {key: row[key] for key in keys if key in row}
            text = str(item.get("text") or "")
            if used + len(text) <= max_text_chars:
                used += len(text)
            else:
                item.pop("text", None)
                item.update(text_omitted_for_budget=True,
                            text_sha256=hashlib.sha256(text.encode()).hexdigest())
            projected.append(item)
        return list(reversed(projected))

    def has_event(self, *, cycle: str, event_type: str) -> bool:
        return any(row.get("cycle") == cycle and row.get("event_type") == event_type for row in self.recent(limit=300))


class XAutonomyLoop:
    def __init__(self, *, runtime: Any, model_runtime: Any, state_root: Path, base_url: str,
                 browser_mentions_observer: Any | None = None,
                 browser_publish_transport: Any | None = None,
                 growth_mission: Any | None = None,
                 social_observer: Any | None = None,
                 social_context_provider: Any | None = None,
                 l1_decisions: Any | None = None) -> None:
        self.runtime = runtime
        self.model_runtime = model_runtime
        self.browser_mentions_observer = browser_mentions_observer
        self.browser_publish_transport = browser_publish_transport
        self.growth_mission = growth_mission
        self.social_observer = social_observer
        self.social_context_provider = social_context_provider
        self.l1_decisions = (
            l1_decisions
            if l1_decisions is not None
            else getattr(model_runtime, "l1_decisions", None)
        )
        self.state_root = Path(state_root)
        self.base_url = base_url.rstrip("/")
        self.root = self.state_root / "content" / "x"
        self.history = XHistoryLedger(self.state_root)
    def _l1(self):
        if self.l1_decisions is None:
            self.l1_decisions = FerroL1Decisions(state_root=self.state_root)
        return self.l1_decisions

    @property
    def profile_path(self) -> Path:
        return self.root / "public_profile.json"

    @property
    def social_evidence_path(self) -> Path:
        return self.root / "social_evidence.jsonl"

    @property
    def seen_path(self) -> Path:
        return self.root / "seen_mentions.json"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @property
    def publication_blocker_path(self) -> Path:
        return self.root / "publication_blocker.json"

    def _active_publication_blocker(self, *, now: datetime) -> dict[str, Any] | None:
        row = _read_json(self.publication_blocker_path)
        if str(row.get("status") or "") != "ACTIVE":
            return None
        # API credits are a provider-specific outage, not a publication-wide
        # blocker now that authenticated browser transports are available.
        if (
            str(row.get("blocker") or "") == "X_CREDITS_DEPLETED"
            or str(row.get("resource") or "") == "X_API_CREDITS"
        ):
            return None
        if str(row.get("transport") or "") != "JEV_AGENT_RUNTIME":
            _write_json(self.publication_blocker_path, {
                **row, "status": "SUPERSEDED", "superseded_at": now.isoformat(),
                "reason": "publication executor changed to JEV_AGENT_RUNTIME",
            })
            return None
        raw = str(row.get("retry_after") or "")
        try:
            retry_after = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if retry_after.tzinfo is None:
            return None
        if now.astimezone(timezone.utc) >= retry_after.astimezone(timezone.utc):
            return None
        return row

    def _record_publication_blocker(
        self,
        *,
        now: datetime,
        status: str,
        detail: str,
        receipt_id: str | None,
        resource: str = "X_PUBLICATION_TRANSPORT",
        required_action: str = "RESTORE_X_PUBLICATION_TRANSPORT",
    ) -> dict[str, Any]:
        prior = _read_json(self.publication_blocker_path)
        first_seen = prior.get("first_seen_at") or now.isoformat()
        row = {
            "schema_version": "content-agent-x-publication-blocker.v1",
            "status": "ACTIVE",
            "blocker": str(status),
            "resource": str(resource),
            "required_action": str(required_action),
            "first_seen_at": first_seen,
            "last_seen_at": now.isoformat(),
            "retry_after": (now + timedelta(seconds=60)).isoformat(),
            "transport": "JEV_AGENT_RUNTIME",
            "receipt_id": receipt_id,
            "detail": str(detail)[:400],
        }
        _write_json(self.publication_blocker_path, row)
        return row

    def _clear_publication_blocker(self, *, now: datetime, receipt_id: str | None) -> None:
        if not self.publication_blocker_path.exists():
            return
        prior = _read_json(self.publication_blocker_path)
        _write_json(self.publication_blocker_path, {
            **prior,
            "status": "CLEARED",
            "cleared_at": now.isoformat(),
            "clearing_receipt_id": receipt_id,
        })

    def _write_status(self, *, now: datetime, status: str, detail: str = "") -> None:
        _write_json(self.status_path, {
            "schema_version": "content-agent-x-status.v1", "as_of": now.isoformat(),
            "status": status, "detail": detail[:400],
        })

    def _activity(self, *, cycle: str, title: str, detail: str, status: str = "completed") -> None:
        emit_public_activity(
            self.state_root, cycle=cycle, phase="X", title=title, detail=detail,
            status=status, kind="social",
        )

    async def capture_mission_publication_baseline(
        self, *, cycle: str, now: datetime
    ) -> dict[str, Any]:
        if self.growth_mission is None:
            return {"status": "NO_GROWTH_MISSION"}
        user, identity_receipt = await self._publication_identity(now=now)
        record = getattr(self.growth_mission, "record_publication_baseline", None)
        if not callable(record):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        return record(
            cycle=cycle,
            user=user,
            identity_receipt_id=identity_receipt,
            now=now,
        )

    async def observe(self, *, now: datetime, allow_remote_actions: bool = True) -> dict[str, Any]:
        cycle = "x-observe-" + str(int(now.timestamp()))
        try:
            me, identity_receipt = await self._publication_identity(now=now)
        except Exception as exc:
            self._write_status(now=now, status="X_UNAVAILABLE", detail=str(exc))
            return {"status": "X_UNAVAILABLE", "error_type": type(exc).__name__, "message": str(exc)[:300]}
        growth_result: dict[str, Any] | None = None
        if self.growth_mission is not None:
            try:
                growth_result = await self.growth_mission.observe_identity(
                    user=me,
                    identity_receipt_id=identity_receipt,
                    now=now,
                )
            except Exception as exc:
                growth_result = {
                    "status": "DEGRADED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                }
        self._activity(cycle=cycle, title="Scanning X", detail="Checking new mentions for questions, rebuttals, and evidence worth investigating.", status="working")
        try:
            observer = self.browser_mentions_observer
            if observer is not None:
                raw_mentions = await observer.observe(now=now, max_results=20)
                discovery = {"source": "MENTIONS", "authority": "ATTENTION_ONLY"}
            else:
                discovery = self._l1().choose_x_discovery(
                    mission=x_growth_mission_context(self.state_root),
                    persona=public_persona_projection(self.state_root),
                    previous={key:value for key,value in _read_json(self.root/"discovery.json").items()
                              if key in {"decision","observed_at","post_count"}})
                from .x_browser import XBrowserTransportRouter
                transport = self.browser_publish_transport or XBrowserTransportRouter(state_root=self.state_root)
                if discovery["source"] == "SEARCH":
                    from .x_discovery import plan_x_search
                    previous = _read_json(self.root / "discovery.json")
                    search_folder = self.root / "discovery-cycles" / cycle
                    attempts = []
                    # One observation task permits three autonomous searches, with real feedback
                    # between attempts. A transport/model failure still fails this observation.
                    for attempt in range(1, 4):
                        attempt_folder = search_folder if attempt == 1 else search_folder / f"attempt-{attempt}"
                        search = await plan_x_search(
                            runtime=self.model_runtime, state_root=self.state_root,
                            folder=attempt_folder,
                            mission=x_growth_mission_context(self.state_root),
                            persona=public_persona_projection(self.state_root),
                            previous=previous,
                        )
                        discovery = {**discovery, **search.model_dump(mode="json")}
                        raw_mentions = await transport.discover(source="SEARCH", query=search.query, max_results=20)
                        feedback = {
                            "attempt": attempt, "decision": discovery,
                            "post_count": len(raw_mentions), "posts": raw_mentions,
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                        }
                        _write_json(attempt_folder / "search_observation.json", feedback)
                        attempts.append({"attempt": attempt, **search.model_dump(mode="json"),
                                         "post_count": len(raw_mentions)})
                        if raw_mentions:
                            break
                        previous = feedback
                    discovery = {**discovery, "search_attempts": attempts}
                else:
                    raw_mentions = await transport.discover(source=discovery["source"], max_results=20)
            mentions = normalize_posts(raw_mentions, observed_at=now)
            mentions_receipt_id = f"x-jev-discovery:{cycle}"
            _write_json(self.root / "discovery.json", {"cycle": cycle, "observed_at": now.isoformat(),
                "decision": discovery, "posts": raw_mentions, "post_count":len(raw_mentions), "receipt_id": mentions_receipt_id})
        except Exception as exc:
            self._write_status(now=now, status="X_OBSERVATION_FAILED", detail=str(exc))
            return {"status":"X_OBSERVATION_FAILED", "detail":str(exc)[:300], "x_growth_mission":growth_result}
        seen_payload = _read_json(self.seen_path)
        seen = {str(value) for value in seen_payload.get("ids", []) if str(value)}
        fresh = [row for row in mentions if row["evidence_id"].removeprefix("x-post:") not in seen]
        handled = 0
        failures = []
        for row in fresh[:10]:
            post_id = row["evidence_id"].removeprefix("x-post:")
            try:
                completed = await self._handle_mention(row=row, post_id=post_id, me=me, cycle=cycle, now=now,
                    allow_remote_actions=allow_remote_actions)
                if completed is False:
                    if allow_remote_actions:
                        failures.append({"post_id":post_id,"status":"REPLY_NOT_COMMITTED"})
                    continue
                seen.add(post_id)
                handled += 1
            except Exception as exc:
                failures.append({"post_id":post_id,"error_type":type(exc).__name__,"detail":str(exc)[:300]})
        _write_json(self.seen_path, {"schema_version": "content-agent-x-seen.v1", "ids": sorted(seen)[-2000:]})
        replies = [
            {key: row.get(key) for key in ("x_post_id", "x_url", "target_post_id", "runtime_receipt_id")}
            for row in self.history.recent(limit=40)
            if row.get("cycle") == cycle and row.get("event_type") == "REPLY"
            and row.get("publication_verified") and row.get("runtime_receipt_id")
        ]
        final_status = "DEGRADED" if failures else "OK"
        self._write_status(now=now, status=final_status)
        self._activity(cycle=cycle, title="Finished checking X", detail=f"Reviewed {len(mentions)} recent mention(s); {handled} were new.")
        return {
            "status": final_status, "identity_receipt_id": identity_receipt, "mentions_receipt_id": mentions_receipt_id,
            "mention_count": len(mentions), "new_mentions": handled, "discovery": discovery, "failures": failures,
            "discovery_outcome": "OBSERVED_POSTS" if mentions else "EMPTY_RESULTS",
            "x_growth_mission": growth_result,
            "reply_count": len(replies), "replies": replies,
            "engagement_outcome": (
                "REPLIED" if replies else "READ_ONLY" if not allow_remote_actions
                else "BLOCKED" if failures else "NO_REPLY_SELECTED"
            ),
        }

    async def _handle_mention(self, *, row: dict[str, Any], post_id: str, me: dict[str, Any], cycle: str, now: datetime,
                              allow_remote_actions: bool = True) -> None:
        self._activity(cycle=cycle, title="Reading a public X post", detail=row.get("summary", "")[:220])
        folder = self.root / "social-model-calls" / cycle / post_id
        model_runtime = ReceiptRuntime(self.model_runtime, folder)
        actor = str(row.get("x_author_id") or row.get("username") or "").strip()
        relationship_context = None
        provider = self.social_context_provider
        if callable(provider) and actor:
            candidate = provider(actor)
            if isinstance(candidate, dict):
                relationship_context = {
                    key: candidate.get(key)
                    for key in (
                        "actor",
                        "interactions",
                        "familiarity",
                        "valence",
                        "approach_bias",
                        "last_category",
                        "last_signal",
                        "last_seen_at",
                    )
                    if candidate.get(key) is not None
                }
        l1_attention = self._l1().choose_social_attention(
            mention=row,
            relationship=relationship_context,
            mission=x_growth_mission_context(self.state_root),
        )
        _write_json(folder / "l1_social_attention.json", l1_attention)
        action = str(l1_attention["action"])
        category = str(l1_attention["category"])
        valuable_evidence = bool(l1_attention["valuable_evidence"])
        summary = str(row.get("summary") or "Public X mention").strip()
        if len(summary) < 5:
            summary = "Public X mention: " + summary
        reply_text = None
        rationale = (
            f"Jev L1 routed this mention to {action} as {category}; "
            "no reply wording was generated."
        )
        if action == "REPLY":
            prompt = (
                "X_REPLY_DRAFT: The bounded JEV L1 router has already decided to REPLY. "
                "Write only the reply wording; do not reconsider IGNORE/INVESTIGATE/REPLY. "
                "Keep it <=280 characters. Use only the supplied public mention and durable public persona. "
                "Keep factual claims grounded in the supplied evidence; do not invent observations. "
                "If the mention asserts an unverified fact, phrase the reply so it does not adopt that fact as true. Return JSON only.\n"
                + json.dumps({
                    "mention": row,
                    "agent_handle": me.get("username"),
                    "public_persona": _expression_persona(self.state_root),
                    "relationship_context": relationship_context,
                    "l1_attention": l1_attention,
                }, ensure_ascii=False)
            )
            drafted = await _structured(model_runtime, prompt, XReplyDraft)
            reply_text = drafted.reply_text
            rationale = drafted.rationale
        decision = SocialAttentionDecision(
            action=action,
            category=category,
            valuable_evidence=valuable_evidence,
            summary=summary[:400],
            reply_text=reply_text,
            rationale=rationale[:500],
        )
        if decision.category != "SPAM":
            record_public_interaction(
                state_root=self.state_root, source="X", message=str(row.get("summary") or ""),
                category=decision.category, source_ref=post_id, observed_at=now, summary=decision.summary,
            )
            observer = self.social_observer
            if callable(observer) and actor:
                observer(
                    actor=actor,
                    category="SOCIAL",
                    signal=decision.category,
                    now=now,
                )
        if decision.valuable_evidence:
            _append_jsonl(self.social_evidence_path, {
                "schema_version": "content-agent-x-social-evidence.v1",
                "observed_at": now.isoformat(), "status": "DISCOVERED",
                "post_id": post_id, "url": row.get("url"), "summary": decision.summary,
                "category": decision.category, "source_text": row.get("summary", "")[:1000],
                "evidence_refs": row.get("evidence_refs", ()),
            })
            self._activity(cycle=cycle, title="Investigating a public claim", detail=decision.summary)
        if decision.action == "REPLY" and decision.reply_text:
            if not allow_remote_actions or not x_writes_enabled():
                return False
            from .x_browser import XBrowserTransportRouter
            transport = self.browser_publish_transport or XBrowserTransportRouter(state_root=self.state_root)
            reply = await transport.reply(text=decision.reply_text, target_url=str(row.get("url") or ""))
            receipt_id = f"x-jev-reply:{post_id}:{reply.post_id}"
            self.history.append({
                "event_type":"REPLY", "cycle":cycle, "cognition":decision.model_dump(mode="json"),
                "expression_kind":"SOCIAL_REPLY", "text":decision.reply_text,
                "x_post_id":reply.post_id, "x_url":reply.url, "target_post_id":post_id,
                "target_user_id":row.get("x_author_id"), "runtime_receipt_id":receipt_id,
                "publication_verified":True, "publication_transport":reply.provider,
                "source_evidence_refs":list(row.get("evidence_refs", ())),
                "lineage":{"source":"public_x_discovery","attention":decision.category,
                           "future_cognition":bool(decision.valuable_evidence)},
            })
            self._activity(cycle=cycle,title="Replied on X",detail=decision.reply_text)
        return True

    def _l1_x_strategy(
        self,
        *,
        folder: Path,
        context: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        social_payload = _read_json(folder / "social_expression_drafts.json")
        strategy = (
            social_payload.get("x_strategy")
            if isinstance(social_payload.get("x_strategy"), dict)
            else {}
        )
        if (
            strategy
            and str(strategy.get("schema_version") or "")
            == "ferro-l1-x-strategy.v1"
        ):
            return dict(strategy)
        strategy_path = folder / "x_l1_strategy.json"
        persisted = _read_json(strategy_path)
        if (
            persisted
            and str(persisted.get("schema_version") or "")
            == "ferro-l1-x-strategy.v1"
        ):
            return persisted

        l1 = self._l1()
        evidence = (
            social_payload.get("evidence_decision")
            if isinstance(social_payload.get("evidence_decision"), dict)
            else {
                "resolution": {
                    "action": "DRAFT",
                    "rationale": (
                        "Persisted cognition has already been admitted to the "
                        "mission-bound X expression lane."
                    ),
                    "useful_facts": [],
                }
            }
        )
        from .social_performance import x_social_performance_context

        strategy = l1.choose_x_strategy(
            cycle=str(context.get("cycle") or folder.name),
            cognition=(
                context.get("cognition")
                if isinstance(context.get("cognition"), dict)
                else {}
            ),
            evidence=evidence,
            mission=x_growth_mission_context(self.state_root),
            owner_goal=(context.get("mind_plan") or {}).get("goal") or {},
            performance=x_social_performance_context(
                state_root=self.state_root,
                as_of=now,
            ),
        )
        _write_json(strategy_path, strategy)
        return strategy

    def _cycle_context(self, result: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
        cycle = str(result.get("cycle") or "")
        if not cycle:
            return None, {}
        folder = self.state_root / "content" / "continuous" / "cycles" / cycle
        cognition = _read_json(folder / "cognition.json")
        worldview = _read_json(folder / "worldview_update.json")
        prediction = _read_json(folder / "prediction.json")
        plan = _read_json(folder / "mind_plan.json")
        draft = _read_json(Path(str(result.get("draft_path")))) if result.get("draft_path") else {}
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        publication = result.get("publication") if isinstance(result.get("publication"), dict) else {}
        from .x_owner_execution import owner_x_publication_request
        owner_request = owner_x_publication_request(folder)
        x_expression = _read_json(folder / "x_expression_context.json") if owner_request else {}
        return folder, {
            "owner_publication_request": owner_request,
            "source_evidence": x_expression.get("source_evidence") or {},
            "learning": _read_json(folder / "learning_result.json"),
            "cycle": cycle, "cognition": cognition, "worldview": worldview, "prediction": prediction,
            "mind_plan": plan, "artifact": artifact, "publication": publication,
        }
    async def after_content_cycle(self, *, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        if not x_publication_enabled():
            return {"status": "WRITE_DISABLED"}
        folder, context = self._cycle_context(result)
        if folder is None:
            return {"status": "NO_CYCLE"}
        await self._publish_prediction_outcome(folder=folder, context=context, now=now)
        cognition = context.get("cognition") or {}
        cycle = str(context.get("cycle") or "")
        mission_binding = None
        if self.growth_mission is not None:
            binding_method = getattr(self.growth_mission, "publication_binding", None)
            if callable(binding_method):
                mission_binding = binding_method(cycle=cycle)
        result_status = str(result.get("status") or "").upper()
        owner_directed_x = bool(context.get("owner_publication_request")) and result_status == "X_EXPRESSION_READY"
        x_only_admitted = (
            isinstance(mission_binding, dict)
            and bool(mission_binding.get("bound"))
            and str(mission_binding.get("status") or "") == "BOUND_X_EXPRESSION"
        )
        if (
            (result_status != "PUBLISHED" and not x_only_admitted and not owner_directed_x)
            or not cognition.get("worth_saying")
        ):
            return {"status": "NO_EXPRESSION", "cycle": cycle}
        if self.history.has_event(cycle=cycle, event_type="EXPRESSION"):
            return {"status": "ALREADY_EXPRESSED", "cycle": cycle}
        from .learning_expression_quality import learning_required_for_sharing
        if not context.get("learning") and learning_required_for_sharing(self.state_root):
            return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle,
                    "reason": "NO_VERIFIED_LEARNING_RECORD"}
        owner_surface=str((context.get("owner_publication_request") or {}).get("decision",{}).get("publication_surface") or "")
        if owner_directed_x and owner_surface in {"X_LONG_POST","X_ARTICLE"} and not context.get("learning"):
            return await self._owner_longform(folder=folder,context=context,now=now,surface=owner_surface)
        blocker = self._active_publication_blocker(now=now)
        if blocker is not None:
            return {
                "status": "X_RESOURCE_BLOCKED",
                "cycle": cycle,
                "blocker": blocker,
                "retry_after": blocker.get("retry_after"),
            }
        if self.history.has_event(cycle=cycle, event_type="EXPRESSION"):
            return {"status": "ALREADY_EXPRESSED", "cycle": cycle}
        context["x_mission_binding"] = mission_binding
        x_strategy = self._l1_x_strategy(
            folder=folder,
            context=context,
            now=now,
        )
        if not owner_directed_x and (
            str(x_strategy.get("post_timing") or "") == "HOLD"
            or str(x_strategy.get("interaction_mode") or "ORIGINAL") != "ORIGINAL"
        ):
            return {
                "status": "SKIPPED",
                "cycle": cycle,
                "reason": "L1_X_STRATEGY",
                "x_strategy": x_strategy,
            }
        if (
            not owner_directed_x
            and not context.get("learning")
            and x_strategy
            and str(x_strategy.get("x_format") or "SHORT_POST") != "SHORT_POST"
            and not self.history.has_event(cycle=cycle, event_type="EXPRESSION")
        ):
            surface = "X_ARTICLE" if x_strategy["x_format"] == "ARTICLE" else "X_LONG_POST"
            context["x_l1_strategy"] = x_strategy
            return await self._owner_longform(folder=folder, context=context, now=now, surface=surface)
        model_runtime = ReceiptRuntime(self.model_runtime, self.root / "expression-model-calls" / cycle)
        public_url = _find_url(context.get("publication"))
        x_mission = _expression_mission(self.state_root)
        prompt = (
            "X_EXPRESSION_DRAFT: "
            + ("The durable owner controller explicitly requested an original SHORT_POST on X in this cycle. L1 timing remains advisory for this execution request. "
               if owner_directed_x else "Jev L1 has already selected POST_NOW and SHORT_POST. ")
            +             "Write only the post text; do not reconsider whether to POST or SKIP and do not choose another format. "
            "The post must arise from the supplied cognition, not invent a new topic. For LONGFORM/RESEARCH_NOTE/METHOD_NOTE, a concise takeaway plus the supplied public URL is useful when one exists. "
            "Share what you actually learned: use learning.answer to explain a mechanism, useful step, "
            "distinction or implication with concrete support, rather than listing a method name or isolated fact. "
            "Use learning.sources as attributed factual input alongside source_evidence. Preserve concrete names, measurements or implementation details when supplied. "
            "Do not compress the discovery into a generic moral such as 'agents need measurable outcomes'. "
            "Compare recent_shares by meaning, not wording; a short post must retain the new detail that distinguishes this step. "
            "For METHOD_NOTE, describe the method/tool only as a candidate being tested unless adoption is separately proven. "
            "For a worldview formation or revision, leave a clear public trace of what changed and uncertainty. "
            "Preserve the supplied public_persona voice and LivingRuntime affiliation. When relevant, show what persistent-agent continuity enables, but do not force promotion into unrelated market observations. "
            "x_growth_mission and x_l1_strategy are strategy context, not factual evidence. "
            "Keep factual claims grounded in the supplied evidence and preserve uncertainty. Max 280 characters including any URL. Return JSON only.\n"
            + json.dumps({**_expression_model_context(context), "public_url": public_url,
                          "recent_shares": _expression_recent_shares(self.history),
                          "public_persona": _expression_persona(self.state_root),
                          "x_growth_mission": x_mission,
                          "x_l1_strategy": x_strategy}, ensure_ascii=False)
        )
        _write_json(folder / "x_expression_context_projection.json", {
            "schema_version": "ferro-x-expression-context-projection.v1",
            "source_context_chars": len(json.dumps(context, ensure_ascii=False)),
            "projected_context_chars": len(json.dumps(_expression_model_context(context), ensure_ascii=False)),
            "prompt_chars": len(prompt),
            "source_policy": "EXACT_VERIFIED_FACTS_AND_SOURCE_METADATA; FULL_RESEARCH_IN_CYCLE_RECEIPT",
            "plan_policy": "CURRENT_GOAL_AND_TOPIC; CANDIDATES_RETAINED_IN_MIND_PLAN_RECEIPT",
        })
        draft_contract = XPostDraft
        if context.get("learning"):
            draft_contract = XResearchPostDraft
            prompt = prompt.replace(
                "Write only the post text; do not reconsider whether to POST or SKIP and do not choose another format.",
                "Write the post; do not reconsider whether to POST or SKIP. Any earlier format selection is provisional."
            ).replace(
                "Max 280 characters including any URL.",
                "Return format SHORT_POST (up to 280 characters), LONG_POST or ARTICLE (up to 8000). "
                "For ARTICLE include title, optional cover_caption and images. "
                "Choose the space needed for a useful explanation; do not pad."
            )
            prompt = prompt.replace(
                "The durable owner controller explicitly requested an original SHORT_POST on X in this cycle.",
                "The durable owner controller requested an original research post on X; explain the supplied learning."
            ).replace(
                "Jev L1 has already selected POST_NOW and SHORT_POST.",
                "Jev L1 selected POST_NOW; the research determines the space needed for its explanation."
            ).replace(
                "For LONGFORM/RESEARCH_NOTE/METHOD_NOTE, a concise takeaway plus the supplied public URL is useful when one exists.",
                "Make the explanation understandable in the post itself; a takeaway and source link do not replace it."
            )
            prompt += "\n" + PUBLIC_RESEARCH_WRITING_INSTRUCTIONS

        decision = await _structured(model_runtime, prompt, draft_contract,
                                     max_tokens=6000 if context.get("learning") else 2400)
        if context.get("learning") and decision.format in {"LONG_POST", "ARTICLE"}:
            return await self._owner_longform(
                folder=folder, context=context, now=now, surface="X_ARTICLE" if decision.format == "ARTICLE" else "X_LONG_POST",
                prepared_draft=decision,
            )
        if context.get("learning"):
            from .learning_expression_quality import review_learning_expression
            quality_runtime = ReceiptRuntime(self.model_runtime, self.root / "expression-model-calls" / cycle / "learning-quality")
            for attempt in range(2):
                quality = await review_learning_expression(runtime=quality_runtime, folder=folder,
                    learning=context["learning"], text=decision.post_text,
                    recent_shares=_expression_recent_shares(self.history),
                    label=f"x_learning_quality_{attempt}", format_name=decision.format)
                if quality["passed"]:
                    break
                if attempt == 0:
                    decision = await _structured(model_runtime,
                        prompt + "\nREWRITE_LEARNING_EXPRESSION: The independent final-text check rejected the draft. "
                        "Retain a specific supported detail from learning.facts, with source attribution; preserve uncertainty. "
                        "Explain why the detail matters or how it works; a method name or isolated statistic is insufficient. "
                        "Do not substitute a validation slogan for the discovery.\n"
                        + json.dumps({"rejected_post": decision.post_text, "review": quality}, ensure_ascii=False),
                        draft_contract, max_tokens=6000)
                    if context.get("learning") and decision.format in {"LONG_POST", "ARTICLE"}:
                        return await self._owner_longform(
                            folder=folder, context=context, now=now, surface="X_ARTICLE" if decision.format == "ARTICLE" else "X_LONG_POST",
                            prepared_draft=decision,
                        )
            if not quality["passed"]:
                return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle, "quality": quality}
        # Persist the exact short-post draft before any browser action. If
        # submission succeeds but verification is interrupted, CHECK_X can
        # reconcile this same text through the durable X transaction receipt
        # without calling the writing model again.
        existing_draft = _read_json(folder / "social_expression_drafts.json")
        persisted_strategy = dict(x_strategy)
        if owner_directed_x:
            persisted_strategy["post_timing"] = "POST_NOW"
            persisted_strategy["interaction_mode"] = "ORIGINAL"
            persisted_strategy["selection_authority"] = "FERRO_OWNER_EPISODE"
        _write_json(folder / "social_expression_drafts.json", {
            **existing_draft,
            "status": "DRAFT_READY",
            "cycle": cycle,
            "drafts": {
                "x": {
                    "channel": "X",
                    "format": "SHORT_POST",
                    "intent": str(cognition.get("expression_kind") or "THOUGHT"),
                    "text": decision.post_text,
                    "title": None,
                    "cover_caption": None,
                    "images": [],
                    "style_observation_ids": [],
                    "rationale": decision.rationale[:400],
                }
            },
            "x_strategy": persisted_strategy,
        })
        me, _identity_receipt = await self._publication_identity(now=now)
        baseline = None
        if self.growth_mission is not None and isinstance(mission_binding, dict) and mission_binding.get("bound"):
            try:
                baseline = self.growth_mission.record_publication_baseline(
                    cycle=cycle, user=me, identity_receipt_id=_identity_receipt, now=now,
                )
            except Exception as exc:
                baseline = {"status": "MEASUREMENT_FAILED", "error_type": type(exc).__name__, "detail": str(exc)}
            _write_json(folder / "x_publication_measurement.json", baseline)
        try:
            published = await self._publish_through_browser(text=decision.post_text)
        except Exception as exc:
            from .x_browser import XBrowserPublicationUnverified
            status = (
                "PUBLICATION_UNVERIFIED" if isinstance(exc, XBrowserPublicationUnverified)
                else "X_PUBLICATION_CAPABILITY_UNAVAILABLE"
            )
            blocker = self._record_publication_blocker(
                now=now, status=status, detail=str(exc), receipt_id=None,
            )
            return {
                "status": status, "cycle": cycle, "blocker": blocker,
                "transport": "JEV_AGENT_RUNTIME", "transport_failover": None,
                "error_type": type(exc).__name__, "detail": str(exc)[:400],
            }
        post_id = str(published.post_id).strip()
        publication_url = str(published.url).strip()
        publication_transport = str(published.provider)
        publication_receipt_id = f"x-browser-publication:{cycle}:{post_id}"
        transport_failover = None
        if not post_id:
            return {
                "status": "X_POST_ID_MISSING",
                "cycle": cycle,
                "receipt_id": publication_receipt_id,
                "transport": publication_transport,
                "transport_failover": transport_failover,
            }
        mission_experiment_transition: dict[str, Any] | None = None
        if self.growth_mission is not None:
            try:
                mission_experiment_transition = (
                    self.growth_mission.start_experiment_from_publication(
                        cycle=cycle,
                        publication_receipt_id=publication_receipt_id,
                        post_id=post_id,
                        now=now,
                    )
                )
            except Exception as exc:
                mission_experiment_transition = {
                    "status": "DEGRADED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                }
        self._clear_publication_blocker(now=now, receipt_id=publication_receipt_id)
        handle = str(me.get("username") or "").lstrip("@")
        url = publication_url or (
            f"https://x.com/{handle}/status/{post_id}" if handle and post_id else None
        )
        worldview = context.get("worldview") or {}
        plan = context.get("mind_plan") or {}
        row = self.history.append({
            "event_type": "EXPRESSION", "cycle": cycle,
            "cognition": cognition, "expression_kind": cognition.get("expression_kind"),
            "text": decision.post_text, "x_post_id": post_id, "x_url": url,
            "runtime_receipt_id": publication_receipt_id,
            "worldview": worldview,
            "prediction": context.get("prediction"),
            "inquiry_refs": list((plan.get("goal") or {}).get("inquiry_refs") or ()) if isinstance(plan.get("goal"), dict) else [],
            "source_evidence_refs": list((context.get("artifact") or {}).get("source_refs") or ()),
            "lineage": {
                "cycle": cycle, "cognition_file": "cognition.json", "worldview_file": "worldview_update.json",
                "expression_decision": decision.model_dump(mode="json"), "future_cognition": True,
                "external_mission_id": x_mission.get("mission_id"),
                "mission_experiment_id": (
                    (x_mission.get("active_experiment") or {}).get("experiment_id")
                    if isinstance(x_mission.get("active_experiment"), dict)
                    else None
                ),
                "mission_experiment_transition": mission_experiment_transition,
                "publication_transport": publication_transport,
                "transport_failover": transport_failover,
            },
        })
        self._update_recent_public_posts(row)
        label = str(cognition.get("expression_kind") or "THOUGHT").replace("_", " ").lower()
        self._activity(cycle=cycle, title="Posted a thought", detail=f"Published {label} on X: {decision.post_text}")
        return {
            "status": "POSTED",
            "cycle": cycle,
            "post_id": post_id,
            "url": url,
            "receipt_id": publication_receipt_id,
            "content_sha256": hashlib.sha256(decision.post_text.encode("utf-8")).hexdigest(),
            "text": decision.post_text,
            "transport": publication_transport,
            "transport_failover": transport_failover,
            "mission_experiment_transition": mission_experiment_transition,
            "mission_baseline": baseline,
            "owner_execution_request": context.get("owner_publication_request") or None,
        }

    async def _owner_longform(self, *, folder, context, now, surface, prepared_draft=None):
        """Execute Ferro's selected X format without a website publication prerequisite."""
        from .social_expression import ChannelSocialDraft
        from .x_browser import XBrowserPublisher
        cycle=folder.name
        fmt="ARTICLE" if surface=="X_ARTICLE" else "LONG_POST"
        if self.history.has_event(cycle=cycle,event_type="EXPRESSION"):
            return {"status":"ALREADY_EXPRESSED","cycle":cycle}
        from .learning_expression_quality import learning_required_for_sharing
        if not context.get("learning") and learning_required_for_sharing(self.state_root):
            return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle,
                    "reason": "NO_VERIFIED_LEARNING_RECORD"}
        path=folder/"social_expression_drafts.json"
        if prepared_draft is not None:
            draft = ChannelSocialDraft(
                channel="X", format=fmt, intent="EXPLAINER", text=prepared_draft.post_text,
                title=prepared_draft.title, cover_caption=prepared_draft.cover_caption, images=prepared_draft.images,
                style_observation_ids=(), rationale=prepared_draft.rationale[:400],
            )
            _write_json(path, {
                "status": "DRAFT_READY", "cycle": cycle, "drafts": {"x": draft.model_dump(mode="json")},
                "x_strategy": {"schema_version": "ferro-research-format.v1", "post_timing": "POST_NOW",
                    "x_format": fmt, "interaction_mode": "ORIGINAL", "selection_authority": "FERRO_LEARNING_EXPRESSION"},
                "evidence_decision": {"source_evidence": context.get("source_evidence")},
            })
        existing=_read_json(path)
        if existing.get("status")!="DRAFT_READY" or (existing.get("drafts",{}).get("x",{}).get("format") != fmt):
            prompt=("X_OWNER_LONGFORM_DRAFT: Ferro selected "+fmt+" through its persisted autonomous publication decision. "
                "Write in the supplied public persona from persisted cognition and attributed source only. "
                "Share the actual learning_delta and concrete observations from learning, with attribution to learning.sources. "
                "Use learning.answer and reasoning_steps for the source-supported explanation or worked case. "
                "Show how observations support an inference, calculation or distinction, and where it stops. "
                "Useful intermediate reasoning is shareable without a final conclusion; keep its assumptions and limits. "
                "Give the reader "
                "a mechanism, calculation, comparison consequence or concrete step they can understand or use. "
                "Do not force every explanation to end in an unresolved forecast. "
                "Do not replace the discovery with generic evidence reminders or a repeated slogan. "
                "Return a ChannelSocialDraft JSON: channel X, format "+fmt+", title for ARTICLE (5-100 characters), "
                "optional cover_caption <=64 characters, text 20-8000 characters, intent, style_observation_ids [], rationale. "
                "Depth must follow evidence quality. A source used in a short post may support this richer explanation only when it adds useful supported context; preserve attribution and do not pad or repeat a prior post. "
                "For ARTICLE, choose images: each has role COVER or INLINE, mode GRAPHIC/GENERATE/SEARCH, alt_text, caption, "
                "prompt for actual ChatGPT image generation or query for licensed Commons image search. "
                "An empty images list uses an original graphic cover. Never claim generated art is a real product screenshot or evidence. "
                "Preserve uncertainty and source attribution. Honor an active temporary daily contract through substantive learning and sharing.\n"
                + PUBLIC_RESEARCH_WRITING_INSTRUCTIONS + "\n"
                +json.dumps({**_expression_model_context(context),"persona":_expression_persona(self.state_root),
                    "mission":_expression_mission(self.state_root),
                    "recent_posts":_expression_recent_shares(self.history)},ensure_ascii=False))
            runtime=ReceiptRuntime(self.model_runtime,self.root/"expression-model-calls"/cycle/"longform-draft"/fmt)
            draft=await _structured(runtime,prompt,ChannelSocialDraft,max_tokens=6000)
            if draft.channel!="X" or draft.format!=fmt:
                return {"status":"OWNER_FORMAT_MISMATCH","cycle":cycle}
            strategy={"schema_version":"ferro-x-owner-format-request.v1","post_timing":"POST_NOW",
                "x_format":fmt,"interaction_mode":"ORIGINAL","selection_authority":("FERRO_OWNER_EPISODE" if context.get("owner_publication_request") else "FERRO_L1_STRATEGY"),
                "owner_request":context.get("owner_publication_request")}
            _write_json(path,{"status":"DRAFT_READY","cycle":cycle,"drafts":{"x":draft.model_dump(mode="json")},
                "x_strategy":strategy,"evidence_decision":existing.get("evidence_decision") or {"source_evidence":context.get("source_evidence"),
                    "resolution":{"action":"DRAFT","useful_facts":[str((context.get("source_evidence") or {}).get("summary") or "")]}}})
        if context.get("learning"):
            from .learning_expression_quality import review_learning_expression
            quality_runtime = ReceiptRuntime(self.model_runtime, self.root / "expression-model-calls" / cycle / "longform-quality" / fmt)
            for attempt in range(2):
                saved = _read_json(path)
                draft = ChannelSocialDraft.model_validate(saved["drafts"]["x"])
                quality = await review_learning_expression(runtime=quality_runtime, folder=folder,
                    learning=context["learning"], text=(draft.title or "") + "\n" + draft.text,
                    recent_shares=_expression_recent_shares(self.history),
                    label=f"x_longform_learning_quality_{attempt}", format_name=fmt,
                    images=[image.model_dump(mode="json") for image in draft.images])
                if quality["passed"]:
                    break
                if attempt == 0:
                    repair_runtime = ReceiptRuntime(self.model_runtime, self.root / "expression-model-calls" / cycle / "longform-repair" / fmt)
                    draft = await _structured(repair_runtime,
                        "REWRITE_LEARNING_EXPRESSION: Repair this rejected research sharing draft as ChannelSocialDraft JSON. "
                        "Keep channel X and format " + fmt + ". Preserve source attribution, the concrete discovery and uncertainty. "
                        "Do not pad the text or substitute generic validation reminders.\n"
                        + PUBLIC_RESEARCH_WRITING_INSTRUCTIONS + "\n"
                        + json.dumps({"draft": draft.model_dump(mode="json"), "review": quality,
                                      "learning": _expression_model_context(context)["learning"]}, ensure_ascii=False),
                        ChannelSocialDraft,max_tokens=6000)
                    if draft.channel != "X" or draft.format != fmt:
                        return {"status": "OWNER_FORMAT_MISMATCH", "cycle": cycle}
                    saved["drafts"]["x"] = draft.model_dump(mode="json")
                    _write_json(path, saved)
            if not quality["passed"]:
                return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle, "quality": quality}
        publisher=XBrowserPublisher(state_root=self.state_root,transport=self.browser_publish_transport,
            growth_mission=self.growth_mission,mission_baseline_observer=self.capture_mission_publication_baseline)
        result=await publisher.after_content_cycle(cycle=cycle,now=now)
        publication=result.get("publication") or {}
        if result.get("status") in {"POSTED","ALREADY_POSTED"}:
            receipt_id=f"x-browser-publication:{cycle}:{publication['post_id']}"
            self._clear_publication_blocker(now=now,receipt_id=receipt_id)
            return {**result,"status":"POSTED" if result['status']=='POSTED' else "ALREADY_EXPRESSED",
                "post_id":publication["post_id"],"url":publication["url"],"receipt_id":receipt_id,
                "format":fmt,"owner_execution_request":context.get("owner_publication_request")}
        return result

    async def _publish_through_browser(self, *, text: str):
        transport = self.browser_publish_transport
        if transport is None:
            from .x_browser import XBrowserTransportRouter
            transport = XBrowserTransportRouter(state_root=self.state_root)
        return await transport.publish(text=text)

    async def _publication_identity(self, *, now: datetime):
        transport = self.browser_publish_transport
        if transport is None:
            from .x_browser import XBrowserTransportRouter
            transport = XBrowserTransportRouter(state_root=self.state_root)
        user = await transport.identity()
        cached = _read_json(self.profile_path)
        handle = str(user.get("username") or "").lstrip("@")
        if not handle:
            raise RuntimeError("Jev publication identity has no handle")
        if str(cached.get("handle") or "").casefold() == handle.casefold():
            user["id"] = str(cached.get("id") or "")
        receipt_id = f"x-browser-identity:{handle}:{now.isoformat()}"
        _write_json(self.profile_path, {
            **cached, "handle": handle, "id": str(user.get("id") or ""),
            "synced_at": now.isoformat(), "name": user.get("name") or handle,
            "public_metrics": user.get("public_metrics") or {},
            "followers_count": follower_count_from_user(user),
            "identity_receipt_id": receipt_id,
            "identity_source": "JEV_AGENT_RUNTIME",
        })
        return user, receipt_id

    def _update_recent_public_posts(self, row: dict[str, Any]) -> None:
        profile = _read_json(self.profile_path)
        recent = [value for value in profile.get("recent_posts", []) if isinstance(value, dict)]
        recent.append({
            "post_id": row.get("x_post_id"), "url": row.get("x_url"), "text": row.get("text"),
            "cycle": row.get("cycle"), "recorded_at": row.get("recorded_at"),
        })
        profile["recent_posts"] = recent[-5:]
        _write_json(self.profile_path, profile)
    async def _publish_prediction_outcome(self, *, folder: Path, context: dict[str, Any], now: datetime) -> None:
        cycle = str(context.get("cycle") or "")
        if self.history.has_event(cycle=cycle, event_type="PREDICTION_OUTCOME"):
            return
        raw = _read_json(folder / "prediction_settlements.json")
        settlements = raw if isinstance(raw, list) else []
        terminal = [row for row in settlements if isinstance(row, dict) and row.get("learning_status") == "APPLIED"]
        if not terminal:
            try:
                parsed = json.loads((folder / "prediction_settlements.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                parsed = []
            terminal = [row for row in parsed if isinstance(row, dict) and row.get("learning_status") == "APPLIED"] if isinstance(parsed, list) else []
        if not terminal:
            return
        model_runtime = ReceiptRuntime(self.model_runtime, self.root / "prediction-outcome-model-calls" / cycle)
        prompt = (
            "X_PREDICTION_OUTCOME: One or more previously dated predictions now have a matured recorded outcome. "
            "Write a concise correction/update only if the supplied settlement supports one. Clearly label the result as CONFIRMED, REFUTED, or INCONCLUSIVE exactly as recorded; do not reinterpret it. "
            "No investment advice or invented facts. Max 280 characters. Return JSON only.\n"
            + json.dumps({"settlements": terminal, "cycle_context": _expression_model_context(context)}, ensure_ascii=False)
        )
        decision = await _structured(model_runtime, prompt, XExpressionDecision)
        if decision.action != "POST" or not decision.post_text:
            return
        published = await self._publish_through_browser(text=decision.post_text)
        post_id = str(published.post_id).strip()
        if not post_id:
            raise RuntimeError("Jev prediction publication returned no post id")
        url = str(published.url).strip()
        receipt_id = f"x-browser-publication:{cycle}:{post_id}"
        row = self.history.append({
            "event_type": "PREDICTION_OUTCOME", "cycle": cycle, "expression_kind": "PREDICTION_OUTCOME",
            "text": decision.post_text, "x_post_id": post_id, "x_url": url,
            "runtime_receipt_id": receipt_id, "prediction_outcomes": terminal,
            "lineage": {"cycle": cycle, "settlement_file": "prediction_settlements.json", "future_cognition": True},
        })
        self._update_recent_public_posts(row)
        self._activity(cycle=cycle, title="Posted a prediction outcome", detail=decision.post_text)
def public_x_projection(state_root: Path, *, limit: int = 8) -> dict[str, Any]:
    root = Path(state_root) / "content" / "x"
    profile = _read_json(root / "public_profile.json")
    status = _read_json(root / "status.json")
    history = XHistoryLedger(state_root).recent(limit=max(limit * 3, 20))
    public_rows = []
    for row in reversed(history):
        if row.get("event_type") not in {"EXPRESSION", "REPLY", "PREDICTION_OUTCOME"}:
            continue
        public_rows.append({
            "event_type": row.get("event_type"), "cycle": row.get("cycle"),
            "expression_kind": row.get("expression_kind"), "text": row.get("text"),
            "post_id": row.get("x_post_id"), "url": row.get("x_url"),
            "recorded_at": row.get("recorded_at"), "target_post_id": row.get("target_post_id"),
        })
        if len(public_rows) >= limit:
            break
    return {
        "connected": bool(profile.get("id") and profile.get("handle")),
        "handle": profile.get("handle"), "name": profile.get("name"),
        "avatar_url": profile.get("avatar_url"), "url": profile.get("url"),
        "synced_at": profile.get("synced_at"), "recent_activity": public_rows,
        "status": status.get("status") or ("CONNECTED" if profile.get("id") else "NOT_CONNECTED"),
        "status_as_of": status.get("as_of"), "status_detail": status.get("detail"),
        "identity_continuity": "X account identity is external to any one server; local tokens are revocable credentials, not the agent identity.",
    }


def social_evidence_items(state_root: Path, *, now: datetime, limit: int = 12) -> list[dict[str, Any]]:
    path = Path(state_root) / "content" / "x" / "social_evidence.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(limit * 3, 20):]
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("status") != "DISCOVERED":
            continue
        post_id = str(row.get("post_id") or "")
        if not post_id or post_id in seen:
            continue
        seen.add(post_id)
        text = str(row.get("source_text") or row.get("summary") or "").strip()
        items.append({
            "evidence_id": "x-social:" + post_id, "source": "X social response", "source_kind": "social-x-response",
            "title": str(row.get("summary") or text)[:180], "summary": text[:1000], "url": row.get("url"),
            "source_revision": post_id, "published_at": row.get("observed_at"), "observed_at": now.isoformat(),
            "evidence_refs": tuple(row.get("evidence_refs") or ("x-post:" + post_id,)),
        })
        if len(items) >= limit:
            break
    return list(reversed(items))


__all__ = ["XAutonomyLoop", "XHistoryLedger", "public_x_projection", "social_evidence_items"]
