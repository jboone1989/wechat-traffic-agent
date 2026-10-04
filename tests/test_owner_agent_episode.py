from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.openai_public_chat import OpenAIPublicChatError
from wechat_traffic_agent.owner_agent_episode import OwnerAgentEpisode
from wechat_traffic_agent.goal_scheduler_evolution import GoalSchedulerEvolution
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


def test_owner_cognition_blocker_persists_until_retry_window(tmp_path):
    episode = OwnerAgentEpisode(tmp_path)
    episode._state_for(
        {"goal_id": "goal-owner", "description": "Promote LivingRuntime Remote"},
        NOW,
    )

    blocked = episode.mark_cognition_blocked(
        goal_id="goal-owner",
        error_type="OpenAIPublicChatError",
        message="HTTP_429",
        retry_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    assert blocked["status"] == "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
    assert episode.running_for("goal-owner") is True
    assert episode.cognition_blocker_for(
        "goal-owner",
        now=NOW + timedelta(minutes=4),
    )["message"] == "HTTP_429"
    assert episode.cognition_blocker_for(
        "goal-owner",
        now=NOW + timedelta(minutes=5),
    ) is None


def test_owner_429_is_bounded_and_duplicate_wake_does_not_retry_api(tmp_path):
    class FailingRuntime:
        def __init__(self):
            self.calls = 0

        async def cognitive_probe(self, prompt, max_tokens):
            self.calls += 1
            raise OpenAIPublicChatError("HTTP_429")

    class Wakeful:
        def __init__(self):
            self.goal_followups = []

        def schedule_goal_followup(self, now, *, seconds=5, relevant_refs=()):
            self.goal_followups.append((now, seconds, tuple(relevant_refs)))

    runtime = FailingRuntime()
    wakeful = Wakeful()
    domain_runtime = SimpleNamespace(
        active_goals=[],
        goals=[],
        external_missions=[],
    )
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        app=SimpleNamespace(domain_runtime=domain_runtime),
        wakeful=wakeful,
        owner_cognition_runtime=runtime,
    )
    owner_goal = {
        "goal_id": "goal-owner",
        "description": "Promote LivingRuntime Remote",
        "origin": "USER",
        "priority": 0.9,
    }

    first = runner._owner_agent_control(owner_goal=owner_goal, now=NOW)
    second = runner._owner_agent_control(
        owner_goal=owner_goal,
        now=NOW + timedelta(minutes=1),
    )

    assert first["status"] == "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
    assert first["retry_seconds"] == 300
    assert second["status"] == "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
    assert runtime.calls == 1
    assert wakeful.goal_followups == [
        (NOW, 300, ("goal-owner",)),
    ]


def test_owner_episode_completion_settles_original_scheduler_receipt(tmp_path):
    scheduler = GoalSchedulerEvolution(tmp_path)
    choice = scheduler.select(
        [
            {
                "goal_id": "goal-owner",
                "priority": 0.9,
                "age_seconds": 100.0,
                "created_at": NOW.isoformat(),
            },
            {
                "goal_id": "goal-other",
                "priority": 0.4,
                "age_seconds": 90.0,
                "created_at": NOW.isoformat(),
            },
        ],
        now=NOW,
    )
    episode = OwnerAgentEpisode(tmp_path)
    episode._state_for(
        {
            "goal_id": choice["goal_id"],
            "description": "Advance the selected owner goal",
            "goal_scheduler_decision_id": choice["decision_id"],
        },
        NOW,
    )

    completed = episode.complete(
        outcome={"status": "PUBLISHED", "world_utility": 0.6},
        now=NOW + timedelta(minutes=1),
        goal_id=choice["goal_id"],
    )

    assert completed["status"] == "COMPLETED"
    assert completed["goal_scheduler_outcome"]["status"] == "SETTLED"
    assert completed["goal_scheduler_outcome"]["outcome"]["valid"] is True
    assert GoalSchedulerEvolution(tmp_path).projection()["settled_valid_count"] == 1

    duplicate = episode.complete(
        outcome={"status": "PUBLISHED", "world_utility": 0.9},
        now=NOW + timedelta(minutes=2),
        goal_id=choice["goal_id"],
    )
    assert duplicate["duplicate"] is True
    assert GoalSchedulerEvolution(tmp_path).projection()["settled_valid_count"] == 1


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


def test_long_rationale_keeps_valid_owner_action_and_complete_explanation(tmp_path):
    rationale = "Reality checks support the bounded publication attempt. " * 30
    runtime = Runtime([{"action":"RUN_CONTENT_PIPELINE", "topic":"livingruntime-remote", "rationale":rationale}])
    async def execute(_decision):
        raise AssertionError("terminal delegation must not execute an inspection")
    episode = OwnerAgentEpisode(tmp_path)
    result = asyncio.run(episode.run(runtime=runtime, owner_goal={"goal_id":"goal-owner","description":"Publish the authorized Remote experiment"}, mission_context={}, tool_executor=execute, now=NOW))
    assert result["status"] == "DECIDED"
    assert result["decision"]["action"] == "RUN_CONTENT_PIPELINE"
    assert result["decision"]["rationale"] == rationale
    assert episode.projection_for("goal-owner")["decision"]["rationale"] == rationale
    assert len(runtime.prompts) == 1


def test_owner_prompt_uses_current_mind_and_retrieved_history_not_archives(tmp_path, monkeypatch):
    from wechat_traffic_agent import memory_service

    research = tmp_path / "content" / "learning"
    research.mkdir(parents=True)
    (research / "research.json").write_text(
        json.dumps({"history": [{"fact": "HISTORICAL_ARCHIVE_MUST_NOT_ENTER_PROMPT"}]}),
        encoding="utf-8",
    )
    checkpoint = tmp_path / "application" / "content_mind"
    checkpoint.mkdir(parents=True)
    (checkpoint / "checkpoint.json").write_text(
        json.dumps({"subject": {"interests": {"items": {"CHECKPOINT_ARCHIVE_MUST_NOT_ENTER_PROMPT": {}}}}}),
        encoding="utf-8",
    )

    queries = []

    class FakeMemoryService:
        def __init__(self, _state_root):
            pass

        def search(self, query, **_kwargs):
            queries.append(query)
            return [{
                "node_id": "memory:btc-drawdown",
                "type": "memory",
                "text": "RETRIEVED_RELEVANT_HISTORY",
                "context": "btc drawdown regime",
                "version": "v1",
                "status": "ACTIVE",
                "topics": ["btc-drawdown"],
                "change_seq": 1,
            }]

    monkeypatch.setattr(memory_service, "MemoryService", FakeMemoryService)
    runtime = Runtime([
        {
            "action": "INSPECT_MISSION",
            "rationale": "Inspect the live mission once before deciding the next action.",
        },
        {
            "action": "WAIT",
            "wait_seconds": 300,
            "rationale": "Wait for the next measured observation before changing the active experiment.",
        },
    ])
    episode = OwnerAgentEpisode(tmp_path)

    async def execute(decision):
        assert decision["action"] == "INSPECT_MISSION"
        return {"status": "OK", "mission": "current"}

    result = asyncio.run(episode.run(
        runtime=runtime,
        owner_goal={"goal_id": "goal-owner", "description": "Study BTC drawdown regimes"},
        mission_context={
            "current_mind": {
                "interests": [{"name": "bitcoin-regimes", "salience": 0.9, "preference": 0.8}],
                "open_inquiries": [{"question": "Does drawdown clustering distinguish market regimes?"}],
            }
        },
        tool_executor=execute,
        now=NOW,
    ))

    prompt = runtime.prompts[-1][0]
    assert result["status"] == "DECIDED"
    assert len(queries) == 1
    assert "Study BTC drawdown regimes" in queries[0]
    assert "bitcoin-regimes" in prompt
    assert "RETRIEVED_RELEVANT_HISTORY" in prompt
    assert "HISTORICAL_ARCHIVE_MUST_NOT_ENTER_PROMPT" not in prompt
    assert "CHECKPOINT_ARCHIVE_MUST_NOT_ENTER_PROMPT" not in prompt
    assert '"learning_research"' not in prompt
    assert '"durable_interests"' not in prompt


def test_owner_decision_prompt_projects_context_instead_of_dumping_full_state(
    tmp_path,
    monkeypatch,
):
    from wechat_traffic_agent import memory_service

    class FakeMemoryService:
        def __init__(self, _state_root):
            pass

        def search(self, _query, **_kwargs):
            return [
                {
                    "node_id": f"memory:{index}",
                    "type": "memory",
                    "status": "ACTIVE",
                    "topics": [f"topic-{index}", "shared", "extra", "overflow"],
                    "text": ("useful memory " + str(index) + " ") * 80,
                    "context": "CONTEXT_MUST_NOT_ENTER_DECISION_PROMPT",
                    "version": "VERSION_MUST_NOT_ENTER_DECISION_PROMPT",
                    "change_seq": index,
                }
                for index in range(8)
            ]

    monkeypatch.setattr(memory_service, "MemoryService", FakeMemoryService)
    runtime = Runtime([
        {
            "action": "WAIT",
            "wait_seconds": 300,
            "rationale": "No further action is needed until the next measured observation.",
        }
    ])
    episode = OwnerAgentEpisode(tmp_path)

    async def execute(_decision):
        raise AssertionError("WAIT must not execute a tool")

    mind = {
        "mind_tick": 12,
        "interests": [
            {"name": f"interest-{index}", "salience": 0.9, "preference": 0.8}
            for index in range(20)
        ],
        "open_inquiries": [
            {"inquiry_id": f"q-{index}", "question": "question " + ("x" * 500)}
            for index in range(20)
        ],
        "beliefs": [
            {"proposition": "belief " + ("y" * 500), "stance": 0.5, "confidence": 0.9}
            for _ in range(20)
        ],
        "recent_belief_changes": [{"raw": "DO_NOT_DUMP_BELIEF_HISTORY"}] * 20,
        "latest_plan": {"raw": "DO_NOT_DUMP_LATEST_PLAN"},
        "memory_system": {"raw": "DO_NOT_DUMP_MEMORY_SYSTEM"},
        "learning_boundary": "DO_NOT_DUMP_LEARNING_BOUNDARY",
        "strategy_learning": {"outcome_count": 50, "topics": [f"s-{i}" for i in range(30)]},
    }
    result = asyncio.run(episode.run(
        runtime=runtime,
        owner_goal={"goal_id": "goal-owner", "description": "Advance the real mission"},
        mission_context={
            "mission_id": "mission",
            "description": "Mission description",
            "roadmap": {
                "steps": [
                    {"step_id": f"step-{i}", "title": f"Step {i}", "objective": "z" * 1000, "status": "PLANNED"}
                    for i in range(20)
                ]
            },
            "current_mind": mind,
        },
        tool_executor=execute,
        now=NOW,
    ))

    prompt = runtime.prompts[0][0]
    assert result["status"] == "DECIDED"
    assert "interest-5" in prompt
    assert "interest-6" not in prompt
    assert "memory:2" in prompt
    assert "memory:3" not in prompt
    assert "CONTEXT_MUST_NOT_ENTER_DECISION_PROMPT" not in prompt
    assert "DO_NOT_DUMP_BELIEF_HISTORY" not in prompt
    assert "DO_NOT_DUMP_LATEST_PLAN" not in prompt
    assert "DO_NOT_DUMP_MEMORY_SYSTEM" not in prompt
    assert "DO_NOT_DUMP_LEARNING_BOUNDARY" not in prompt
    assert '"objective"' not in prompt
    assert len(prompt) < 20000
