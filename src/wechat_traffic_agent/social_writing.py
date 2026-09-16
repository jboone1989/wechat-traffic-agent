"""Style-only social writing observations; never factual market evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SocialStyleObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: str = Field(min_length=3, max_length=120)
    channel: Literal["X", "OKX"]
    format: Literal["SHORT_POST", "MARKET_RECAP", "EXPLAINER_POST", "ARTICLE"]
    source_url: HttpUrl
    features: tuple[str, ...] = Field(min_length=2, max_length=8)


class SocialStyleCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["social-writing-observations.v1"]
    purpose: Literal["STYLE_ONLY"]
    status: Literal["DISCOVERED"]
    performance_claim: Literal["UNVERIFIED"]
    observations: tuple[SocialStyleObservation, ...] = Field(min_length=1, max_length=40)


def load_social_style_context(path: Path, *, expression_kind: str, market_pulse: bool) -> dict[str, Any]:
    if not path.exists():
        return {"purpose": "STYLE_ONLY", "status": "UNAVAILABLE", "observations": []}
    corpus = SocialStyleCorpus.model_validate_json(path.read_text(encoding="utf-8"))
    wanted = {"ARTICLE"} if expression_kind == "LONGFORM" else {"SHORT_POST", "MARKET_RECAP", "EXPLAINER_POST"}
    if market_pulse:
        wanted |= {"SHORT_POST", "MARKET_RECAP"}
    rows = [row.model_dump(mode="json") for row in corpus.observations if row.format in wanted]
    return {
        "purpose": corpus.purpose,
        "status": corpus.status,
        "performance_claim": corpus.performance_claim,
        "rules": [
            "Use these only to learn structure and pacing, never as factual evidence.",
            "Do not copy wording, persona, claims, calls to action, or certainty from a source author.",
            "No style pattern is proven effective until this agent has its own audience outcome evidence."
        ],
        "observations": rows[:8],
    }


__all__ = ["SocialStyleCorpus", "SocialStyleObservation", "load_social_style_context"]
