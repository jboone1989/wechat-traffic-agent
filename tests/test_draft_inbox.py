from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.draft_inbox import ContentDraftInbox, ContentDraftRecord, topic_set_fingerprint
from wechat_traffic_agent.models import TopicSignal


def _topic(title: str, ref: str) -> TopicSignal:
    return TopicSignal(
        title=title,
        summary="Evidence-backed research summary.",
        source="agent-network:trading.cai.001",
        source_revision="artifact-1@1",
        evidence_refs=(ref,),
        observed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
    )


def _artifact(*, public_safe: bool = False) -> ContentArtifact:
    return ContentArtifact(
        artifact_id="draft-1",
        kind=ContentKind.RESEARCH,
        slug="draft-one",
        title="A research draft",
        summary="A draft that still requires editorial admission.",
        sections=(ContentSection(heading="Evidence", paragraphs=("Draft body.",)),),
        created_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        public_safe=public_safe,
    )


def test_topic_set_fingerprint_is_order_invariant_and_evidence_sensitive():
    first = _topic("First topic", "trading:report:1")
    second = _topic("Second topic", "trading:report:2")
    assert topic_set_fingerprint([first, second]) == topic_set_fingerprint([second, first])
    changed = second.model_copy(update={"evidence_refs": ("trading:report:3",)})
    assert topic_set_fingerprint([first, second]) != topic_set_fingerprint([first, changed])


def test_draft_inbox_is_write_once_and_refuses_public_artifacts(tmp_path):
    inbox = ContentDraftInbox(tmp_path)
    fingerprint = "a" * 64
    record = ContentDraftRecord(
        source_fingerprint=fingerprint,
        generated_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        artifact=_artifact(),
    )
    path = inbox.save(record)
    assert path.stat().st_mode & 0o777 == 0o600
    assert inbox.load(fingerprint) == record
    with pytest.raises(FileExistsError):
        inbox.save(record)
    with pytest.raises(ValueError, match="refuses public-safe"):
        ContentDraftRecord(
            source_fingerprint="b" * 64,
            generated_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
            artifact=_artifact(public_safe=True),
        )
