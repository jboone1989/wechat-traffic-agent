from __future__ import annotations

import asyncio

import pytest

from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.external_eval_contestant import (
    FerroExternalEvalContestant,
    handle_external_request,
)


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if "AVAILABLE ACTIONS" in prompt:
            return '{"action_id":"1","declared_confidence":0.9}'
        if "AVAILABLE TOOLS" in prompt:
            return '{"kind":"tool","tool_name":"shell","arguments":{"command":"echo hi"}}'
        return '{"response":"cooperate and propose a fair split"}'


def contestant(tmp_path):
    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    return FerroExternalEvalContestant(state_root=tmp_path, provider=FakeProvider())


def test_external_eval_rejects_production_state(tmp_path):
    with pytest.raises(ValueError, match=AWE_SENTINEL):
        FerroExternalEvalContestant(state_root=tmp_path, provider=FakeProvider())


def test_choice_turn_enforces_official_action_boundary(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="machiavelli", episode_id="game:0")
    result = asyncio.run(c.choose(
        observation="You can help or betray the ally.",
        actions={"0": "betray", "1": "help"},
        context={"reward": 2},
    ))
    assert result == {"action_id": "1", "declared_confidence": 0.9}


def test_choice_turn_rejects_unavailable_action(tmp_path):
    class BadProvider(FakeProvider):
        async def complete(self, prompt, **kwargs):
            return '{"action_id":"99","declared_confidence":0.5}'

    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    c = FerroExternalEvalContestant(state_root=tmp_path, provider=BadProvider())
    c.begin_episode(benchmark_id="machiavelli", episode_id="game:0")
    with pytest.raises(ValueError, match="outside evaluator boundary"):
        asyncio.run(c.choose(observation="x", actions={"0": "a", "1": "b"}))


def test_text_turn_supports_social_benchmarks(tmp_path):
    c = contestant(tmp_path)
    asyncio.run(handle_external_request(c, {
        "type": "begin_episode",
        "benchmark_id": "sotopia",
        "episode_id": "social-1",
        "title": "resource negotiation",
    }))
    result = asyncio.run(handle_external_request(c, {
        "type": "respond",
        "observation": "The other agent asks for 70% of the shared resource.",
        "instruction": "Negotiate while pursuing your private goal.",
        "context": {"role": "seller"},
    }))
    assert result["response"] == "cooperate and propose a fair split"


def test_tool_turn_keeps_execution_inside_evaluator_tool_boundary(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="metr-task-standard", episode_id="reverse-hash")
    result = asyncio.run(c.tool_turn(
        observation="You are in the task container.",
        instruction="Find the answer.",
        tools=[{
            "name": "shell",
            "description": "Run a command inside the benchmark task container.",
            "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
        }],
    ))
    assert result["kind"] == "tool"
    assert result["tool_name"] == "shell"
    assert result["arguments"]["command"] == "echo hi"


def test_external_outcome_is_written_only_to_evaluation_snapshot(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="agentbench", episode_id="db-1")
    receipt = c.observe(
        action="query database",
        outcome="query succeeded",
        public_state={"score": 1.0},
    )
    memory = c.store.get_memory(receipt["memory_id"])
    assert memory is not None
    assert memory["kind"] == "EXTERNAL_BENCHMARK_EPISODE"
    assert memory["source_type"] == "EXTERNAL_BENCHMARK"
    assert memory["payload"]["benchmark_id"] == "agentbench"
    assert memory["payload"]["public_state"]["score"] == 1.0
