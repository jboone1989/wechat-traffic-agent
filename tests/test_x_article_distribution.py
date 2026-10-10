"""Website article -> X Article distribution adapter tests."""
import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from wechat_traffic_agent.platforms.x import x_article_distribution
from wechat_traffic_agent.publication.errors import NonRetryablePublication
from wechat_traffic_agent.publication.event_store import PublicationEventStore
from wechat_traffic_agent.publication.models import DistributionTask, PublicationEvent

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def make_artifact(**overrides):
    values = dict(
        artifact_id="art-1", kind=ContentKind.RESEARCH, slug="example-research",
        title="Example Research Finding",
        summary="A supported finding with a concrete mechanism.",
        sections=(ContentSection(heading="Finding",
                                 paragraphs=("Something concrete and supported.",)),),
        created_at=NOW,
    )
    values.update(overrides)
    return ContentArtifact(**values)


def test_render_includes_headings_and_paragraphs():
    artifact = make_artifact(sections=(
        ContentSection(heading="Finding", paragraphs=("First supported point.", "Second point.")),
        ContentSection(heading="Limits", paragraphs=("Where it stops.",)),
    ))
    text = x_article_distribution.render_article_text(artifact)
    assert text == ("Finding\n\nFirst supported point.\n\nSecond point.\n\n"
                    "Limits\n\nWhere it stops.")
