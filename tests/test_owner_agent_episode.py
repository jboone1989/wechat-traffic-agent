from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.livingruntime_chatgpt import LivingRuntimeChatGPTPendingError
from wechat_traffic_agent.owner_agent_episode import OwnerAgentEpisode


NOW = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)


class Runtime:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def cognitive_probe(self, prompt, max_tokens):
        self.prompts.append((prompt, max_tokens))
        if not self.responses:
            raise AssertionError("unexpected extra cognition call")
        return json.dumps(self.responses.pop(0))


def test_owner_episode_can_inspect_then_choose_content_as_one_tool(tmp_path):
    runtime = Runtime([
        {
            "action": "INSPECT_MISSION",
            "query": None,
            "topic": None,
            "wait_seconds": None,
            "rationale": "Inspect the live mission state before committing to an action.",
        },
        {
            "action": "RUN_CONTENT_PIPELINE",
            "query": None,
            "topic": "livingruntime-remote",
            "wait_seconds": None,
            "rationale": "Current mission evidence supports a bounded content experiment now.",
        },
    ])
    episode = OwnerAgentEpisode(tmp_path)
    calls = []

    async def execute(decision):
        calls.append(decision["action"])
        return {"status": "OK", "current": 1810, "trajectory": "BEHIND"}

    result = asyncio.run(episode.run(
        runtime=runtime,
        owner_goal={
            "goal_id": "goal-owner",
            "description": "Grow the real X audience for LivingRuntime Remote",
            "origin": "EXTERNAL_MISSION",
        },
        mission_context={
            "mission_id": "mission-x",
            "metric": "followers",
            "baseline": 1800,
            "current": 1810,
            "target": 5000,
        },
        tool_executor=execute,
        now=NOW,
    ))

    assert result["status"] == "DECIDED"
    assert result["decision"]["action"] == "RUN_CONTENT_PIPELINE"
    assert result["decision"]["topic"] == "livingruntime-remote"
    assert calls == ["INSPECT_MISSION"]
    assert len(runtime.prompts) == 2
    assert "not a content topic by default" in runtime.prompts[0][0]


def test_owner_episode_resume_reuses_durable_terminal_decision(tmp_path):
    first_runtime = Runtime([
        {
            "action": "WAIT",
            "query": None,
            "topic": None,
            "wait_seconds": 600,
            "rationale": "The next useful evidence is time dependent, so wait for fresh state.",
        }
    ])
    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("terminal WAIT must not execute a tool")

    first = asyncio.run(episode.run(
        runtime=first_runtime,
        owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
        mission_context={},
        tool_executor=execute,
        now=NOW,
    ))
    second_runtime = Runtime([])
    second = asyncio.run(OwnerAgentEpisode(tmp_path).run(
        runtime=second_runtime,
        owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
        mission_context={},
        tool_executor=execute,
        now=NOW,
    ))

    assert first["decision"]["action"] == "WAIT"
    assert second["status"] == "DECIDED"
    assert second["resumed"] is True
    assert second["decision"] == first["decision"]
    assert second_runtime.prompts == []


def test_pending_owner_cognition_leaves_running_episode_for_durable_resume(tmp_path):
    class PendingRuntime:
        async def cognitive_probe(self, prompt, max_tokens):
            raise LivingRuntimeChatGPTPendingError("llmreq-owner-episode")

    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("no tool runs before cognition completes")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=PendingRuntime(),
            owner_goal={
                "goal_id": "goal-owner",
                "description": "Grow the real X audience",
            },
            mission_context={"mission_id": "mission-x"},
            tool_executor=execute,
            now=NOW,
        ))

    assert episode.running_for("goal-owner") is True
    assert episode.running_for("other-goal") is False
