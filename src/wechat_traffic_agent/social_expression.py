"""Channel-specific social drafts derived from persisted public cognition."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_loop import ReceiptRuntime, _put, _structured
from .public_activity import emit_public_activity
from .social_evidence import EvidenceCollector, resolve_social_evidence
from .social_performance import okx_social_performance_context, x_social_performance_context
from .public_persona import public_persona_projection

OKX_SHORT_POST_MAX_CHARS = 500
SOCIAL_LONG_TEXT_MAX_CHARS = 8000


class ChannelSocialDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channel: Literal["OKX", "X"]
    format: Literal["SHORT_POST", "LONG_POST", "ARTICLE"]
    intent: Literal["ASSERTION", "QUESTION", "OBSERVATION", "UPDATE", "EXPLAINER"] = "ASSERTION"
    text: str = Field(min_length=20, max_length=SOCIAL_LONG_TEXT_MAX_CHARS)
    style_observation_ids: tuple[str, ...] = Field(max_length=4)
    rationale: str = Field(min_length=10, max_length=400)

    @model_validator(mode="after")
    def channel_bounds(self) -> "ChannelSocialDraft":
        if self.channel == "OKX" and self.format == "SHORT_POST" and len(self.text) > OKX_SHORT_POST_MAX_CHARS:
            raise ValueError(f"OKX short post must be at most {OKX_SHORT_POST_MAX_CHARS} characters")
        if self.channel == "OKX" and self.format == "LONG_POST":
            raise ValueError("OKX should use SHORT_POST for feed posts or ARTICLE for long-form content")
        if len(self.text) > SOCIAL_LONG_TEXT_MAX_CHARS:
            raise ValueError(f"social draft must be at most {SOCIAL_LONG_TEXT_MAX_CHARS} characters")
        if self.intent == "QUESTION" and "?" not in self.text and "？" not in self.text:
            raise ValueError("QUESTION social intent requires an explicit question")
        return self


class SocialExpressionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    okx: ChannelSocialDraft
    x: ChannelSocialDraft

    @model_validator(mode="after")
    def channels_match(self) -> "SocialExpressionBundle":
        if self.okx.channel != "OKX" or self.x.channel != "X":
            raise ValueError("bundle channels must be OKX and X")
        return self


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class SocialExpressionDraftWorker:
    def __init__(self, *, runtime: Any, state_root: Path,
                 evidence_collector: EvidenceCollector | None = None) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.evidence_collector = evidence_collector

    async def after_content_cycle(self, *, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        cycle = str(result.get("cycle") or "")
        if not cycle or result.get("status") != "PUBLISHED":
            return {"status": "NO_PUBLISHED_EXPRESSION", "cycle": cycle}
        folder = self.state_root / "content" / "continuous" / "cycles" / cycle
        out = folder / "social_expression_drafts.json"
        if out.exists():
            existing = _read_json(out)
            status = "ALREADY_SKIPPED" if existing.get("status") == "SKIPPED" else "ALREADY_DRAFTED"
            return {"status": status, "cycle": cycle, "drafts": existing}
        cognition = _read_json(folder / "cognition.json")
        if not cognition.get("worth_saying"):
            return {"status": "NO_EXPRESSION", "cycle": cycle}
        style = _read_json(folder / "social_style_context.json")
        performance = okx_social_performance_context(state_root=self.state_root, as_of=now)
        x_performance = x_social_performance_context(state_root=self.state_root, as_of=now)
        persona = public_persona_projection(self.state_root)
        draft_path = Path(str(result.get("draft_path") or ""))
        draft_record = _read_json(draft_path) if draft_path.is_file() else {}
        artifact = draft_record.get("artifact") if isinstance(draft_record.get("artifact"), dict) else {}
        publication = result.get("publication") if isinstance(result.get("publication"), dict) else {}

        evidence = await resolve_social_evidence(
            runtime=self.runtime, folder=folder, cognition=cognition, artifact=artifact,
            now=now, evidence_collector=self.evidence_collector,
        )
        resolution = evidence.get("resolution") if isinstance(evidence.get("resolution"), dict) else {}
        evidence_action = str(resolution.get("action") or "SKIP")
        if evidence_action == "SKIP":
            payload = {
                "schema_version": "content-social-expression-drafts.v2",
                "status": "SKIPPED", "cycle": cycle, "created_at": now.isoformat(),
                "publication_authority": False, "evidence_decision": evidence,
                "performance_context": performance,
                "x_performance_context": x_performance,
                "drafts": {},
            }
            _put(out, payload)
            emit_public_activity(
                self.state_root, cycle=cycle, phase="CREATE",
                title="I chose not to force a social post",
                detail=str(resolution.get("rationale") or "The evidence did not create enough public information value."),
                status="completed",
            )
            return {"status": "SKIPPED", "cycle": cycle, "drafts": payload}

        allowed_style_ids = {
            str(row.get("observation_id")) for row in style.get("observations", []) if isinstance(row, dict)
        }
        model_runtime = ReceiptRuntime(self.runtime, folder / "social-expression-model-calls")
        question_instruction = (
            " Evidence resolution is QUESTION: both channel drafts must have intent QUESTION. Give the known fact briefly, then ask a genuine open question. "
            "Do not smuggle an unsupported answer into a rhetorical question and do not pad the post with a list of caveats."
            if evidence_action == "QUESTION" else
            " Evidence resolution is DRAFT: make the post valuable with the strongest supported information gain. Do not make 'what data is missing' the main content when the supplemental evidence has already filled that gap."
        )
        prompt = (
            "SOCIAL_EXPRESSION_DRAFT: Render the same persisted cognition for two social channels. Do not invent a new topic or new facts. "
            "Use only the website artifact plus supplemental_evidence from the social evidence decision as factual input. social_style_context is STYLE_ONLY: use structure and pacing only, never copy wording, persona, certainty, or calls to action. "
            "social_performance_context is DESCRIPTIVE_STYLE_ONLY and OKX_ONLY: when its generation_authority is STYLE_ONLY, it may guide only the OKX draft among evidence-compatible format, pacing, and presentation patterns. The X draft must ignore this OKX performance context. "
            "x_performance_context is DESCRIPTIVE_STYLE_ONLY and X_ONLY: when its generation_authority is STYLE_ONLY, it may guide only the X draft among evidence-compatible format, pacing, and presentation patterns. The OKX draft must ignore this X performance context. "
            "Both performance contexts are observational, not causal; neither may change factual certainty, evidence_action, topic, grounding, or publication authority. When a context says INSUFFICIENT_SAMPLES, ignore it. "
            "public_persona is durable identity: write in that agent's voice and preserve its LivingRuntime affiliation. persona.learning is only STYLE_AND_TOPIC_INTEREST_ONLY: recurring questions may suggest what needs clearer explanation, but cannot change facts, identity, mission, safety, or authority. "
            "Promote LivingRuntime by showing concrete work and continuity when relevant; do not append repetitive promotional slogans or force a link into every post. "
            "For OKX, choose SHORT_POST for an immediate feed update and keep that format at most 500 characters. If the cognition has enough evidence, mechanism, counterpoint, or durable value to justify long-form treatment, choose ARTICLE instead of stretching a feed post into a mini-article. "
            "For X, this account supports long posts and Articles. Choose SHORT_POST when the idea is simple, LONG_POST when one social post genuinely benefits from more context, and ARTICLE when the cognition deserves a structured long-form treatment. Prefer conditional language over unsupported prediction. "
            "Posts may be assertions, questions, observations, updates, or explainers; choose the form that matches the evidence rather than forcing a market call. "
            "Treat social_evidence_decision.resolution.useful_facts as the information-value boundary: a non-question post must lead with at least one of those supported facts or a direct inference they support. Missing-data caveats may qualify a claim, but must never be the main payload of a DRAFT. "
            "If intent is QUESTION, anchor it in a known fact and ask a genuine unresolved question rather than engagement bait. "
            "ARTICLE is a real long-form channel artifact, not a teaser. Do not choose it merely because more text is available; choose it only when the cognition supports depth. No private data, personalized investment advice, fabricated numbers, or unsupported causation. Return JSON only."
            + question_instruction + "\n"
            + json.dumps({
                "cycle": cycle, "cognition": cognition, "artifact": artifact,
                "publication": publication, "social_style_context": style,
                "social_performance_context": performance,
                "x_performance_context": x_performance,
                "public_persona": persona,
                "social_evidence_decision": evidence,
            }, ensure_ascii=False)
        )

        def validate(value: SocialExpressionBundle) -> None:
            for channel in (value.okx, value.x):
                if any(style_id not in allowed_style_ids for style_id in channel.style_observation_ids):
                    raise ValueError("social draft references an unknown style observation")
                if evidence_action == "QUESTION" and channel.intent != "QUESTION":
                    raise ValueError("QUESTION evidence resolution requires QUESTION social intent")
                if channel.intent == "QUESTION" and "?" not in channel.text and "？" not in channel.text:
                    raise ValueError("QUESTION social intent requires an explicit question")

        bundle = await _structured(model_runtime, prompt, SocialExpressionBundle, validate=validate)
        payload = {
            "schema_version": "content-social-expression-drafts.v2",
            "status": "DRAFT_READY", "cycle": cycle, "created_at": now.isoformat(),
            "publication_authority": False,
            "style_status": str(style.get("status") or "UNAVAILABLE"),
            "style_performance_claim": str(style.get("performance_claim") or "UNVERIFIED"),
            "performance_context": performance,
            "x_performance_context": x_performance,
            "public_persona": persona,
            "evidence_decision": evidence,
            "drafts": bundle.model_dump(mode="json"),
        }
        _put(out, payload)
        emit_public_activity(
            self.state_root, cycle=cycle, phase="CREATE",
            title=("I turned the unresolved evidence into a useful question" if evidence_action == "QUESTION"
                   else "I shaped the evidence into social posts"),
            detail="Prepared separate OKX and X drafts from the same cognition and evidence; nothing was auto-posted.",
            status="completed",
        )
        return {"status": "DRAFTED", "cycle": cycle, "drafts": payload}


__all__ = ["ChannelSocialDraft", "SocialExpressionBundle", "SocialExpressionDraftWorker"]
