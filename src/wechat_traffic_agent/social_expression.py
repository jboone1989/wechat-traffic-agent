"""Channel-specific social drafts derived from persisted public cognition."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_loop import ReceiptRuntime, _put, _structured
from .public_activity import emit_public_activity


class ChannelSocialDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channel: Literal["OKX", "X"]
    format: Literal["SHORT_POST", "LONG_POST", "ARTICLE_TEASER"]
    text: str = Field(min_length=20, max_length=1200)
    style_observation_ids: tuple[str, ...] = Field(max_length=4)
    rationale: str = Field(min_length=10, max_length=400)

    @model_validator(mode="after")
    def channel_bounds(self) -> "ChannelSocialDraft":
        if self.channel == "X" and len(self.text) > 280:
            raise ValueError("X draft must be at most 280 characters")
        if self.channel == "OKX" and len(self.text) > 1200:
            raise ValueError("OKX draft must be at most 1200 characters")
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
    def __init__(self, *, runtime: Any, state_root: Path) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)

    async def after_content_cycle(self, *, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        cycle = str(result.get("cycle") or "")
        if not cycle or result.get("status") != "PUBLISHED":
            return {"status": "NO_PUBLISHED_EXPRESSION", "cycle": cycle}
        folder = self.state_root / "content" / "continuous" / "cycles" / cycle
        out = folder / "social_expression_drafts.json"
        if out.exists():
            return {"status": "ALREADY_DRAFTED", "cycle": cycle, "drafts": _read_json(out)}
        cognition = _read_json(folder / "cognition.json")
        if not cognition.get("worth_saying"):
            return {"status": "NO_EXPRESSION", "cycle": cycle}
        style = _read_json(folder / "social_style_context.json")
        draft_path = Path(str(result.get("draft_path") or ""))
        draft_record = _read_json(draft_path) if draft_path.is_file() else {}
        artifact = draft_record.get("artifact") if isinstance(draft_record.get("artifact"), dict) else {}
        publication = result.get("publication") if isinstance(result.get("publication"), dict) else {}
        allowed_style_ids = {
            str(row.get("observation_id")) for row in style.get("observations", []) if isinstance(row, dict)
        }
        model_runtime = ReceiptRuntime(self.runtime, folder / "social-expression-model-calls")
        prompt = (
            "SOCIAL_EXPRESSION_DRAFT: Render the same persisted cognition for two social channels. Do not invent a new topic or new facts. "
            "The website artifact is factual/context input, not a template to copy. social_style_context is STYLE_ONLY: use structure and pacing only, never copy wording, persona, certainty, or calls to action. "
            "For OKX, write like a current market/community post: direct hook, current bounded view, then what to watch or what would change the view; use short paragraphs and no formal article sections. Max 1200 characters. "
            "For X, compress the same useful idea to at most 280 characters. Prefer conditional language over unsupported prediction. "
            "If the cognition is LONGFORM, ARTICLE_TEASER is allowed; otherwise prefer SHORT_POST or LONG_POST. No private data, personalized investment advice, fabricated numbers, or unsupported causation. Return JSON only.\n"
            + json.dumps({
                "cycle": cycle, "cognition": cognition, "artifact": artifact,
                "publication": publication, "social_style_context": style,
            }, ensure_ascii=False)
        )
        def validate(value: SocialExpressionBundle) -> None:
            for channel in (value.okx, value.x):
                if any(style_id not in allowed_style_ids for style_id in channel.style_observation_ids):
                    raise ValueError("social draft references an unknown style observation")
        bundle = await _structured(model_runtime, prompt, SocialExpressionBundle, validate=validate)
        payload = {
            "schema_version": "content-social-expression-drafts.v1",
            "cycle": cycle, "created_at": now.isoformat(),
            "publication_authority": False,
            "style_status": str(style.get("status") or "UNAVAILABLE"),
            "style_performance_claim": str(style.get("performance_claim") or "UNVERIFIED"),
            "drafts": bundle.model_dump(mode="json"),
        }
        _put(out, payload)
        emit_public_activity(
            self.state_root, cycle=cycle, phase="CREATE",
            title="I shaped the thought for social channels",
            detail="Prepared separate OKX and X drafts from the same cognition; nothing was auto-posted.",
            status="completed",
        )
        return {"status": "DRAFTED", "cycle": cycle, "drafts": payload}


__all__ = ["ChannelSocialDraft", "SocialExpressionBundle", "SocialExpressionDraftWorker"]
