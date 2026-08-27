"""BilibiliCliReader offline tests: scripted CLI envelopes, no network."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from wechat_traffic_agent.agent_reach import AgentReachProvider
from wechat_traffic_agent.bilibili import BilibiliCliReader, extract_video_id
from wechat_traffic_agent.sources import SourceStatus

VIDEO_PAYLOAD = {
    "ok": True,
    "data": {
        "bvid": "BV1xx411c7mD",
        "title": "十分钟看懂三线小城的早餐江湖",
        "desc": "这是一段视频简介，记录了摊主的一天。",
        "owner": {"mid": 42},
        "pubdate": 1756278000,
        "stat": {
            "view": 100000,
            "like": 9000,
            "reply": 700,
            "share": 500,
            "favorite": 1500,
        },
    },
}
USER_PAYLOAD = {"ok": True, "data": {"user": {"mid": 42, "follower": 321}}}
COMMENTS_PAYLOAD = {
    "ok": True,
    "data": {
        "comments": [
            {"rpid": 7, "content": {"message": "前排围观"}, "like": 10},
        ]
    },
}


class FakeRunner:
    """按 bilibili-cli 动词路由的脚本化 runner（离线覆盖全部通道）。"""

    def __init__(
        self,
        *,
        video: object | None = VIDEO_PAYLOAD,
        user: object | None = USER_PAYLOAD,
        comments: object | None = COMMENTS_PAYLOAD,
        jina_text: str = "",
        video_returncode: int = 0,
        video_stdout_override: str | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._video = video
        self._user = user
        self._comments = comments
        self._jina_text = jina_text
        self._video_rc = video_returncode
        self._video_stdout = video_stdout_override

    async def __call__(self, cmd: list[str]) -> tuple[int, str, str]:
        joined = " ".join(cmd)
        self.calls.append(cmd)
        if joined.startswith("curl"):
            return 0, self._jina_text, ""
        if "--comments" in joined:
            return 0, json.dumps(self._comments, ensure_ascii=False), ""
        if cmd[0] == "bili" and cmd[1] == "video":
            if self._video_rc != 0:
                return self._video_rc, "", "bili-cli boom"
            stdout = (
                self._video_stdout
                if self._video_stdout is not None
                else json.dumps(self._video, ensure_ascii=False)
            )
            return 0, stdout, ""
        if cmd[0] == "bili" and cmd[1] == "user":
            return 0, json.dumps(self._user, ensure_ascii=False), ""
        return 0, "{}", ""


def _reader(**kwargs) -> tuple[BilibiliCliReader, FakeRunner]:
    runner = FakeRunner(**kwargs)
    return BilibiliCliReader(runner=runner), runner


def test_extract_video_id_accepts_documented_url_shapes() -> None:
    assert extract_video_id("https://www.bilibili.com/video/BV1xx411c7mD?spm=a1") == "BV1xx411c7mD"
    assert extract_video_id("https://m.bilibili.com/video/BV1abCDabcde/") == "BV1abCDabcde"
    assert extract_video_id("https://www.bilibili.com/video/av170001") == "av170001"
    assert extract_video_id("https://space.bilibili.com/42") is None
    assert extract_video_id(None) is None


def test_read_fills_author_and_pubdate_from_video_metadata() -> None:
    reader, runner = _reader()
    result = asyncio.run(
        reader.read("https://www.bilibili.com/video/BV1xx411c7mD", platform="bilibili")
    )
    assert result.status is SourceStatus.OK
    snapshot = result.items
    # PR7 核心补齐：author_id / published_at 让 author()/freshness 得以发生
    assert snapshot.content_id == "BV1xx411c7mD"
    assert snapshot.author_id == "42"
    assert snapshot.published_at == datetime.fromtimestamp(1756278000, tz=timezone.utc)
    assert snapshot.title.startswith("十分钟看懂")
    assert snapshot.text.endswith("摊主的一天。")
    # 只有视频元数据一次调用，没走 Jina
    assert len(runner.calls) == 1
    assert "--comments" not in " ".join(runner.calls[0])


def test_read_failure_yields_failed_with_degenerate_snapshot() -> None:
    reader, _runner = _reader(video_returncode=127)
    result = asyncio.run(reader.read("https://www.bilibili.com/video/BV1xx411c7mD"))
    assert result.status is SourceStatus.FAILED
    assert "boom" in (result.error or "")
    assert result.items.content_id == ""
    assert result.items.text == ""


def test_read_rejects_non_json_and_broken_envelopes() -> None:
    for kwargs, expected_error in (
        ({"video_stdout_override": "not json"}, "not JSON"),
        ({"video": {"ok": False, "error": {"code": -404, "message": "啥都木有"}}}, "啥都木有"),
        ({"video": {"ok": True}}, "no data object"),
    ):
        reader, _runner = _reader(**kwargs)
        result = asyncio.run(
            reader.read("https://www.bilibili.com/video/BV1xx411c7mD")
        )
        assert result.status is SourceStatus.FAILED, kwargs
        assert expected_error in (result.error or ""), kwargs


def test_non_video_url_degrades_to_generic_jina_read() -> None:
    reader, runner = _reader(jina_text="专栏正文全文……足够长的一段文字。")
    result = asyncio.run(
        reader.read("https://www.bilibili.com/read/cv123456", platform="bilibili")
    )
    # 能力降级但不伪造：正文可用，元数据缺失保持 None
    assert result.status is SourceStatus.OK
    assert result.items.text.startswith("专栏正文全文")
    assert result.items.author_id is None
    assert result.items.published_at is None
    assert runner.calls and runner.calls[0][0] == "curl"


def test_engagement_maps_stat_onto_contract_fields() -> None:
    reader, _runner = _reader()
    result = asyncio.run(reader.engagement("BV1xx411c7mD", platform="bilibili"))
    assert result.status is SourceStatus.OK
    snap = result.items
    assert (snap.views, snap.likes, snap.comments, snap.shares, snap.saves) == (
        100000,
        9000,
        700,
        500,
        1500,
    )


def test_engagement_missing_stat_is_structural_failure() -> None:
    broken = {"ok": True, "data": {"bvid": "BV1xx411c7mD"}}
    reader, _runner = _reader(video=broken)
    result = asyncio.run(reader.engagement("BV1xx411c7mD", platform="bilibili"))
    assert result.status is SourceStatus.FAILED
    assert "no stat object" in (result.error or "")


def test_partial_stat_fields_stay_none_not_zero() -> None:
    sparse = {
        "ok": True,
        "data": {"bvid": "BV1xx411c7mD", "stat": {"view": 555, "like": None}},
    }
    reader, _runner = _reader(video=sparse)
    result = asyncio.run(reader.engagement("BV1xx411c7mD", platform="bilibili"))
    assert result.status is SourceStatus.OK
    snap = result.items
    assert (snap.views, snap.likes, snap.comments) == (555, None, None)


def test_comments_and_author_delegate_to_shared_runner() -> None:
    reader, runner = _reader()
    comments = asyncio.run(
        reader.comments("BV1xx411c7mD", platform="bilibili")
    )
    author = asyncio.run(reader.author("42", platform="bilibili"))
    assert comments.status is SourceStatus.OK
    assert comments.items[0].text == "前排围观"
    assert comments.items[0].comment_id == "7"
    assert author.status is SourceStatus.OK
    assert author.items.followers == 321
    assert {tuple(c[:2]) for c in runner.calls} == {("bili", "video"), ("bili", "user")}
