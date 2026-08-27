from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    EngagementReader,
    EngagementSnapshot,
    HotItem,
    SourceResult,
    SourceStatus,
)


def test_hot_item_missing_fields_default_to_none() -> None:
    item = HotItem(source_id="h1", platform="weibo", title="标题")
    assert item.url is None
    assert item.rank is None
    assert item.observed_at is None
    assert item.raw_hot_value is None


def test_content_snapshot_round_trips() -> None:
    snapshot = ContentSnapshot(
        content_id="c1",
        platform="generic_web",
        url="https://example.com/a",
        author_id=None,
        title="标题",
        text="正文",
        published_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )
    restored = ContentSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot


def test_engagement_author_comment_allow_all_missing() -> None:
    engagement = EngagementSnapshot(content_id="c1", platform="weibo")
    assert all(
        value is None
        for value in (
            engagement.views,
            engagement.likes,
            engagement.comments,
            engagement.shares,
            engagement.saves,
        )
    )
    author = AuthorSnapshot(author_id="a1", platform="bilibili")
    assert author.followers is None
    comment = Comment(content_id="c1", platform="zhihu", text="评论")
    assert comment.author_id is None
    assert comment.likes is None


def test_source_result_carries_provenance_and_is_frozen() -> None:
    observed_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    result = SourceResult(
        source="fixture",
        platform="weibo",
        status=SourceStatus.OK,
        items=(HotItem(source_id="h1", platform="weibo", title="标题"),),
        error=None,
        observed_at=observed_at,
    )
    assert result.source == "fixture"
    assert result.platform == "weibo"
    assert result.error is None
    assert result.observed_at == observed_at
    assert len(result.items) == 1
    with pytest.raises((TypeError, ValidationError)):
        result.status = SourceStatus.FAILED  # frozen
    with pytest.raises(ValidationError):
        SourceResult(
            source="fixture",
            platform="weibo",
            status=SourceStatus.OK,
            items=(),
            observed_at=observed_at,
            unknown_field=1,  # extra="forbid"：拼错字段必须炸
        )


def test_engagement_reader_protocol_is_structural() -> None:
    class _Reader:
        async def engagement(
            self, content_id: str, *, platform: str
        ) -> SourceResult[EngagementSnapshot]: ...

    reader: EngagementReader = _Reader()  # 结构化满足协议即可
