"""Canonical, immutable website-publication and downstream distribution records."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse


@dataclass(frozen=True)
class PublicationEvent:
    event_id: str
    artifact_id: str
    canonical_url: str
    title: str
    summary: str
    topics: list[str]
    published_at: datetime
    content_hash: str
    source_agent: str = "ferro"
    visibility: str = "PUBLIC"

    def __post_init__(self):
        url = urlparse(self.canonical_url)
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("publication event requires an absolute canonical URL")
        if not self.artifact_id or not self.content_hash or not self.event_id:
            raise ValueError("publication event requires an immutable identity")
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise ValueError("published_at must be timezone aware")
        if self.visibility not in {"PUBLIC", "UNLISTED", "PRIVATE"}:
            raise ValueError("invalid publication visibility")

    def model_dump(self):
        value = asdict(self)
        value["published_at"] = self.published_at.astimezone(timezone.utc).isoformat()
        return value


@dataclass
class DistributionTask:
    distribution_id: str
    event_id: str
    channel: str
    status: str = "PENDING"
    attempts: int = 0
    last_error: str | None = None
    published_url: str | None = None
    attempt_times: list[str] = field(default_factory=list)
    published_post_id: str | None = None

    def __post_init__(self):
        if not self.distribution_id or not self.event_id or not self.channel:
            raise ValueError("distribution task requires an identity")
        if self.status not in {"PENDING", "RUNNING", "SUCCESS", "RETRY_PENDING", "FAILED_PERMANENT"}:
            raise ValueError("invalid distribution status")

    def model_dump(self):
        return asdict(self)
