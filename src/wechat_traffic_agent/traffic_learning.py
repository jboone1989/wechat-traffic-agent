"""Traffic sensing and strategy feedback for the autonomous Website loop."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .channel_evidence import ChannelEvidenceStore
from .content_admission import _write_once
from .web_editorial import digest

SEARCH_SCHEMA = "website-search-demand.v1"
SEARCH_SOURCE = "google-search-console:v1"
PAGEVIEW_SOURCE = "nginx-access-log:v1"
PAGEVIEW_METRIC = "http_pageviews"
PAGEVIEW_WINDOW = timedelta(hours=24)


class SearchDemandObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal["website-search-demand.v1"] = SEARCH_SCHEMA
    site: str = Field(min_length=1, max_length=300)
    query: str = Field(min_length=1, max_length=500)
    page: str | None = Field(default=None, max_length=1000)
    clicks: int = Field(ge=0)
    impressions: int = Field(ge=0)
    ctr: float = Field(ge=0, le=1)
    position: float | None = Field(default=None, ge=0)
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    revision: int = Field(ge=1, le=10000, strict=True)
    source: Literal["google-search-console:v1"] = SEARCH_SOURCE
    evidence_ref: str = Field(min_length=1, max_length=1000)

    @field_validator("window_start", "window_end", "observed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("search-demand timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def valid_measurement(self):
        if self.clicks > self.impressions:
            raise ValueError("clicks cannot exceed impressions")
        if not self.window_start < self.window_end <= self.observed_at:
            raise ValueError("require window_start < window_end <= observed_at")
        expected = (self.clicks / self.impressions) if self.impressions else 0.0
        if abs(self.ctr - expected) > 1e-6:
            raise ValueError("ctr must match clicks/impressions")
        return self

    def identity(self) -> dict:
        return {
            "site": self.site.rstrip("/"),
            "query": self.query,
            "page": self.page,
            "window_start": self.window_start.astimezone(timezone.utc).isoformat(),
            "window_end": self.window_end.astimezone(timezone.utc).isoformat(),
            "source": self.source,
        }


class SearchDemandStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def record(self, row: SearchDemandObservation) -> Path:
        row = SearchDemandObservation.model_validate_json(row.model_dump_json())
        key = digest(row.identity())
        path = self.root / key / f"{row.revision:05d}.json"
        _write_once(path, row.model_dump_json(indent=2) + "\n")
        return path

    def as_of(self, cutoff: datetime) -> list[SearchDemandObservation]:
        if cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        latest: dict[str, SearchDemandObservation] = {}
        for path in sorted(self.root.glob("*/*.json")):
            row = SearchDemandObservation.model_validate_json(path.read_text(encoding="utf-8"))
            key = digest(row.identity())
            if path.parent.name != key or path.name != f"{row.revision:05d}.json":
                raise ValueError("search-demand filename/identity mismatch")
            if row.observed_at > cutoff:
                continue
            if key not in latest or row.revision > latest[key].revision:
                latest[key] = row
        return list(latest.values())


class TopicTrafficSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str
    article_count: int = Field(ge=0)
    orphan_article_count: int = Field(ge=0)
    search_rows: int = Field(ge=0)
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    ctr: float | None = Field(default=None, ge=0, le=1)
    average_position: float | None = Field(default=None, ge=0)
    pageview_samples_24h: int = Field(ge=0)
    mean_pageviews_24h: float | None = Field(default=None, ge=0)
    trend_hits: int = Field(default=0, ge=0)
    demand_score: float = Field(default=0.0, ge=0, le=1)
    priority_score: float = Field(default=0.0, ge=0, le=2)
    exposure_status: Literal["UNKNOWN", "NO_IMPRESSIONS", "NO_CLICKS", "HAS_CLICKS"]


class TrafficLearningContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-traffic-learning-context.v1"] = "website-traffic-learning-context.v1"
    as_of: datetime
    search_data_status: Literal["NO_DATA", "AVAILABLE"]
    pageview_data_status: Literal["NO_DATA", "AVAILABLE"]
    topics: tuple[TopicTrafficSignal, ...]
    unmapped_search_rows: int = Field(ge=0)
    interpretation_rules: tuple[str, ...]
    control_authority: Literal[False] = False


class TrafficLearningContextBuilder:
    def __init__(self, *, state_root: Path, account: str) -> None:
        self.state_root = Path(state_root)
        self.account = str(account).strip().rstrip("/")
        if not self.account:
            raise ValueError("traffic-learning account must be non-empty")
        self.search = SearchDemandStore(self.state_root / "content" / "search_demand")
        self.channel = ChannelEvidenceStore(self.state_root / "content" / "channel_evidence")

    def build(self, *, website: dict, as_of: datetime, trend_candidates: list[dict] | tuple[dict, ...] = ()) -> TrafficLearningContext:
        if as_of.utcoffset() is None:
            raise ValueError("traffic-learning cutoff must be timezone-aware")
        as_of = as_of.astimezone(timezone.utc)
        search_rows = [row for row in self.search.as_of(as_of) if row.site.rstrip("/") == self.account]
        pageviews = [
            row for row in self.channel.as_of(as_of)
            if row.channel == "website"
            and row.account.rstrip("/") == self.account
            and row.metric == PAGEVIEW_METRIC
            and row.source == PAGEVIEW_SOURCE
            and row.value is not None
            and row.agent_candidate_sha256 == row.published_content_sha256
            and row.window_end - row.window_start == PAGEVIEW_WINDOW
        ]
        route_topics: dict[str, set[str]] = {}
        for row in website["articles"]:
            route = str(row["route"])
            for tag in row["artifact"].get("tags") or ():
                topic = str(tag).strip().casefold().replace(" ", "-")
                if topic in website["topics"]:
                    route_topics.setdefault(route, set()).add(topic)
        topic_search: dict[str, list[SearchDemandObservation]] = {topic: [] for topic in website["topics"]}
        unmapped = 0
        for row in search_rows:
            route = _route_from_page(row.page)
            topics = route_topics.get(route, set()) if route else set()
            if not topics:
                unmapped += 1
                continue
            for topic in topics:
                topic_search[topic].append(row)
        topic_pageviews: dict[str, list[float]] = {topic: [] for topic in website["topics"]}
        for row in pageviews:
            if row.topic in topic_pageviews:
                topic_pageviews[row.topic].append(float(row.value))

        trend_hits = {topic: 0 for topic in website["topics"]}
        for candidate in tuple(trend_candidates)[:60]:
            text = str(candidate.get("title") or "") + " " + str(candidate.get("summary") or "")
            for topic in website["topics"]:
                if _topic_match(topic, text):
                    trend_hits[topic] += 1
        max_impressions = max((sum(row.impressions for row in topic_search[t]) for t in topic_search), default=0)
        max_trends = max(trend_hits.values(), default=0)

        signals = []
        inbound = website.get("inbound_counts", {})
        for topic in sorted(website["topics"]):
            rows = topic_search[topic]
            impressions = sum(row.impressions for row in rows)
            clicks = sum(row.clicks for row in rows)
            weighted_position = (
                sum((row.position or 0.0) * row.impressions for row in rows if row.position is not None)
                / impressions if impressions else None
            )
            if not rows:
                exposure = "UNKNOWN"
            elif impressions == 0:
                exposure = "NO_IMPRESSIONS"
            elif clicks == 0:
                exposure = "NO_CLICKS"
            else:
                exposure = "HAS_CLICKS"
            values = topic_pageviews[topic]
            article_ids = website["topics"][topic]
            orphan_count = sum(1 for article_id in article_ids if inbound.get(article_id, 0) == 0)
            demand_score = (impressions / max_impressions) if max_impressions else 0.0
            trend_score = (trend_hits[topic] / max_trends) if max_trends else 0.0
            coverage_gap = 1.0 / (1.0 + len(article_ids))
            link_gap = orphan_count / max(1, len(article_ids))
            unrealized = ((impressions - clicks) / impressions) if impressions else 0.0
            priority = (0.35 * demand_score + 0.30 * trend_score + 0.20 * coverage_gap
                        + 0.10 * link_gap + 0.05 * unrealized)
            signals.append(TopicTrafficSignal(
                topic=topic, article_count=len(article_ids),
                orphan_article_count=orphan_count, search_rows=len(rows),
                impressions=impressions, clicks=clicks,
                ctr=(clicks / impressions if impressions else (0.0 if rows else None)),
                average_position=weighted_position,
                pageview_samples_24h=len(values),
                mean_pageviews_24h=(sum(values) / len(values) if values else None),
                trend_hits=trend_hits[topic], demand_score=demand_score,
                priority_score=round(priority, 9), exposure_status=exposure,
            ))
        return TrafficLearningContext(
            as_of=as_of,
            search_data_status="AVAILABLE" if search_rows else "NO_DATA",
            pageview_data_status="AVAILABLE" if pageviews else "NO_DATA",
            topics=tuple(signals),
            unmapped_search_rows=unmapped,
            interpretation_rules=(
                "NO_DATA is unknown, never zero demand.",
                "NO_IMPRESSIONS means Google supplied rows but no exposure in the measured window.",
                "NO_CLICKS with impressions means exposure existed but search clicks did not.",
                "Nginx pageviews measure eligible HTTP requests, not unique or engaged readers.",
                "Traffic signals guide content strategy; they do not weaken grounding or Evidence Floor checks.",
            ),
        )


def _route_from_page(page: str | None) -> str | None:
    if not page:
        return None
    parsed = urlsplit(page)
    route = parsed.path if parsed.scheme or parsed.netloc else page
    if not route.startswith("/"):
        route = "/" + route
    return route if route.endswith("/") else route + "/"


def _topic_match(topic: str, text: str) -> bool:
    import re

    def tokens(value: str) -> set[str]:
        raw = re.findall(r"[a-z0-9]+", value.casefold().replace("_", " ").replace("-", " "))
        return {
            token[:-1] if token.endswith("s") and len(token) > 4 else token
            for token in raw if len(token) > 2
        }

    wanted, seen = tokens(topic), tokens(text)
    return bool(wanted) and len(wanted & seen) >= max(1, (len(wanted) + 1) // 2)
