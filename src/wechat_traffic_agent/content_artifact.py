"""Channel-neutral content contracts for Content Agent."""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ContentKind(str, Enum):
    ARTICLE = "article"
    RESEARCH = "research"
    GUIDE = "guide"


class ContentSourceRef(BaseModel):
    """Immutable provenance pointer retained across channel renderers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=80)
    locator: str | None = Field(default=None, max_length=1000)
    revision: str | None = Field(default=None, max_length=200)
    observed_at: datetime | None = None


class ContentSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    heading: str = Field(min_length=1, max_length=160)
    paragraphs: tuple[str, ...] = Field(min_length=1, max_length=40)

    @field_validator("paragraphs")
    @classmethod
    def reject_empty_paragraphs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("content paragraphs must not be empty")
        return cleaned


class ContentArtifact(BaseModel):
    """Canonical publishable content before platform-specific rendering.

    ``public_safe`` is an explicit admission decision, not an inference from the
    source type. Public channels must reject artifacts where it is false.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1, max_length=200)
    kind: ContentKind
    slug: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=4, max_length=180)
    summary: str = Field(min_length=1, max_length=500)
    sections: tuple[ContentSection, ...] = Field(min_length=1, max_length=50)
    source_refs: tuple[ContentSourceRef, ...] = ()
    tags: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime | None = None
    disclosure: str | None = Field(default=None, max_length=1000)
    public_safe: bool = False

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not _SLUG_RE.fullmatch(value):
            raise ValueError("slug must be lowercase ASCII words separated by hyphens")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("content timestamps must be timezone-aware")
        return value
