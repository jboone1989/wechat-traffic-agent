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


class FakePublication:
    post_id = "555"
    url = "https://x.com/ferro/status/555"


class FakeRouter:
    calls = []
    fail_first = False

    def __init__(self, *, state_root):
        pass

    async def publish_article(self, *, title, text, image_path, transaction_path, inline_images=()):
        if FakeRouter.fail_first:
            FakeRouter.fail_first = False
            raise RuntimeError("X unavailable")
        FakeRouter.calls.append({"title": title, "text": text})
        return FakePublication()


@pytest.fixture
def x_enabled(monkeypatch):
    from wechat_traffic_agent.platforms.x import x_browser
    monkeypatch.setattr(x_browser, "x_browser_writes_enabled", lambda: True)
    monkeypatch.setattr(x_browser, "XBrowserTransportRouter", FakeRouter)


async def fake_media(*, draft, folder, **kwargs):
    cover = folder / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"png-bytes")
    return {"cover": {"path": str(cover), "attribution": None}, "inline": []}


def store_article(tmp_path, artifact):
    records = ChannelRecords(tmp_path)
    with records.repo.connect() as db:
        db.execute(
            "INSERT INTO articles(artifact_id,kind,slug,route,title,summary,created_at,updated_at,"
            "content_json,content_sha256,record_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (artifact.artifact_id, artifact.kind.value, artifact.slug,
             "/research/" + artifact.slug, artifact.title, artifact.summary,
             artifact.created_at.isoformat(), NOW.isoformat(),
             artifact.model_dump_json(), "hash", "website"))
        db.commit()


def make_event(store, artifact, event_id, content_hash):
    event = PublicationEvent(event_id, artifact.artifact_id,
                             "https://example.org/research/" + artifact.slug,
                             artifact.title, artifact.summary, list(artifact.tags),
                             NOW, content_hash)
    store.save_event(event)
    return event


def test_publishes_verified_article(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    FakeRouter.calls = []
    row = x_article_distribution.publish_x_article(
        event, DistributionTask("t1", "e1", "x_article"),
        state_root=tmp_path, model_runtime=FakeRuntime())
    assert row == {"status": "POSTED", "url": "https://x.com/ferro/status/555", "post_id": "555"}
    assert FakeRouter.calls[0]["title"] == artifact.title
    assert "Finding" in FakeRouter.calls[0]["text"]
    receipt = json.loads(
        (tmp_path / "content/x_article_distribution/art-1/publication_receipt.json")
        .read_text(encoding="utf-8"))
    assert receipt["post_id"] == "555"


def test_image_plan_drafted_once_across_retries(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    FakeRouter.calls = []
    FakeRouter.fail_first = True
    runtime = FakeRuntime()
    task = DistributionTask("t1", "e1", "x_article")
    with pytest.raises(RuntimeError, match="X unavailable"):
        x_article_distribution.publish_x_article(event, task, state_root=tmp_path, model_runtime=runtime)
    row = x_article_distribution.publish_x_article(event, task, state_root=tmp_path, model_runtime=runtime)
    assert row["status"] == "POSTED"
    assert runtime.calls == 1


def test_revised_event_reuses_original_receipt(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    first = make_event(store, artifact, "e1", "h1")
    second = make_event(store, artifact, "e2", "h2")
    FakeRouter.calls = []
    runtime = FakeRuntime()
    x_article_distribution.publish_x_article(
        first, DistributionTask("t1", "e1", "x_article"),
        state_root=tmp_path, model_runtime=runtime)
    row = x_article_distribution.publish_x_article(
        second, DistributionTask("t2", "e2", "x_article"),
        state_root=tmp_path, model_runtime=runtime)
    assert row["url"] == "https://x.com/ferro/status/555"
    assert len(FakeRouter.calls) == 1


def test_media_failure_propagates_for_retry(tmp_path, x_enabled, monkeypatch):
    async def broken_media(*, draft, folder, **kwargs):
        raise RuntimeError("no licensed image found")

    monkeypatch.setattr(x_article_distribution, "prepare_article_media", broken_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    with pytest.raises(RuntimeError, match="no licensed image"):
        x_article_distribution.publish_x_article(
            event, DistributionTask("t1", "e1", "x_article"),
            state_root=tmp_path, model_runtime=FakeRuntime())


def test_oversized_text_is_not_retryable(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact(sections=(ContentSection(
        heading="Long", paragraphs=("word " * 6000,)),))
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    with pytest.raises(NonRetryablePublication):
        x_article_distribution.publish_x_article(
            event, DistributionTask("t1", "e1", "x_article"),
            state_root=tmp_path, model_runtime=FakeRuntime())


def test_writes_disabled_is_rejected(tmp_path, monkeypatch):
    from wechat_traffic_agent.platforms.x import x_browser
    monkeypatch.setattr(x_browser, "x_browser_writes_enabled", lambda: False)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    with pytest.raises(RuntimeError, match="disabled"):
        x_article_distribution.publish_x_article(
            event, DistributionTask("t1", "e1", "x_article"),
            state_root=tmp_path, model_runtime=FakeRuntime())


def test_inline_caption_and_credits_are_injected(tmp_path, x_enabled, monkeypatch):
    async def media_with_inline(*, draft, folder, **kwargs):
        cover = folder / "cover.png"
        cover.write_bytes(b"png")
        inline_png = folder / "inline.png"
        inline_png.write_bytes(b"png2")
        return {"cover": {"path": str(cover), "attribution": None},
                "inline": [{"path": str(inline_png), "caption": "Figure 1: flow",
                            "alt_text": "Diagram of the flow",
                            "after_text": "Something concrete and supported.",
                            "attribution": "Photo by Someone, CC BY 4.0"}]}

    monkeypatch.setattr(x_article_distribution, "prepare_article_media", media_with_inline)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    FakeRouter.calls = []
    x_article_distribution.publish_x_article(
        event, DistributionTask("t1", "e1", "x_article"),
        state_root=tmp_path, model_runtime=FakeRuntime())
    text = FakeRouter.calls[0]["text"]
    assert "Something concrete and supported.\n\nFigure 1: flow" in text
    assert "Image credits:\nPhoto by Someone, CC BY 4.0" in text
