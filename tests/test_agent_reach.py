from __future__ import annotations

import asyncio
import json

import httpx

from wechat_traffic_agent.agent_reach import AgentReachProvider
from wechat_traffic_agent.sources import EngagementSnapshot, SourceStatus


def _runner(script: dict[tuple[str, ...], tuple[int, str, str]]):
    async def run(cmd: list[str]) -> tuple[int, str, str]:
        return script.get(tuple(cmd), (1, "", "command not mocked"))

    return run


def test_doctor_parses_json_output() -> None:
    doctor_output = json.dumps(
        {"channels": {"bilibili": "✅", "weibo": "❌"}, "active_backend": {"bilibili": "cli"}}
    )
    runner = _runner({("agent-reach", "doctor", "--json"): (0, doctor_output, "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.doctor())
    assert result.status is SourceStatus.OK
    assert result.source == "agent-reach"
    assert result.platform == "doctor"
    assert result.items["channels"]["bilibili"] == "✅"
    assert result.observed_at is not None


def test_doctor_nonzero_exit_returns_failed() -> None:
    runner = _runner({("agent-reach", "doctor", "--json"): (127, "", "agent-reach: not found")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.doctor())
    assert result.status is SourceStatus.FAILED
    assert "not found" in (result.error or "")


def test_read_uses_configured_template_and_returns_snapshot() -> None:
    url = "https://example.com/article"
    runner = _runner(
        {("curl", "-s", f"https://r.jina.ai/{url}"): (0, "# 标题\n\n正文段落", "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.read(url))
    assert result.status is SourceStatus.OK
    assert result.platform == "generic_web"
    assert result.items.content_id == url
    assert result.items.text.startswith("# 标题")


def test_read_failure_returns_failed() -> None:
    runner = _runner({})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.read("https://example.com/missing"))
    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert result.items.text == ""  # 标量类型 FAILED 携带退化实例（契约约定）


def test_comments_and_author_unconfigured_return_unavailable() -> None:
    provider = AgentReachProvider(runner=_runner({}))

    async def run():
        comments = await provider.comments("some-id", platform="weibo")
        author = await provider.author("some-author", platform="weibo")
        return comments, author

    comments, author = asyncio.run(run())
    for result in (comments, author):
        assert result.status is SourceStatus.UNAVAILABLE
        assert result.error is not None
    assert comments.items == ()  # 元组类型 UNAVAILABLE 为空元组（契约约定）
    assert author.items.author_id == ""  # 标量类型 UNAVAILABLE 携带退化实例（契约约定）


def test_doctor_non_json_output_returns_failed() -> None:
    runner = _runner({("agent-reach", "doctor", "--json"): (0, "not json", "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.doctor())
    assert result.status is SourceStatus.FAILED
    assert "not JSON" in (result.error or "")


def test_read_empty_content_returns_failed() -> None:
    url = "https://example.com/empty"
    runner = _runner({("curl", "-s", f"https://r.jina.ai/{url}"): (0, "   ", "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.read(url))
    assert result.status is SourceStatus.FAILED
    assert "empty content" in (result.error or "")


def test_commands_override_merges_with_defaults() -> None:
    url = "https://example.com/custom"
    doctor_output = json.dumps({"channels": {}})
    runner = _runner(
        {
            ("fake-reader", url): (0, "自定义读取器正文", ""),
            ("agent-reach", "doctor", "--json"): (0, doctor_output, ""),
        }
    )
    provider = AgentReachProvider(
        runner=runner, commands={"read_web": ["fake-reader", "{url}"]}
    )
    read_result = asyncio.run(provider.read(url))
    doctor_result = asyncio.run(provider.doctor())
    assert read_result.status is SourceStatus.OK
    assert read_result.items.text == "自定义读取器正文"
    assert doctor_result.status is SourceStatus.OK  # 未覆盖的 doctor 仍走默认模板


async def _boom_runner(cmd: list[str]) -> tuple[int, str, str]:
    raise AssertionError("subprocess runner must not be used when http_client is set")


def test_comments_bilibili_default_template_parses_envelope() -> None:
    payload = json.dumps(
        {
            "ok": True,
            "schema_version": "1",
            "data": {
                "comments": [
                    {"rpid": 101, "content": {"message": "太真实了"}, "like": 34},
                    {"rpid": 102, "text": "已收藏"},
                    {"rpid": 103},  # 无正文 → 跳过
                    "not-a-dict",  # 非法行 → 跳过
                ]
            },
        }
    )
    runner = _runner(
        {("bili", "video", "BV1", "--comments", "--json"): (0, payload, "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.comments("BV1", platform="bilibili"))
    assert result.status is SourceStatus.OK
    assert result.source == "agent-reach"
    first, second = result.items
    assert first.text == "太真实了"
    assert first.comment_id == "101"
    assert first.likes == 34
    assert first.content_id == "BV1"
    assert second.text == "已收藏"


def test_comments_bilibili_envelope_error_returns_failed() -> None:
    payload = json.dumps(
        {"ok": False, "schema_version": "1",
         "error": {"code": "not_found", "message": "未找到视频"}}
    )
    runner = _runner(
        {("bili", "video", "BV404", "--comments", "--json"): (0, payload, "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.comments("BV404", platform="bilibili"))
    assert result.status is SourceStatus.FAILED
    assert "未找到视频" in (result.error or "")
    assert result.items == ()


def test_comments_non_json_output_returns_failed() -> None:
    runner = _runner(
        {("bili", "video", "BV1", "--comments", "--json"): (0, "not json", "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.comments("BV1", platform="bilibili"))
    assert result.status is SourceStatus.FAILED
    assert "not JSON" in (result.error or "")


def test_comments_custom_template_is_configurable() -> None:
    runner = _runner({("xhs", "comments", "note-1"): (0, json.dumps([{"text": "评论甲"}]), "")})
    provider = AgentReachProvider(
        runner=runner, commands={"comments_xiaohongshu": ["xhs", "comments", "{content_id}"]}
    )
    result = asyncio.run(provider.comments("note-1", platform="xiaohongshu"))
    assert result.status is SourceStatus.OK
    assert result.items[0].text == "评论甲"


def test_author_bilibili_default_template_parses_user() -> None:
    payload = json.dumps(
        {"ok": True, "schema_version": "1",
         "data": {"user": {"mid": 946974, "follower": 123456}}}
    )
    runner = _runner({("bili", "user", "946974", "--json"): (0, payload, "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.author("946974", platform="bilibili"))
    assert result.status is SourceStatus.OK
    assert result.items.author_id == "946974"
    assert result.items.followers == 123456


def test_author_missing_followers_stays_none() -> None:
    payload = json.dumps({"ok": True, "schema_version": "1", "data": {"user": {"mid": 1}}})
    runner = _runner({("bili", "user", "1", "--json"): (0, payload, "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.author("1", platform="bilibili"))
    assert result.status is SourceStatus.OK  # 调用成功但数据缺失 ≠ 失败
    assert result.items.followers is None


def test_engagement_returns_unavailable_with_degenerate_snapshot() -> None:
    provider = AgentReachProvider(runner=_runner({}))
    result = asyncio.run(provider.engagement("any", platform="bilibili"))
    assert result.status is SourceStatus.UNAVAILABLE
    assert result.error is not None
    assert result.items == EngagementSnapshot(content_id="", platform="bilibili")


def test_read_uses_shared_http_client_for_all_reads() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=f"# 标题\n\n正文 {len(requested)}")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AgentReachProvider(runner=_boom_runner, http_client=client)
        first = await provider.read("https://example.com/a")
        second = await provider.read("https://example.com/b")
        await client.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first.status is SourceStatus.OK
    assert first.items.text.startswith("# 标题")
    assert second.status is SourceStatus.OK
    assert requested == [
        "https://r.jina.ai/https://example.com/a",
        "https://r.jina.ai/https://example.com/b",
    ]


def test_read_http_error_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AgentReachProvider(runner=_boom_runner, http_client=client)
        result = await provider.read("https://example.com/x")
        await client.aclose()
        return result

    result = asyncio.run(run())
    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert result.items.text == ""  # 退化实例纪律


def test_read_http_empty_content_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="   ")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AgentReachProvider(runner=_boom_runner, http_client=client)
        result = await provider.read("https://example.com/x")
        await client.aclose()
        return result

    result = asyncio.run(run())
    assert result.status is SourceStatus.FAILED
    assert "empty content" in (result.error or "")
