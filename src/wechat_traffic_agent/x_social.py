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


class XSafetyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    safe: bool
    no_private_data: bool
    no_impersonation: bool
    no_harassment: bool
    no_investment_advice: bool
    no_fabricated_facts: bool
    attribution_ok: bool
    rationale: str = Field(min_length=10, max_length=500)
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
        final_status = "DEGRADED" if failures else "OK"
        self._write_status(now=now, status=final_status)
        self._activity(cycle=cycle, title="Finished checking X", detail=f"Reviewed {len(mentions)} recent mention(s); {handled} were new.")
        return {
            "status": final_status, "identity_receipt_id": identity_receipt, "mentions_receipt_id": mentions_receipt_id,
            "mention_count": len(mentions), "new_mentions": handled, "discovery": discovery, "failures": failures,
            "x_growth_mission": growth_result,
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
                "No private data, impersonation, harassment, investment advice, fabricated facts, or unsupported factual assertions. "
                "If the mention asserts an unverified fact, phrase the reply so it does not adopt that fact as true. Return JSON only.\n"
                + json.dumps({
                    "mention": row,
                    "agent_handle": me.get("username"),
                    "public_persona": public_persona_projection(self.state_root),
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
            # Independent wording check before any public input.
            safety = await self._safety_check(model_runtime=model_runtime, text=decision.reply_text,
                context={"public_post":row,"public_persona":public_persona_projection(self.state_root)})
            _write_json(folder / "reply_safety.json", safety.model_dump(mode="json"))
            if not _safety_passes(safety):
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
        owner_surface=str((context.get("owner_publication_request") or {}).get("decision",{}).get("publication_surface") or "")
        if owner_directed_x and owner_surface in {"X_LONG_POST","X_ARTICLE"}:
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
            and x_strategy
            and str(x_strategy.get("x_format") or "SHORT_POST") != "SHORT_POST"
            and not self.history.has_event(cycle=cycle, event_type="EXPRESSION")
        ):
            return {
                "status": "X_LONGFORM_TRANSPORT_REQUIRED",
                "cycle": cycle,
                "x_strategy": x_strategy,
            }
        model_runtime = ReceiptRuntime(self.model_runtime, self.root / "expression-model-calls" / cycle)
        public_url = _find_url(context.get("publication"))
        x_mission = x_growth_mission_context(self.state_root)
        prompt = (
            "X_EXPRESSION_DRAFT: "
            + ("The durable owner controller explicitly requested an original SHORT_POST on X in this cycle. L1 timing remains advisory for this execution request. "
               if owner_directed_x else "Jev L1 has already selected POST_NOW and SHORT_POST. ")
            +             "Write only the post text; do not reconsider whether to POST or SKIP and do not choose another format. "
            "The post must arise from the supplied cognition, not invent a new topic. For LONGFORM/RESEARCH_NOTE/METHOD_NOTE, a concise takeaway plus the supplied public URL is useful when one exists. "
            "For METHOD_NOTE, describe the method/tool only as a candidate being tested unless adoption is separately proven. "
            "For a worldview formation or revision, leave a clear public trace of what changed and uncertainty. "
            "Preserve the supplied public_persona voice and LivingRuntime affiliation. When relevant, show what persistent-agent continuity enables, but do not force promotion into unrelated market observations. "
            "x_growth_mission and x_l1_strategy are strategy context, not factual evidence. "
            "No private data, impersonation, harassment, investment advice, fabricated facts, or unsupported certainty. Max 280 characters including any URL. Return JSON only.\n"
            + json.dumps({**context, "public_url": public_url,
                          "public_persona": public_persona_projection(self.state_root),
                          "x_growth_mission": x_mission,
                          "x_l1_strategy": x_strategy}, ensure_ascii=False)
        )
        decision = await _structured(model_runtime, prompt, XPostDraft)
        safety = await self._safety_check(model_runtime=model_runtime, text=decision.post_text, context=context)
        if not _safety_passes(safety):
            return {"status": "HELD_BY_X_SAFETY", "cycle": cycle, "reason": safety.rationale}
        guardrail = getattr(self.growth_mission, "publication_guardrail", None)
        if callable(guardrail):
            guard = guardrail()
            if not bool(guard.get("allowed", True)):
                return {
                    "status": "HELD_BY_MISSION_GUARDRAIL",
                    "cycle": cycle,
                    "guardrail": guard,
                }
        me, _identity_receipt = await self._publication_identity(now=now)
        if self.growth_mission is not None:
            if isinstance(mission_binding, dict) and bool(mission_binding.get("bound")):
                baseline = self.growth_mission.record_publication_baseline(
                    cycle=cycle,
                    user=me,
                    identity_receipt_id=_identity_receipt,
                    now=now,
                )
                if str((baseline or {}).get("status") or "") != "RECORDED":
                    return {
                        "status": "HELD_MISSION_BASELINE_UNAVAILABLE",
                        "cycle": cycle,
                        "binding": mission_binding,
                        "baseline": baseline,
                    }
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
            "owner_execution_request": context.get("owner_publication_request") or None,
        }

    async def _owner_longform(self, *, folder, context, now, surface):
        """Execute Ferro's selected X format without a website publication prerequisite."""
        from .social_expression import ChannelSocialDraft
        from .x_browser import XBrowserPublisher
        cycle=folder.name
        fmt="ARTICLE" if surface=="X_ARTICLE" else "LONG_POST"
        if self.history.has_event(cycle=cycle,event_type="EXPRESSION"):
            return {"status":"ALREADY_EXPRESSED","cycle":cycle}
        path=folder/"social_expression_drafts.json"
        existing=_read_json(path)
        if existing.get("status")!="DRAFT_READY":
            prompt=("X_OWNER_LONGFORM_DRAFT: Ferro's durable owner-goal controller selected "+fmt+". "
                "Write in the supplied public persona from persisted cognition and attributed source only. "
                "Return a ChannelSocialDraft JSON: channel X, format "+fmt+", title for ARTICLE (5-100 characters), "
                "optional cover_caption <=64 characters, text 20-8000 characters, intent, style_observation_ids [], rationale. "
                "For ARTICLE, choose images: each has role COVER or INLINE, mode GRAPHIC/GENERATE/SEARCH, alt_text, caption, "
                "prompt for actual ChatGPT image generation or query for licensed Commons image search. "
                "An empty images list uses an original graphic cover. Never claim generated art is a real product screenshot or evidence. "
                "Preserve uncertainty and source attribution. Owner quantities are optional advice, not quotas.\n"
                +json.dumps({**context,"persona":public_persona_projection(self.state_root),
                    "mission":x_growth_mission_context(self.state_root)},ensure_ascii=False))
            runtime=ReceiptRuntime(self.model_runtime,self.root/"expression-model-calls"/cycle)
            draft=await _structured(runtime,prompt,ChannelSocialDraft)
            if draft.channel!="X" or draft.format!=fmt:
                return {"status":"OWNER_FORMAT_MISMATCH","cycle":cycle}
            strategy={"schema_version":"ferro-x-owner-format-request.v1","post_timing":"POST_NOW",
                "x_format":fmt,"interaction_mode":"ORIGINAL","selection_authority":"FERRO_OWNER_EPISODE",
                "owner_request":context["owner_publication_request"]}
            _write_json(path,{"status":"DRAFT_READY","cycle":cycle,"drafts":{"x":draft.model_dump(mode="json")},
                "x_strategy":strategy,"evidence_decision":{"source_evidence":context.get("source_evidence"),
                    "resolution":{"action":"DRAFT","useful_facts":[str((context.get("source_evidence") or {}).get("summary") or "")]}}})
        publisher=XBrowserPublisher(state_root=self.state_root,transport=self.browser_publish_transport,
            growth_mission=self.growth_mission,mission_baseline_observer=self.capture_mission_publication_baseline,
            draft_safety_check=self.validate_public_draft)
        result=await publisher.after_content_cycle(cycle=cycle,now=now)
        publication=result.get("publication") or {}
        if result.get("status") in {"POSTED","ALREADY_POSTED"}:
            receipt_id=f"x-browser-publication:{cycle}:{publication['post_id']}"
            self._clear_publication_blocker(now=now,receipt_id=receipt_id)
            return {**result,"status":"POSTED" if result['status']=='POSTED' else "ALREADY_EXPRESSED",
                "post_id":publication["post_id"],"url":publication["url"],"receipt_id":receipt_id,
                "format":fmt,"owner_execution_request":context["owner_publication_request"]}
        return result

    async def validate_public_draft(self, *, cycle, draft):
        folder=self.state_root/"content"/"continuous"/"cycles"/cycle
        context={"cognition":_read_json(folder/"cognition.json"),
            "evidence":_read_json(folder/"social_expression_drafts.json").get("evidence_decision") or {},
            "public_persona":public_persona_projection(self.state_root)}
        text="\n".join(str(draft.get(key) or "") for key in ("title","text","cover_caption"))
        if draft.get("images"):
            text += "\nIMAGE_PLAN:\n" + json.dumps(draft["images"], ensure_ascii=False)
        runtime=ReceiptRuntime(self.model_runtime,self.root/"expression-model-calls"/cycle)
        safety=await self._safety_check(model_runtime=runtime,text=text,context=context)
        payload={**safety.model_dump(mode="json"),"passed":_safety_passes(safety)}
        _write_json(folder/"x_article_safety.json",payload)
        return payload

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

    async def _safety_check(self, *, model_runtime: ReceiptRuntime, text: str, context: dict[str, Any]) -> XSafetyDecision:
        prompt = (
            "X_SAFETY_CHECK: Check the candidate X post only against the supplied persisted public cognition/artifact context. "
            "safe may be true only if there is no private data, impersonation, harassment, individualized investment advice, fabricated factual claim, or missing attribution for a factual claim that needs it. "
            "Do not improve or rewrite the post; judge it. Return JSON only.\n"
            + json.dumps({"candidate_post": text, "context": context}, ensure_ascii=False)
        )
        return await _structured(model_runtime, prompt, XSafetyDecision)

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
            + json.dumps({"settlements": terminal, "cycle_context": context}, ensure_ascii=False)
        )
        decision = await _structured(model_runtime, prompt, XExpressionDecision)
        if decision.action != "POST" or not decision.post_text:
            return
        safety = await self._safety_check(model_runtime=model_runtime, text=decision.post_text, context={"settlements": terminal})
        if not _safety_passes(safety):
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
def _safety_passes(value: XSafetyDecision) -> bool:
    return all((
        value.safe, value.no_private_data, value.no_impersonation, value.no_harassment,
        value.no_investment_advice, value.no_fabricated_facts, value.attribution_ok,
    ))


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
