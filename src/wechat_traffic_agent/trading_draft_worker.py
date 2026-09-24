"""Bounded Trading-research draft worker for the persistent Content Agent."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from .content_artifact import ContentKind
from .content_service import ContentAgent
from .draft_inbox import ContentDraftInbox, ContentDraftRecord, topic_set_fingerprint
from .models import AudienceProfile, ContentObjective
from .sources.trading_network import TradingResearchNetworkSource


@dataclass(frozen=True)
class TradingDraftResult:
    status: str
    source_fingerprint: str | None = None
    artifact_id: str | None = None
    draft_path: str | None = None
    topic_count: int = 0

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class TradingResearchDraftWorker:
    """Create at most one non-public draft for each stable Trading research snapshot."""

    def __init__(
        self,
        *,
        runtime: Any,
        state_root: Path,
        audience: AudienceProfile | None = None,
        topic_limit: int = 20,
    ) -> None:
        if topic_limit < 1 or topic_limit > 100:
            raise ValueError("topic_limit must be between 1 and 100")
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.audience = audience or default_trading_research_audience()
        self.topic_limit = topic_limit
        self.inbox = ContentDraftInbox(self.state_root / "content")

    async def run_once(self, *, now: datetime | None = None) -> TradingDraftResult:
        generated_at = now or datetime.now(timezone.utc)
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("draft generation timestamp must be timezone-aware")
        network = _network_client(self.state_root)
        topics = await TradingResearchNetworkSource(network).fetch_topics(limit=self.topic_limit)
        if not topics:
            return TradingDraftResult(status="NO_RESEARCH", topic_count=0)

        fingerprint = topic_set_fingerprint(topics)
        if self.inbox.contains(fingerprint):
            existing = self.inbox.load(fingerprint)
            return TradingDraftResult(
                status="ALREADY_DRAFTED",
                source_fingerprint=fingerprint,
                artifact_id=existing.artifact.artifact_id,
                draft_path=str(self.inbox.path_for(fingerprint)),
                topic_count=len(topics),
            )

        artifact_id = f"trading-research-{fingerprint[:20]}"
        artifact = await ContentAgent(self.runtime).compose(
            topics,
            self.audience,
            artifact_id=artifact_id,
            slug=None,
            kind=ContentKind.RESEARCH,
            now=generated_at,
        )
        record = ContentDraftRecord(
            source_fingerprint=fingerprint,
            generated_at=generated_at,
            artifact=artifact,
        )
        path = self.inbox.save(record)
        self.runtime.persist()
        return TradingDraftResult(
            status="DRAFT_CREATED",
            source_fingerprint=fingerprint,
            artifact_id=artifact.artifact_id,
            draft_path=str(path),
            topic_count=len(topics),
        )


def default_trading_research_audience() -> AudienceProfile:
    return AudienceProfile(
        people="Readers evaluating automated trading and AI systems",
        scene="Researching how trading ideas are tested before anyone should trust them",
        recurring_need="Evidence-aware explanations of validation, uncertainty, reliability, and failure modes",
        objective=ContentObjective.READS,
    )


def trading_draft_worker_enabled() -> bool:
    return (os.environ.get("CONTENT_TRADING_DRAFTS_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }


def _network_seed_urls_from_environment() -> tuple[str, ...]:
    raw_seed_urls = os.environ.get("CONTENT_NETWORK_SEED_URLS")
    seed_values = (
        tuple(item.strip() for item in raw_seed_urls.split(",") if item.strip())
        if raw_seed_urls is not None
        else (os.environ.get("CONTENT_NETWORK_SEED_URL", "http://127.0.0.1:8001"),)
    )
    seed_urls = tuple(dict.fromkeys(item.strip().rstrip("/") for item in seed_values if item.strip()))
    if not seed_urls:
        raise ValueError("CONTENT_NETWORK_SEED_URLS must contain at least one non-empty URL")
    return seed_urls


def _network_client(state_root: Path):
    """Create an outbound-only Network client using Content's persistent identity."""
    from agent_network.crypto import Identity
    from agent_network.network import AgentNetwork

    agent_id = os.environ.get("CONTENT_NETWORK_AGENT_ID", "content.cai.001")
    seed_urls = _network_seed_urls_from_environment()
    host = os.environ.get("CONTENT_NETWORK_ADVERTISE_HOST") or os.environ.get("CONTENT_NETWORK_HOST", "127.0.0.1")
    port = int(os.environ.get("CONTENT_NETWORK_PORT", "8103"))
    endpoint = f"http://{host}:{port}/a2a"
    card_url = f"http://{host}:{port}/.well-known/agent-card.json"
    identity = Identity.load_or_create(
        Path(state_root) / "application" / "network" / "identity.json",
        agent_id,
        secret_path=Path(state_root) / "secrets" / "network-identity.json",
    )
    return AgentNetwork(
        identity=identity,
        seeds=list(seed_urls),
        agent_card_url=card_url,
        endpoint=endpoint,
        capabilities=[],
    )


__all__ = [
    "TradingDraftResult",
    "TradingResearchDraftWorker",
    "default_trading_research_audience",
    "trading_draft_worker_enabled",
]
