"""Autonomous X observation, expression and autobiographical lineage."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_loop import ReceiptRuntime, _structured
from .public_activity import emit_public_activity
from .sources.runtime_x import first_post_id, first_user, normalize_posts
from .x_mcp import x_writes_enabled
from .public_persona import public_persona_projection, record_public_interaction
from .x_growth_mission import follower_count_from_user, x_growth_mission_context


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
                 growth_mission: Any | None = None) -> None:
        self.runtime = runtime
        self.model_runtime = model_runtime
        self.browser_mentions_observer = browser_mentions_observer
        self.growth_mission = growth_mission
        self.state_root = Path(state_root)
        self.base_url = base_url.rstrip("/")
        self.root = self.state_root / "content" / "x"
        self.history = XHistoryLedger(self.state_root)
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

    def _identity(self, *, now: datetime) -> tuple[dict[str, Any], str]:
        receipt = self.runtime.invoke("x.identity", {})
        if not receipt.payload.get("ok"):
            raise RuntimeError(str(receipt.payload.get("status") or "X_IDENTITY_FAILED"))
        user = first_user(receipt.payload.get("data"))
        if not user:
            raise RuntimeError("X identity response did not contain a user")
        handle = str(user.get("username") or "").strip().lstrip("@")
        follower_count = follower_count_from_user(user)
        profile = {
            "schema_version": "content-agent-x-profile.v1",
            "synced_at": now.isoformat(),
            "id": str(user.get("id") or ""),
            "handle": handle,
            "name": str(user.get("name") or ""),
            "avatar_url": user.get("profile_image_url"),
            "url": f"https://x.com/{handle}" if handle else None,
            "followers_count": follower_count,
            "public_metrics": (
                dict(user.get("public_metrics"))
                if isinstance(user.get("public_metrics"), dict)
                else None
            ),
            "identity_receipt_id": receipt.receipt_id,
        }
        existing = _read_json(self.profile_path)
        _write_json(self.profile_path, {**existing, **profile})
        return user, receipt.receipt_id

    async def capture_mission_publication_baseline(
        self, *, cycle: str, now: datetime
    ) -> dict[str, Any]:
        if self.growth_mission is None:
            return {"status": "NO_GROWTH_MISSION"}
        user, identity_receipt = self._identity(now=now)
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
            me, identity_receipt = self._identity(now=now)
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
        receipt = self.runtime.invoke("x.mentions", {"user_id": str(me.get("id") or ""), "max_results": 20})
        browser_fallback = False
        mentions_receipt_id: str | None = receipt.receipt_id
        if not receipt.payload.get("ok"):
            status = str(receipt.payload.get("status") or "X_MENTIONS_FAILED")
            detail = str(receipt.payload.get("detail") or "")[:300]
            if status == "X_CREDITS_DEPLETED":
                try:
                    observer = self.browser_mentions_observer
                    if observer is None:
                        from .x_browser_mentions import XBrowserMentionsObserverRouter
                        observer = XBrowserMentionsObserverRouter(state_root=self.state_root)
                    raw_mentions = await observer.observe(now=now, max_results=20)
                    mentions = normalize_posts(raw_mentions, observed_at=now)
                    browser_fallback = True
                    mentions_receipt_id = None
                    self._write_status(
                        now=now,
                        status="OK_BROWSER_FALLBACK",
                        detail="Official X credits depleted; mentions read through the authenticated browser runtime.",
                    )
                    self._activity(
                        cycle=cycle,
                        title="Reading X through browser fallback",
                        detail="Official X credits are depleted, so I used an authenticated read-only browser provider.",
                    )
                except Exception as fallback_exc:
                    self._write_status(now=now, status=status, detail=detail)
                    self._activity(cycle=cycle, title="X observation paused", detail="X API credits are depleted and the read-only browser fallback was unavailable.", status="stopped")
                    return {"status": status, "mentions_receipt_id": receipt.receipt_id, "detail": detail, "browser_fallback_error": type(fallback_exc).__name__, "x_growth_mission": growth_result}
            else:
                self._write_status(now=now, status=status, detail=detail)
                self._activity(cycle=cycle, title="X observation paused", detail="X returned an error while reading mentions.", status="stopped")
                return {"status": status, "mentions_receipt_id": receipt.receipt_id, "detail": detail, "x_growth_mission": growth_result}
        else:
            mentions = normalize_posts(receipt.payload.get("data"), observed_at=now)
        seen_payload = _read_json(self.seen_path)
        seen = {str(value) for value in seen_payload.get("ids", []) if str(value)}
        fresh = [row for row in mentions if row["evidence_id"].removeprefix("x-post:") not in seen]
        handled = 0
        for row in fresh[:10]:
            post_id = row["evidence_id"].removeprefix("x-post:")
            await self._handle_mention(row=row, post_id=post_id, me=me, cycle=cycle, now=now,
                                       allow_remote_actions=allow_remote_actions and not browser_fallback)
            seen.add(post_id)
            handled += 1
        _write_json(self.seen_path, {"schema_version": "content-agent-x-seen.v1", "ids": sorted(seen)[-2000:]})
        final_status = "OK_BROWSER_FALLBACK" if browser_fallback else "OK"
        self._write_status(now=now, status=final_status)
        self._activity(cycle=cycle, title="Finished checking X", detail=f"Reviewed {len(mentions)} recent mention(s); {handled} were new.")
        return {
            "status": final_status, "identity_receipt_id": identity_receipt, "mentions_receipt_id": mentions_receipt_id,
            "mention_count": len(mentions), "new_mentions": handled,
            "x_growth_mission": growth_result,
        }

    async def _handle_mention(self, *, row: dict[str, Any], post_id: str, me: dict[str, Any], cycle: str, now: datetime,
                              allow_remote_actions: bool = True) -> None:
        self._activity(cycle=cycle, title="Someone replied", detail=row.get("summary", "")[:220])
        folder = self.root / "social-model-calls" / cycle / post_id
        model_runtime = ReceiptRuntime(self.model_runtime, folder)
        prompt = (
            "X_SOCIAL_ATTENTION: Classify this public X mention. Decide IGNORE, INVESTIGATE, or REPLY. "
            "Questions, substantive rebuttals, or evidence may deserve attention. Spam and low-information reactions should not. "
            "valuable_evidence means the content could materially change an inquiry or worldview after verification; it does NOT mean it is already true or adopted. "
            "A reply must be <=280 characters, contain no private data, impersonation, harassment, investment advice, fabricated facts, or unsupported factual assertions. "
            "If factual verification is needed before replying, choose INVESTIGATE instead. Return JSON only.\n"
            + json.dumps({"mention": row, "agent_handle": me.get("username"),
                          "public_persona": public_persona_projection(self.state_root)}, ensure_ascii=False)
        )
        decision = await _structured(model_runtime, prompt, SocialAttentionDecision)
        if decision.category != "SPAM":
            record_public_interaction(
                state_root=self.state_root, source="X", message=str(row.get("summary") or ""),
                category=decision.category, source_ref=post_id, observed_at=now, summary=decision.summary,
            )
        if decision.valuable_evidence:
            _append_jsonl(self.social_evidence_path, {
                "schema_version": "content-agent-x-social-evidence.v1",
                "observed_at": now.isoformat(), "status": "DISCOVERED",
                "post_id": post_id, "url": row.get("url"), "summary": decision.summary,
                "category": decision.category, "source_text": row.get("summary", "")[:1000],
                "evidence_refs": row.get("evidence_refs", ()),
            })
            self._activity(cycle=cycle, title="Investigating a reply", detail=decision.summary)
            if allow_remote_actions and x_writes_enabled():
                bookmark = self.runtime.invoke("x.bookmark", {
                    "user_id": str(me.get("id") or ""), "post_id": post_id,
                })
                if bookmark.payload.get("ok"):
                    self.history.append({
                        "event_type": "BOOKMARK", "cycle": cycle, "expression_kind": "LEARNING_CANDIDATE",
                        "target_post_id": post_id, "runtime_receipt_id": bookmark.receipt_id,
                        "source_evidence_refs": list(row.get("evidence_refs", ())),
                        "lineage": {"source": "mention", "status": "DISCOVERED", "adopted": False},
                    })
                    self._activity(cycle=cycle, title="Bookmarked for testing", detail=decision.summary)
        if decision.action == "REPLY" and decision.reply_text and allow_remote_actions and x_writes_enabled():
            reply = self.runtime.invoke("x.reply", {
                "user_id": str(me.get("id") or ""), "post_id": post_id, "text": decision.reply_text,
            })
            if not reply.payload.get("ok"):
                self._activity(cycle=cycle, title="Reply was not published", detail=str(reply.payload.get("status") or "X API error"), status="stopped")
                return
            reply_id = first_post_id(reply.payload.get("data"))
            self.history.append({
                "event_type": "REPLY", "cycle": cycle, "cognition": decision.model_dump(mode="json"),
                "expression_kind": "SOCIAL_REPLY", "text": decision.reply_text,
                "x_post_id": reply_id, "target_post_id": post_id, "target_user_id": row.get("x_author_id"),
                "runtime_receipt_id": reply.receipt_id, "source_evidence_refs": list(row.get("evidence_refs", ())),
                "lineage": {"source": "mention", "attention": decision.category, "future_cognition": bool(decision.valuable_evidence)},
            })
            self._activity(cycle=cycle, title="Replied on X", detail=decision.reply_text)

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
        return folder, {
            "cycle": cycle, "cognition": cognition, "worldview": worldview, "prediction": prediction,
            "mind_plan": plan, "artifact": artifact, "publication": publication,
        }
    async def after_content_cycle(self, *, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        if not x_writes_enabled():
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
        x_only_admitted = (
            isinstance(mission_binding, dict)
            and bool(mission_binding.get("bound"))
            and str(mission_binding.get("status") or "") == "BOUND_X_EXPRESSION"
        )
        if (
            (result_status != "PUBLISHED" and not x_only_admitted)
            or not cognition.get("worth_saying")
        ):
            return {"status": "NO_EXPRESSION", "cycle": cycle}
        if self.history.has_event(cycle=cycle, event_type="EXPRESSION"):
            return {"status": "ALREADY_EXPRESSED", "cycle": cycle}
        context["x_mission_binding"] = mission_binding
        model_runtime = ReceiptRuntime(self.model_runtime, self.root / "expression-model-calls" / cycle)
        public_url = _find_url(context.get("publication"))
        x_mission = x_growth_mission_context(self.state_root)
        prompt = (
            "X_EXPRESSION: Decide whether this already-completed cognition deserves an X post now. Do not post to satisfy a quota. "
            "The post must arise from the supplied cognition, not invent a new topic. For LONGFORM/RESEARCH_NOTE/METHOD_NOTE, a concise takeaway plus the supplied public URL is useful when one exists. "
            "For METHOD_NOTE, describe the method/tool only as a candidate being tested unless adoption is separately proven. "
            "For a worldview formation or revision, leave a clear public trace of what changed and uncertainty. "
            "Preserve the supplied public_persona voice and LivingRuntime affiliation. When relevant, show what persistent-agent continuity enables, but do not force promotion into unrelated market observations. "
            "x_growth_mission is MISSION_STRATEGY_ONLY. It may influence whether this evidence-compatible expression is a useful instance of the active experiment, but it is not factual evidence and cannot override safety, grounding, X policy, or the rule against posting to satisfy a quota. "
            "No private data, impersonation, harassment, investment advice, fabricated facts, or unsupported certainty. Max 280 characters including any URL. Return JSON only.\n"
            + json.dumps({**context, "public_url": public_url,
                          "public_persona": public_persona_projection(self.state_root),
                          "x_growth_mission": x_mission}, ensure_ascii=False)
        )
        decision = await _structured(model_runtime, prompt, XExpressionDecision)
        if decision.action != "POST" or not decision.post_text:
            return {"status": "SKIPPED", "cycle": cycle, "reason": decision.rationale}
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
        me, _identity_receipt = self._identity(now=now)
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
        post = self.runtime.invoke("x.post", {"user_id": str(me.get("id") or ""), "text": decision.post_text})
        if not post.payload.get("ok"):
            return {"status": str(post.payload.get("status") or "X_POST_FAILED"), "cycle": cycle,
                    "receipt_id": post.receipt_id, "detail": str(post.payload.get("detail") or "")[:300]}
        post_id = first_post_id(post.payload.get("data"))
        if not post_id:
            return {"status": "X_POST_ID_MISSING", "cycle": cycle, "receipt_id": post.receipt_id}
        mission_experiment_transition: dict[str, Any] | None = None
        if self.growth_mission is not None:
            try:
                mission_experiment_transition = (
                    self.growth_mission.start_experiment_from_publication(
                        cycle=cycle,
                        publication_receipt_id=post.receipt_id,
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
        handle = str(me.get("username") or "").lstrip("@")
        url = f"https://x.com/{handle}/status/{post_id}" if handle and post_id else None
        worldview = context.get("worldview") or {}
        plan = context.get("mind_plan") or {}
        row = self.history.append({
            "event_type": "EXPRESSION", "cycle": cycle,
            "cognition": cognition, "expression_kind": cognition.get("expression_kind"),
            "text": decision.post_text, "x_post_id": post_id, "x_url": url,
            "runtime_receipt_id": post.receipt_id,
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
            "receipt_id": post.receipt_id,
            "mission_experiment_transition": mission_experiment_transition,
        }

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
        me, _ = self._identity(now=now)
        post = self.runtime.invoke("x.post", {"user_id": str(me.get("id") or ""), "text": decision.post_text})
        if not post.payload.get("ok"):
            self._activity(cycle=cycle, title="Prediction update was not published", detail=str(post.payload.get("status") or "X API error"), status="stopped")
            return
        post_id = first_post_id(post.payload.get("data"))
        if not post_id:
            return
        handle = str(me.get("username") or "").lstrip("@")
        url = f"https://x.com/{handle}/status/{post_id}" if handle and post_id else None
        row = self.history.append({
            "event_type": "PREDICTION_OUTCOME", "cycle": cycle, "expression_kind": "PREDICTION_OUTCOME",
            "text": decision.post_text, "x_post_id": post_id, "x_url": url,
            "runtime_receipt_id": post.receipt_id, "prediction_outcomes": terminal,
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
