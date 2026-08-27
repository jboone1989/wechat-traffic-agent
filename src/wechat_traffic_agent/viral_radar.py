"""Viral Radar: what content is abnormally succeeding right now.

Scan pipeline (all numeric scoring is deterministic — no LLM ranks numbers):
hot scan -> normalize + dedup -> platform-local ranking -> Top-N selection
-> deep fetch (read + author + engagement) -> ViralCandidate with weighted
sub-scores (rank .35 / engagement .25 / breakout .25 / freshness .15,
re-normalized over whatever is actually available; unavailable is never 0).

Known v1 limitation: engagement/breakout/freshness percentiles are computed
over the deep-fetched Top-N of each platform, not the full hot list.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from .scoring import (
    breakout_ratio,
    combine_final,
    dedup_items,
    engagement_value,
    normalize_title,
    percentile_score,
    rank_score,
)
from .sources import (
    AuthorSnapshot,
    ContentSnapshot,
    EngagementSnapshot,
    HotItem,
    SourceStatus,
)

EXCERPT_LENGTH = 120


class ViralCandidate(BaseModel):
    """One content item that is abnormally succeeding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    platform: str
    content_id: str
    url: str | None = None
    title: str
    text_excerpt: str | None = None
    rank: int | None = None
    published_at: datetime | None = None
    observed_at: datetime
    author_id: str | None = None
    author_followers: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    absolute_hot_score: float | None = None
    rank_score: float | None = None
    engagement_score: float | None = None
    breakout_score: float | None = None
    freshness_score: float | None = None
    cross_platform_score: float = 0.0
    final_score: float | None = None
    evidence_refs: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class PlatformScan(BaseModel):
    """Per-platform hot-scan provenance and dedup accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    source: str
    status: SourceStatus
    items_fetched: int
    items_after_dedup: int = 0
    error: str | None = None


class ScanReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scan_id: str
    observed_at: datetime
    platforms: tuple[PlatformScan, ...]
    candidates: tuple[ViralCandidate, ...]


@dataclass
class _DeepResult:
    platform: str
    item: HotItem
    content: ContentSnapshot | None = None
    author: AuthorSnapshot | None = None
    engagement: EngagementSnapshot | None = None
    evidence: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ViralRadar:
    """Deterministic hot scan + Top-N deep fetch -> ViralCandidates."""

    def __init__(
        self,
        *,
        hot_provider,
        reader,
        platforms: Sequence[str],
        top_n: int = 20,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.hot_provider = hot_provider
        self.reader = reader
        self.platforms = tuple(platforms)
        self.top_n = top_n
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def scan(self) -> ScanReport:
        now = self._now()
        fetches = await asyncio.gather(
            *(self.hot_provider.fetch(platform) for platform in self.platforms)
        )

        platform_scans: list[PlatformScan] = []
        deduped: dict[str, tuple[HotItem, ...]] = {}
        for platform, result in zip(self.platforms, fetches):
            if result.status is not SourceStatus.OK:
                platform_scans.append(
                    PlatformScan(
                        platform=platform,
                        source=result.source,
                        status=result.status,
                        items_fetched=0,
                        error=result.error,
                    )
                )
                continue
            items = dedup_items(result.items)
            deduped[platform] = items
            platform_scans.append(
                PlatformScan(
                    platform=platform,
                    source=result.source,
                    status=result.status,
                    items_fetched=len(result.items),
                    items_after_dedup=len(items),
                )
            )

        cross_scores = self._cross_platform_scores(deduped)
        selected = self._select_top(deduped)
        deep = await asyncio.gather(
            *(self._deep_fetch(platform, item) for platform, item in selected)
        )
        candidates = self._build_candidates(deep, deduped, cross_scores, now)
        return ScanReport(
            scan_id=uuid.uuid4().hex,
            observed_at=now,
            platforms=tuple(platform_scans),
            candidates=candidates,
        )

    def _cross_platform_scores(
        self, deduped: dict[str, tuple[HotItem, ...]]
    ) -> dict[str, float]:
        """Same normalized title appearing on several platforms (not merged)."""
        platform_count = len(deduped)
        if platform_count <= 1:
            return {}
        clusters: dict[str, set[str]] = {}
        for platform, items in deduped.items():
            for item in items:
                clusters.setdefault(normalize_title(item.title), set()).add(platform)
        # 单平台标题没有跨平台热度，不计分（get 默认 0.0）
        return {
            title: len(platforms) / platform_count
            for title, platforms in clusters.items()
            if len(platforms) > 1
        }

    def _select_top(
        self, deduped: dict[str, tuple[HotItem, ...]]
    ) -> list[tuple[str, HotItem]]:
        entries: list[tuple[float | None, str, HotItem]] = []
        for platform, items in deduped.items():
            population_size = len(items)
            for item in items:
                entries.append((rank_score(item.rank, population_size), platform, item))
        entries.sort(
            key=lambda e: (e[0] is None, -(e[0] or 0.0), e[1], e[2].source_id)
        )
        return [(platform, item) for _, platform, item in entries[: self.top_n]]

    async def _deep_fetch(self, platform: str, item: HotItem) -> _DeepResult:
        result = _DeepResult(
            platform=platform, item=item, evidence=(f"hot:{platform}:{item.source_id}",)
        )
        content_id = item.source_id
        if item.url:
            read = await self.reader.read(item.url, platform=platform)
            if read.status is SourceStatus.OK:
                result.content = read.items
                content_id = read.items.content_id
                result.evidence = result.evidence + (f"read:{platform}:{content_id}",)
            else:
                result.errors = result.errors + (
                    f"read:{read.error or read.status.value}",
                )
        if result.content is not None and result.content.author_id:
            author = await self.reader.author(result.content.author_id, platform=platform)
            if author.status is SourceStatus.OK:
                result.author = author.items
                result.evidence = result.evidence + (
                    f"author:{platform}:{author.items.author_id}",
                )
            else:
                result.errors = result.errors + (
                    f"author:{author.error or author.status.value}",
                )
        engagement_reader = getattr(self.reader, "engagement", None)
        if engagement_reader is not None:
            engagement = await engagement_reader(content_id, platform=platform)
            if engagement.status is SourceStatus.OK:
                result.engagement = engagement.items
                result.evidence = result.evidence + (
                    f"engagement:{platform}:{content_id}",
                )
            else:
                result.errors = result.errors + (
                    f"engagement:{engagement.error or engagement.status.value}",
                )
        return result

    def _build_candidates(
        self,
        deep: Sequence[_DeepResult],
        deduped: dict[str, tuple[HotItem, ...]],
        cross_scores: dict[str, float],
        now: datetime,
    ) -> tuple[ViralCandidate, ...]:
        by_platform: dict[str, list[_DeepResult]] = {}
        for result in deep:
            by_platform.setdefault(result.platform, []).append(result)

        built: list[ViralCandidate] = []
        for platform, group in by_platform.items():
            hot_items = deduped[platform]
            raw_hots = [
                item.raw_hot_value for item in hot_items if item.raw_hot_value is not None
            ]
            eng_values = [
                value
                for value in (
                    engagement_value(result.engagement)
                    for result in group
                    if result.engagement is not None
                )
                if value is not None
            ]
            ratios = [
                ratio
                for ratio in (
                    breakout_ratio(
                        engagement_value(result.engagement)
                        if result.engagement is not None
                        else None,
                        result.author.followers if result.author is not None else None,
                    )
                    for result in group
                )
                if ratio is not None
            ]
            ages = [
                self._age_seconds(result.content.published_at, now)
                for result in group
                if result.content is not None and result.content.published_at is not None
            ]
            for result in group:
                built.append(
                    self._build_candidate(
                        result,
                        population_size=len(hot_items),
                        raw_hots=raw_hots,
                        eng_values=eng_values,
                        ratios=ratios,
                        ages=ages,
                        now=now,
                        cross_scores=cross_scores,
                    )
                )
        built.sort(
            key=lambda c: (
                c.final_score is None,
                -(c.final_score or 0.0),
                c.rank is None,
                c.rank or 0,
                c.platform,
                c.candidate_id,
            )
        )
        return tuple(built)

    def _age_seconds(self, published_at: datetime, now: datetime) -> float:
        published = (
            published_at
            if published_at.tzinfo is not None
            else published_at.replace(tzinfo=timezone.utc)
        )
        return (now - published).total_seconds()

    def _build_candidate(
        self,
        result: _DeepResult,
        *,
        population_size: int,
        raw_hots: list[float],
        eng_values: list[int],
        ratios: list[float],
        ages: list[float],
        now: datetime,
        cross_scores: dict[str, float],
    ) -> ViralCandidate:
        item = result.item
        content = result.content
        author = result.author
        engagement = result.engagement
        content_id = content.content_id if content is not None else item.source_id
        eng = engagement_value(engagement) if engagement is not None else None
        ratio = breakout_ratio(
            eng, author.followers if author is not None else None
        )
        age = (
            self._age_seconds(content.published_at, now)
            if content is not None and content.published_at is not None
            else None
        )

        subscores: dict[str, float | None] = {
            "rank": rank_score(item.rank, population_size),
            "engagement": percentile_score(eng, eng_values) if eng is not None else None,
            "breakout": percentile_score(ratio, ratios) if ratio is not None else None,
            "freshness": (
                percentile_score(-age, [-a for a in ages]) if age is not None else None
            ),
        }
        return ViralCandidate(
            candidate_id=f"{item.platform}:{content_id}",
            platform=item.platform,
            content_id=content_id,
            url=item.url,
            title=item.title,
            text_excerpt=(
                content.text[:EXCERPT_LENGTH]
                if content is not None and content.text
                else None
            ),
            rank=item.rank,
            published_at=content.published_at if content is not None else None,
            observed_at=now,
            author_id=(
                author.author_id
                if author is not None
                else (content.author_id if content is not None else None)
            ),
            author_followers=author.followers if author is not None else None,
            views=engagement.views if engagement is not None else None,
            likes=engagement.likes if engagement is not None else None,
            comments=engagement.comments if engagement is not None else None,
            shares=engagement.shares if engagement is not None else None,
            saves=engagement.saves if engagement is not None else None,
            absolute_hot_score=(
                percentile_score(item.raw_hot_value, raw_hots)
                if item.raw_hot_value is not None
                else None
            ),
            rank_score=subscores["rank"],
            engagement_score=subscores["engagement"],
            breakout_score=subscores["breakout"],
            freshness_score=subscores["freshness"],
            cross_platform_score=cross_scores.get(normalize_title(item.title), 0.0),
            final_score=combine_final(subscores),
            evidence_refs=result.evidence,
            errors=result.errors,
        )
