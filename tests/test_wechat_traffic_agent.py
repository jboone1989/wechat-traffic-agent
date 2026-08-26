from __future__ import annotations

import asyncio
import json
import tomllib
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import AgentRunMode, ExecutionPolicy, FixtureAgentBackend
from agent_runtime.runtime import AgentRuntime
from wechat_traffic_agent import (
    AudienceProfile,
    EmotionAngle,
    PerformanceObservation,
    TietuDraft,
    TopicSignal,
    TrafficLearner,
    WeChatTrafficAgent,
)
from wechat_traffic_agent.packaging import ContentPackager


def _audience() -> AudienceProfile:
    return AudienceProfile(
        people="独自在大城市工作的职场人",
        scene="工作日深夜回家的十分钟",
        recurring_need="被理解并获得一个不消耗自己的提醒",
    )


def _topic() -> TopicSignal:
    return TopicSignal(
        title="做内容的人正在被流量绑架",
        summary="堆量不能代替人群、场景和痛点定位。",
        source="WolfMoneyNotes 题图方法帖",
        source_url="https://x.com/WolfMoneyNotes/status/2077624635105980561",
        heat=0.7,
        creator_followers=500,
        likes=80,
        comments=20,
        saves=30,
    )


def _draft(**changes) -> TietuDraft:
    values = {
        "title": "别让副业偷走你的下班",
        "lines": (
            "你盯着阅读量，不是在复盘，",
            "是在等一个数字证明今晚没白熬。",
            "但流量不奖励疲惫，只奖励被看见的需要。",
            "今晚别再多发一条，先写清楚你在替谁说话。",
        ),
        "interaction_hook": "你做内容最累的，是没流量，还是不知道为谁写？",
        "angle": EmotionAngle.GENTLE_REBELLION,
        "topic": "做内容的人正在被流量绑架",
        "pain_point": "深夜用更新数量对抗流量焦虑",
        "viewpoint": "增加发布数量之前，先弄清楚内容在替哪个具体的人说话。",
        "source_urls": (
            "https://x.com/WolfMoneyNotes/status/2077624635105980561",
        ),
        "rationale": "具体命中副业创作者深夜看数据的场景，并给出停止堆量的动作。",
    }
    values.update(changes)
    return TietuDraft(**values)


def _runtime(tmp_path, response: str) -> tuple[AgentRuntime, FixtureAgentBackend]:
    manifest = load_agent_manifest(Path(__file__).parents[1] / "agent.yaml")
    backend = FixtureAgentBackend(response)
    runtime = AgentRuntime.bootstrap(
        tmp_path / "state",
        agent_manifest=manifest,
        agent_backend=backend,
    )
    return runtime, backend


def test_manifest_is_content_only() -> None:
    manifest = load_agent_manifest(Path(__file__).parents[1] / "agent.yaml")
    assert manifest.agent_type == "wechat-traffic-agent"
    assert manifest.actuators == ("local_content_package",)
    assert "wechat_publish" not in manifest.capabilities


def test_topic_breakout_score_rewards_engagement_from_small_creators() -> None:
    topic = _topic()
    assert topic.low_follower_breakout_score == pytest.approx(0.36)
    assert TopicSignal(title="普通样本", summary="摘要", source="人工").low_follower_breakout_score is None


def test_learner_explores_then_uses_observed_reward() -> None:
    learner = TrafficLearner()
    first = learner.recommend([])
    history = []
    for angle in EmotionAngle:
        for sample in range(2):
            history.append(
                PerformanceObservation(
                    post_id=f"{angle.value}-{sample}",
                    angle=angle,
                    impressions=100,
                    reads=90 if angle is EmotionAngle.RESONANCE else 10,
                )
            )
    assert first in EmotionAngle
    assert learner.recommend(history) is EmotionAngle.RESONANCE


def test_runtime_composes_grounded_content_without_execution_authority(tmp_path) -> None:
    selected_angle = TrafficLearner().recommend([])
    response = _draft(angle=selected_angle).model_dump_json()
    runtime, backend = _runtime(tmp_path, response)

    draft, package = asyncio.run(
        WeChatTrafficAgent(runtime).create_content_package(
            [_topic()], [], _audience(), tmp_path / "output"
        )
    )

    assert draft.topic == _topic().title
    assert draft.source_urls == (_topic().source_url,)
    assert package.markdown.exists()
    request = backend.requests[-1]
    assert request.mode is AgentRunMode.ADVISORY
    assert request.policy == ExecutionPolicy.advisory()
    assert "只写文案" in request.task
    assert "低粉高互动" in request.task


def test_runtime_rejects_topic_or_source_invented_by_backend(tmp_path) -> None:
    selected_angle = TrafficLearner().recommend([])
    response = _draft(
        angle=selected_angle,
        topic="模型虚构的话题",
        source_urls=("https://invented.example",),
    ).model_dump_json()
    runtime, _ = _runtime(tmp_path, response)
    with pytest.raises(ValueError, match="outside supplied evidence"):
        asyncio.run(WeChatTrafficAgent(runtime).compose([_topic()], [], _audience()))


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "这是一条明显超过二十个汉字限制而且还在继续变长的标题"},
        {"interaction_hook": "你一定要马上转发给所有人"},
        {"lines": ("只有一句", "只有两句")},
    ],
)
def test_draft_rejects_platform_or_growth_hack_violations(changes) -> None:
    with pytest.raises(ValidationError):
        _draft(**changes)


def test_packager_creates_copy_ready_files(tmp_path) -> None:
    package = ContentPackager().create(
        _draft(), _audience(), tmp_path, today=date(2026, 8, 26)
    )
    assert package.root == tmp_path / "2026-08-26"
    assert {item.name for item in package.root.iterdir()} == {
        "tietu.md",
        "tietu.json",
        "preview.html",
    }
    assert "别让副业偷走你的下班" in package.markdown.read_text(encoding="utf-8")
    assert json.loads(package.json_file.read_text(encoding="utf-8"))["topic"] == _topic().title
    assert "你做内容最累的" in package.preview_html.read_text(encoding="utf-8")


def test_agent_version_matches_package_version() -> None:
    root = Path(__file__).parents[1]
    manifest = load_agent_manifest(root / "agent.yaml")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest.version == pyproject["project"]["version"] == "0.3.0"
