from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.livingruntime_chatgpt import LivingRuntimeChatGPTPendingError
from wechat_traffic_agent.owner_agent_episode import OwnerAgentEpisode
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


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


class RawRuntime:
    def __init__(self, raw: str):
        self.raw = raw
        self.calls = 0

    async def cognitive_probe(self, prompt, max_tokens):
        self.calls += 1
        return self.raw


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


def test_owner_episode_does_not_repair_invalid_model_json(tmp_path):
    runtime = RawRuntime("not-json")
    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("invalid cognition must not execute a tool")

    with pytest.raises(ValueError, match="backend did not return a JSON object"):
        asyncio.run(episode.run(
            runtime=runtime,
            owner_goal={"goal_id": "goal-owner", "description": "Act on the owner goal"},
            mission_context={},
            tool_executor=execute,
            now=NOW,
        ))

    assert runtime.calls == 1


def test_owner_episode_exhaustion_is_explicitly_unresolved_not_wait(tmp_path):
    runtime = Runtime([
        {
            "action": "INSPECT_MISSION",
            "query": None,
            "topic": None,
            "wait_seconds": None,
            "rationale": "Inspect once.",
        }
        for _ in range(OwnerAgentEpisode.MAX_STEPS)
    ])
    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        return {"status": "OK"}

    result = asyncio.run(episode.run(
        runtime=runtime,
        owner_goal={"goal_id": "goal-owner", "description": "Act on the owner goal"},
        mission_context={},
        tool_executor=execute,
        now=NOW,
    ))

    assert result["status"] == "UNRESOLVED_ACTION_REQUIRED"
    assert result["decision"] is None
    assert len(result["steps"]) == OwnerAgentEpisode.MAX_STEPS
    assert episode.projection_for("goal-owner")["status"] == "UNRESOLVED_ACTION_REQUIRED"


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

    assert episode.classify_request(
        "goal-owner", "llmreq-owner-resume"
    ) == "PENDING"

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
    assert "llmreq-owner-resume" in persisted["cognition_request_ids"]
    assert episode.classify_request(
        "goal-owner", "llmreq-owner-resume"
    ) == "STALE"


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
    assert episode.classify_request(
        "goal-owner", "llmreq-owner-timeout"
    ) == "STALE"


def test_pending_request_from_previous_owner_goal_is_stale(tmp_path):
    class PendingRuntime:
        async def cognitive_probe(self, prompt, max_tokens):
            raise LivingRuntimeChatGPTPendingError("llmreq-old-owner")

    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("no tool runs while cognition is pending")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=PendingRuntime(),
            owner_goal={"goal_id": "goal-old", "description": "Old owner task"},
            mission_context={"mission_id": "mission-old"},
            tool_executor=execute,
            now=NOW,
        ))

    assert episode.classify_request(
        "goal-new", "llmreq-old-owner"
    ) == "STALE"


def test_multiple_owner_goals_keep_independent_durable_episodes(tmp_path):
    class PendingRuntime:
        async def cognitive_probe(self, prompt, max_tokens):
            raise LivingRuntimeChatGPTPendingError("llmreq-goal-a")

    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("no tool runs before cognition completes")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=PendingRuntime(),
            owner_goal={"goal_id": "goal-a", "description": "Grow X followers"},
            mission_context={},
            tool_executor=execute,
            now=NOW,
        ))

    result_b = asyncio.run(episode.run(
        runtime=Runtime([{
            "action": "WAIT",
            "query": None,
            "topic": None,
            "wait_seconds": 300,
            "rationale": "Wait for the next bounded observation window.",
        }]),
        owner_goal={
            "goal_id": "goal-b",
            "description": "Promote LivingRuntime Remote",
        },
        mission_context={},
        tool_executor=execute,
        now=NOW,
    ))

    assert result_b["status"] == "DECIDED"
    assert episode.running_for("goal-a") is True
    assert episode.running_for("goal-b") is False
    assert episode.classify_request("goal-a", "llmreq-goal-a") == "PENDING"
    assert episode.classify_request("goal-b", "llmreq-goal-a") == "STALE"
    assert episode.projection_for("goal-a")["pending_request_id"] == "llmreq-goal-a"
    assert episode.projection_for("goal-b")["decision"]["action"] == "WAIT"


def test_owner_goal_scheduler_prevents_priority_starvation(tmp_path):
    old = NOW.replace(hour=0)
    recent = NOW.replace(hour=0, minute=20)
    goal_a = SimpleNamespace(
        goal_id="goal-a",
        description="Grow X followers",
        origin="EXTERNAL_MISSION",
        status="ACTIVE",
        priority=0.95,
        created_at=old,
    )
    goal_b = SimpleNamespace(
        goal_id="goal-b",
        description="Promote LivingRuntime Remote",
        origin="USER",
        status="ACTIVE",
        priority=0.70,
        created_at=old,
    )
    domain_runtime = SimpleNamespace(
        active_goals=[goal_a, goal_b],
        goals=[goal_a, goal_b],
        external_missions=[
            SimpleNamespace(
                goal_id="goal-a",
                status="ACTIVE",
                issued_by="operator",
            )
        ],
    )
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        app=SimpleNamespace(domain_runtime=domain_runtime),
    )
    runner.owner_agent_episode._write({
        "schema_version": OwnerAgentEpisode.SCHEMA,
        "goal_id": "goal-a",
        "status": "COMPLETED",
        "created_at": old.isoformat(),
        "updated_at": recent.isoformat(),
        "cognition_request_ids": [],
        "stale_request_ids": [],
        "pending_request_id": None,
    })

    selected = runner._active_owner_goal_context(
        now=NOW.replace(hour=0, minute=30),
    )
    assert selected["goal_id"] == "goal-b"
    assert selected["selection_authority"] == "FAIR_OWNER_GOAL_SCHEDULER"

    exact = runner._active_owner_goal_context(
        relevant_refs=("goal-a",),
        now=NOW.replace(hour=0, minute=30),
    )
    assert exact["goal_id"] == "goal-a"
    assert exact["selection_authority"] == "EXACT_OWNER_GOAL_REF"


def test_owner_goal_context_uses_specific_active_proposal_over_parent_goal(tmp_path):
    proposals = (
        tmp_path
        / "application"
        / "operator_chat"
        / "goal_proposals"
    )
    proposals.mkdir(parents=True)
    (proposals / "remote.json").write_text(
        json.dumps({
            "status": "ACTIVATED",
            "semantic_status": "CANONICAL",
            "kind": "EXTERNAL_MISSION",
            "goal_id": "goal-x-growth",
            "proposal_id": "goal-proposal:remote",
            "description": (
                "Promote LivingRuntime Remote as the next real X growth experiment"
            ),
            "reason": "Bind a concrete POSTED receipt to follower outcome.",
            "activated_at": "2026-09-28T09:00:00Z",
        }),
        encoding="utf-8",
    )
    goal = SimpleNamespace(
        goal_id="goal-x-growth",
        description="Grow X followers to 5000",
        origin="USER",
        status="ACTIVE",
        priority=0.9,
        created_at=NOW,
    )
    domain_runtime = SimpleNamespace(
        active_goals=[goal],
        goals=[goal],
        external_missions=[],
    )
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        app=SimpleNamespace(domain_runtime=domain_runtime),
    )

    context = runner._active_owner_goal_context(now=NOW)

    assert context["goal_id"] == "goal-x-growth"
    assert context["description"].startswith("Promote LivingRuntime Remote")
    assert context["parent_goal_description"] == "Grow X followers to 5000"
    assert context["owner_proposal_id"] == "goal-proposal:remote"
    assert context["owner_commitment_kind"] == "EXTERNAL_MISSION"


def test_specific_owner_commitment_supersedes_generic_pending_episode(tmp_path):
    class PendingRuntime:
        async def cognitive_probe(self, prompt, max_tokens):
            raise LivingRuntimeChatGPTPendingError("llmreq-generic-parent")

    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("no tool runs before cognition completes")

    with pytest.raises(LivingRuntimeChatGPTPendingError):
        asyncio.run(episode.run(
            runtime=PendingRuntime(),
            owner_goal={
                "goal_id": "goal-x-growth",
                "description": "Grow X followers to 5000",
            },
            mission_context={},
            tool_executor=execute,
            now=NOW,
        ))

    specific = asyncio.run(episode.run(
        runtime=Runtime([{
            "action": "RUN_CONTENT_PIPELINE",
            "query": None,
            "topic": "livingruntime-remote",
            "wait_seconds": None,
            "rationale": "Execute the specific bounded Remote promotion experiment now.",
        }]),
        owner_goal={
            "goal_id": "goal-x-growth",
            "owner_proposal_id": "goal-proposal:remote",
            "description": "Promote LivingRuntime Remote and obtain a POSTED receipt",
        },
        mission_context={},
        tool_executor=execute,
        now=NOW,
    ))

    assert specific["status"] == "DECIDED"
    assert specific["decision"]["topic"] == "livingruntime-remote"
    state = episode.projection_for("goal-x-growth")
    assert state["commitment_key"] == "goal-proposal:remote"
    assert "llmreq-generic-parent" in state["stale_request_ids"]
    assert state["pending_request_id"] is None
