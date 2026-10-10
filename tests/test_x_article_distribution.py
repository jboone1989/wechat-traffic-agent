"""Website article -> X Article distribution adapter tests."""
import asyncio
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


class FakeRuntime:
    def __init__(self, response='{"images": []}'):
        self.response = response
        self.calls = 0

    async def cognitive_probe(self, prompt, max_tokens=2400, output_schema=None):
        self.calls += 1
        return self.response


def test_image_plan_validated_then_cached(tmp_path):
    runtime = FakeRuntime(json.dumps({"images": [
        {"role": "COVER", "mode": "GENERATE", "prompt": "abstract data grid",
         "alt_text": "Abstract cover graphic"}]}))
    artifact = make_artifact()
    folder = tmp_path / "art-1"
    folder.mkdir()
    plan = asyncio.run(x_article_distribution._article_image_plan(runtime, artifact, folder))
    assert plan[0]["mode"] == "GENERATE"
    assert runtime.calls == 1
    assert json.loads((folder / "image_plan.json").read_text(encoding="utf-8"))[0]["mode"] == "GENERATE"
    asyncio.run(x_article_distribution._article_image_plan(runtime, artifact, folder))
    assert runtime.calls == 1


def test_image_plan_two_covers_rejected_and_not_cached(tmp_path):
    runtime = FakeRuntime(json.dumps({"images": [
        {"role": "COVER", "mode": "GRAPHIC", "alt_text": "cover one"},
        {"role": "COVER", "mode": "GRAPHIC", "alt_text": "cover two"}]}))
    folder = tmp_path / "art-1"
    folder.mkdir()
    with pytest.raises(ValueError, match="COVER"):
        asyncio.run(x_article_distribution._article_image_plan(runtime, make_artifact(), folder))
    assert not (folder / "image_plan.json").exists()
