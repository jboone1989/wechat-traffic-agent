"""Thin, platform-neutral internet-source contracts.

PR2 establishes only the contracts WeChat currently needs. No universal
internet framework: providers adapt existing OSS tools; data a platform
does not have is always None (unavailable), never a forced zero.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict


class SourceStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


T = TypeVar("T")


class SourceResult(BaseModel, Generic[T]):
    """One provider call with provenance and failure isolation.

    One platform failing must never abort a scan: callers collect
    SourceResult objects and handle FAILED/UNAVAILABLE per platform.
    FAILED/UNAVAILABLE results carry ``items=()`` for tuple-typed sources
    and a minimal degenerate instance for scalar-typed sources (e.g. a
    ContentSnapshot with empty text); consumers must branch on ``status``
    before touching ``items``. ``observed_at`` is the batch call time;
    item-level ``observed_at`` fields, where present, are per item.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str  # e.g. "dailyhot", "agent-reach", "fixture"
    platform: str  # e.g. "weibo", "zhihu", "generic_web"
    status: SourceStatus
    items: T
    error: str | None = None
    observed_at: datetime


class HotItem(BaseModel):
    """One hot-list entry, as observed at one point in time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str  # id of this item within its platform
    platform: str
    title: str
    url: str | None = None
    rank: int | None = None
    observed_at: datetime | None = None
    raw_hot_value: float | None = None  # 平台原始热度，禁止跨平台横比


class ContentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str
    platform: str
    url: str
    title: str | None = None
    text: str
    author_id: str | None = None
    published_at: datetime | None = None


class EngagementSnapshot(BaseModel):
    """PR2 defines the contract; PR3's deep fetch produces the first values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str
    platform: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    observed_at: datetime | None = None


class AuthorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author_id: str
    platform: str
    followers: int | None = None


class Comment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str
    platform: str
    text: str
    comment_id: str | None = None
    author_id: str | None = None
    likes: int | None = None


# fetch 的 platform 是唯一主参数，保持位置参数；read/comments/author
# 已有资源标识符作主参数，platform 统一 keyword-only 防错位。
class HotFeedProvider(Protocol):
    async def fetch(self, platform: str) -> SourceResult[tuple[HotItem, ...]]: ...


class ContentReader(Protocol):
    async def read(
        self, url: str, *, platform: str = "generic_web"
    ) -> SourceResult[ContentSnapshot]: ...


class CommentReader(Protocol):
    async def comments(
        self, content_id: str, *, platform: str
    ) -> SourceResult[tuple[Comment, ...]]: ...


class AuthorReader(Protocol):
    async def author(
        self, author_id: str, *, platform: str
    ) -> SourceResult[AuthorSnapshot]: ...
