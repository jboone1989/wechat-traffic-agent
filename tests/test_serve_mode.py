"""--serve mode connects the shared mailbox entry (WeCom operator bridge)."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import FixtureAgentBackend
from agent_runtime.runtime import AgentRuntime

import wechat_traffic_agent.__main__ as main_module


def _serve_runtime(tmp_path, response: str = "ok") -> AgentRuntime:
    manifest = load_agent_manifest(Path(__file__).parents[1] / "agent.yaml")
    backend = FixtureAgentBackend(response)
    return AgentRuntime.bootstrap(
        tmp_path / "state", agent_manifest=manifest, agent_backend=backend
    )


def test_serve_flag_makes_audience_and_topics_optional() -> None:
    args = main_module.build_parser().parse_args(["--serve"])
    assert args.serve is True
    assert args.audience is None
    assert args.topics is None


def test_batch_mode_without_inputs_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit):
        main_module.main([])


def test_run_serve_starts_continuous_runner_without_tick_limit(
    tmp_path, monkeypatch
) -> None:
    captured: dict = {}
    runtime = _serve_runtime(tmp_path)

    class FakeRunner:
        def __init__(self, rt, **kwargs):
            self.runtime = rt
            captured.update(kwargs)

        def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status="COMPLETED", stop_reason="fake")

    monkeypatch.setattr(main_module, "ContinuousRunner", FakeRunner)
    result = main_module._run_serve(runtime, argparse.Namespace(idle_seconds=0.0))
    assert result == 0
    assert captured["max_ticks"] is None
    assert captured["idle_seconds"] == 0.0


def test_run_serve_passes_message_router(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    runtime = _serve_runtime(tmp_path)

    class FakeRunner:
        def __init__(self, rt, **kwargs):
            self.runtime = rt
            captured.update(kwargs)

        def run(self, **kwargs):
            return SimpleNamespace(status="COMPLETED", stop_reason="fake")

    monkeypatch.setattr(main_module, "ContinuousRunner", FakeRunner)
    marker = object()
    main_module._run_serve(
        runtime,
        argparse.Namespace(idle_seconds=0.0),
        message_router=marker,
    )
    assert captured["message_router"] is marker


def test_is_content_request_detects_content_commands() -> None:
    from wechat_traffic_agent.wecom_router import is_content_request

    assert is_content_request("写个银发经济领域的")
    assert is_content_request("生成一篇养老院题图")
    assert is_content_request("做个题图")
    assert not is_content_request("你是谁")
    assert not is_content_request("你好")


def test_wecom_router_packages_content_request_and_replies_with_paths(
    tmp_path,
) -> None:
    from wechat_traffic_agent import EmotionAngle, TietuDraft
    from wechat_traffic_agent.wecom_router import build_wecom_router

    draft_json = TietuDraft(
        title="别让副业偷走你的下班",
        lines=(
            "你盯着阅读量，不是在复盘，",
            "是在等一个数字证明今晚没白熬。",
            "但流量不奖励疲惫，只奖励被看见的需要。",
        ),
        interaction_hook="你做内容最累的，是没流量，还是不知道为谁写？",
        angle=EmotionAngle.GENTLE_REBELLION,
        topic="写个银发经济领域的",
        pain_point="深夜用更新数量对抗流量焦虑",
        viewpoint="增加发布数量之前，先弄清楚内容在替谁说话。",
        source_urls=(),
        rationale="具体命中副业创作者深夜看数据的场景。",
    ).model_dump_json()
    runtime = _serve_runtime(tmp_path, response=draft_json)
    output = tmp_path / "output"
    router = build_wecom_router(output)
    reply = asyncio.run(
        router(
            runtime,
            SimpleNamespace(
                message="写个银发经济领域的", partner_id="wecom_ZhangSan"
            ),
        )
    )
    assert reply is not None
    assert "已生成题图初稿" in reply.text
    assert "标题" in reply.text
    assert "别让副业偷走你的下班" in reply.text
    assert "你盯着阅读量，不是在复盘，" in reply.text
    assert str(output) in reply.text
    assert len(reply.attachments) == 1
    assert Path(reply.attachments[0]).name == "poster.png"
    assert Path(reply.attachments[0]).exists()
    assert Path(reply.attachments[0]).stat().st_size > 1000
    assert any(output.rglob("tietu.md"))
    assert any(output.rglob("preview.html"))


def test_wecom_router_falls_back_for_plain_chat(tmp_path) -> None:
    from wechat_traffic_agent.wecom_router import build_wecom_router

    runtime = _serve_runtime(tmp_path)
    reply = asyncio.run(
        build_wecom_router(tmp_path / "output")(
            runtime,
            SimpleNamespace(message="你是谁", partner_id="wecom_ZhangSan"),
        )
    )
    assert reply is None
