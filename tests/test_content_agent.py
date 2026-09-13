from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import AgentRunMode, ExecutionPolicy, FixtureAgentBackend
from agent_runtime.runtime import AgentRuntime
from wechat_traffic_agent.content_artifact import ContentKind
from wechat_traffic_agent.content_service import ContentAgent
from wechat_traffic_agent.models import AudienceProfile, TopicSignal


def _runtime(tmp_path, response: str):
    manifest = load_agent_manifest(Path(__file__).parents[1] / "agent.yaml")
    backend = FixtureAgentBackend(response)
    runtime = AgentRuntime.bootstrap(
        tmp_path / "state",
        agent_manifest=manifest,
        agent_backend=backend,
    )
    return runtime, backend


def _topic() -> TopicSignal:
    return TopicSignal(
        title="为什么热点需要二次验证",
        summary="单个平台的短期热度不能直接代表长期需求。",
        source="research fixture",
        source_url="https://example.com/research-source",
        heat=0.7,
    )


def _audience() -> AudienceProfile:
    return AudienceProfile(
        people="希望理解内容研究方法的创作者",
        scene="筛选下一篇内容选题时",
        recurring_need="区分短期热度和持续需求",
    )


def _response(**changes) -> str:
    data = {
        "title": "热点很高，为什么还不能马上写？",
        "summary": "短期热度只是选题证据的一部分，仍需要验证持续需求。",
        "sections": [
            {"heading": "热点回答了什么", "paragraphs": ["热点说明某个话题当前获得了较多注意。"]},
            {"heading": "它没有回答什么", "paragraphs": ["长期需求和目标人群是否持续关心仍需要独立证据。"]},
        ],
        "topic": _topic().title,
        "source_urls": [_topic().source_url],
        "disclosure": None,
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


def test_content_agent_composes_channel_neutral_artifact(tmp_path):
    runtime, backend = _runtime(tmp_path, _response())
    artifact = asyncio.run(
        ContentAgent(runtime).compose(
            [_topic()], _audience(), artifact_id="research-1",
            slug="why-trends-need-validation", kind=ContentKind.RESEARCH,
        )
    )
    assert artifact.kind is ContentKind.RESEARCH
    assert artifact.public_safe is False
    assert artifact.source_refs[0].locator == _topic().source_url
    assert artifact.source_refs[0].ref_id.startswith("topic:")
    request = backend.requests[-1]
    assert request.mode is AgentRunMode.ADVISORY
    assert request.policy == ExecutionPolicy.advisory()
    assert "general Content Agent" in request.task
    assert "Do not write for a specific publishing platform" in request.task


def test_content_agent_rejects_invented_source(tmp_path):
    runtime, _ = _runtime(tmp_path, _response(source_urls=["https://invented.example"]))
    with pytest.raises(ValueError, match="unsupported source URL"):
        asyncio.run(
            ContentAgent(runtime).compose(
                [_topic()], _audience(), artifact_id="research-1",
                slug="why-trends-need-validation",
            )
        )
