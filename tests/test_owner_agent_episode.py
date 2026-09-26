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
    persisted = episode._read()
    assert persisted["pending_request_id"] == "llmreq-owner-episode"
    assert persisted["pending_step_index"] == 0


def test_pending_owner_step_is_single_flight_across_duplicate_wakes(tmp_path):
    class PendingRuntime:
        def __init__(self):
            self.calls = 0

        async def cognitive_probe(self, prompt, max_tokens):
            self.calls += 1
            raise LivingRuntimeChatGPTPendingError("llmreq-single-flight")

    episode = OwnerAgentEpisode(tmp_path)
    first_runtime = PendingRuntime()

    async def execute(_decision):
        raise AssertionError("no tool runs while cognition is pending")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=first_runtime,
            owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
            mission_context={"mission_id": "mission-x"},
            tool_executor=execute,
            now=NOW,
        ))
    assert first_runtime.calls == 1

    duplicate_runtime = Runtime([])
    with pytest.raises(LivingRuntimeChatGPTPendingError) as caught:
        asyncio.run(episode.run(
            runtime=duplicate_runtime,
            owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
            mission_context={"mission_id": "mission-x"},
            tool_executor=execute,
            now=NOW,
        ))

    assert caught.value.request_id == "llmreq-single-flight"
    assert duplicate_runtime.prompts == []


def test_exact_owner_resume_clears_pending_single_flight(tmp_path):
    class PendingRuntime:
        async def cognitive_probe(self, prompt, max_tokens):
            raise LivingRuntimeChatGPTPendingError("llmreq-owner-resume")

    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("WAIT is terminal and must not execute a tool")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=PendingRuntime(),
            owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
            mission_context={"mission_id": "mission-x"},
            tool_executor=execute,
            now=NOW,
        ))

    resumed = asyncio.run(episode.run(
        runtime=Runtime([{
            "action": "WAIT",
            "query": None,
            "topic": None,
            "wait_seconds": 300,
            "rationale": "Fresh evidence is not available yet, so retry after the bounded wait.",
        }]),
        owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
        mission_context={"mission_id": "mission-x"},
        tool_executor=execute,
        now=NOW,
        resume_request_id="llmreq-owner-resume",
    ))

    assert resumed["status"] == "DECIDED"
    persisted = episode._read()
    assert persisted["pending_request_id"] is None
    assert persisted["pending_step_index"] is None


def test_clear_pending_request_only_releases_matching_request(tmp_path):
    class PendingRuntime:
        async def cognitive_probe(self, prompt, max_tokens):
            raise LivingRuntimeChatGPTPendingError("llmreq-owner-timeout")

    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("no tool runs while cognition is pending")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=PendingRuntime(),
            owner_goal={"goal_id": "goal-owner", "description": "Grow X audience"},
            mission_context={"mission_id": "mission-x"},
            tool_executor=execute,
            now=NOW,
        ))

    assert episode.clear_pending_request("llmreq-other") is False
    assert episode._read()["pending_request_id"] == "llmreq-owner-timeout"
    assert episode.clear_pending_request(
        "llmreq-owner-timeout",
        error_type="LivingRuntimeChatGPTProviderError",
        now=NOW,
    ) is True
    persisted = episode._read()
    assert persisted["pending_request_id"] is None
    assert persisted["last_cognition_error"]["request_id"] == "llmreq-owner-timeout"
