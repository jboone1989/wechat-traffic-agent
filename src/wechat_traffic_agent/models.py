"""Domain contracts for a short WeChat 题图 content agent."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EmotionAngle(str, Enum):
    RESONANCE = "resonance"
    RELIEF = "relief"
    NOSTALGIA = "nostalgia"
    GENTLE_REBELLION = "gentle_rebellion"
    LONELY_NIGHT = "lonely_night"


class ContentObjective(str, Enum):
    READS = "reads"
    FOLLOWS = "follows"
    DISCUSSION = "discussion"
    CONVERSION = "conversion"


class AudienceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    people: str = Field(min_length=2, max_length=80)
    scene: str = Field(min_length=2, max_length=100)
    recurring_need: str = Field(min_length=2, max_length=120)
    objective: ContentObjective = ContentObjective.READS
    backend_offer: str | None = Field(default=None, max_length=120)


class TopicSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=80)
    source_url: str | None = None
    heat: float = Field(default=0.5, ge=0.0, le=1.0)
    observed_at: datetime | None = None
    creator_followers: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)

    @property
    def low_follower_breakout_score(self) -> float | None:
        if self.creator_followers is None:
            return None
        interactions = (self.likes or 0) + 2 * (self.comments or 0) + 2 * (self.saves or 0)
        return interactions / max(self.creator_followers, 100)


class PerformanceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    post_id: str = Field(min_length=1)
    title: str = ""
    angle: EmotionAngle
    impressions: int | None = Field(default=None, ge=0)
    reads: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    follows: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    published_at: datetime | None = None


class TietuDraft(BaseModel):
    """Copy that can be pasted into the platform's 文字海报/题图 editor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=4, max_length=20)
    lines: tuple[str, ...] = Field(min_length=3, max_length=5)
    interaction_hook: str = Field(min_length=2, max_length=80)
    angle: EmotionAngle
    topic: str = Field(min_length=2, max_length=80)
    pain_point: str = Field(min_length=2, max_length=100)
    viewpoint: str = Field(min_length=2, max_length=160)
    source_urls: tuple[str, ...] = ()
    rationale: str = Field(default="", max_length=300)

    @field_validator("lines")
    @classmethod
    def validate_lines(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(line.strip() for line in value)
        if any(not line or len(line) > 48 for line in cleaned):
            raise ValueError("each poster line must contain 1..48 characters")
        return cleaned

    @property
    def poster_text(self) -> str:
        return "\n".join(self.lines)

    @property
    def description(self) -> str:
        return "\n".join((*self.lines, "", self.interaction_hook))

    @model_validator(mode="after")
    def reject_harmful_growth_hacks(self) -> TietuDraft:
        public_text = " ".join((self.title, *self.lines, self.interaction_hook))
        blocked = (
            "不看后悔",
            "必须转发",
            "震惊全国",
            "百分百",
            "保证有效",
            "速转",
            "你一定要",
            "包治百病",
            "不用看医生",
        )
        if any(term in public_text for term in blocked):
            raise ValueError("deceptive, coercive, or unsupported claims are not allowed")
        if any(term in public_text for term in ("去死", "自杀教程", "伤害自己")):
            raise ValueError("unsafe self-harm content is not allowed")
        return self


class ContentPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    root: Path
    markdown: Path
    json_file: Path
    preview_html: Path
    experiment_file: Path | None = None
