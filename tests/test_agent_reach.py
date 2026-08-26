from __future__ import annotations

import asyncio
import json

from wechat_traffic_agent.agent_reach import AgentReachProvider
from wechat_traffic_agent.sources import SourceStatus


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
        comments = await provider.comments("some-id", platform="bilibili")
        author = await provider.author("some-author", platform="bilibili")
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
