from __future__ import annotations

import asyncio

import pytest

from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.external_eval_baseline import (
    ExternalEvalBaselineContestant,
    handle_external_baseline_request,
)


class FakeProvider:
    def __init__(self):
        self.prompts = []
        self.calls = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.calls.append((prompt, kwargs))
        if "AVAILABLE TOOLS" in prompt:
            return '{"kind":"tool","tool_name":"shell","arguments":{"command":"echo hi"}}'
        if "AVAILABLE ACTIONS" in prompt:
            return '{"action_id":"1","declared_confidence":0.8}'
        return '{"response":"ok"}'


def make_agent(tmp_path, mode):
    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    return ExternalEvalBaselineContestant(
        state_root=tmp_path,
        provider=FakeProvider(),
        mode=mode,
    )


def test_external_baseline_rejects_production_state(tmp_path):
    with pytest.raises(ValueError, match=AWE_SENTINEL):
        ExternalEvalBaselineContestant(
            state_root=tmp_path,
            provider=FakeProvider(),
            mode="raw",
        )


def test_raw_baseline_does_not_carry_episode_history(tmp_path):
    c = make_agent(tmp_path, "raw")
    c.begin_episode(benchmark_id="agentbench", episode_id="os-2")
    asyncio.run(c.tool_turn(
        observation="first",
        instruction="solve",
        tools=[{"name":"shell","description":"run","input_schema":{"type":"object"}}],
    ))
    asyncio.run(c.tool_turn(
        observation="second",
        instruction="solve",
        tools=[{"name":"shell","description":"run","input_schema":{"type":"object"}}],
    ))
    assert "recent_episode_turns" not in c.provider.prompts[-1]
    assert "first" not in c.provider.prompts[-1]


def test_durable_baseline_does_not_shorten_queue_lifetime(tmp_path):
    class DurableProvider(FakeProvider):
        durable_dispatch = True

    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    provider = DurableProvider()
    c = ExternalEvalBaselineContestant(
        state_root=tmp_path,
        provider=provider,
        mode="raw",
    )
    c.begin_episode(benchmark_id="agentbench", episode_id="os-2")
    asyncio.run(c.respond(observation="inspect", instruction="solve"))
    kwargs = provider.calls[-1][1]
    assert "_attempt_timeout_seconds" not in kwargs
    assert "_reserve_timeout_seconds" not in kwargs
    assert kwargs["_durable_blocking_wait"] is True
    assert kwargs["_durable_blocking_wait_seconds"] == 300


def test_basic_baseline_carries_bounded_episode_history(tmp_path):
    c = make_agent(tmp_path, "basic")
    c.begin_episode(benchmark_id="agentbench", episode_id="os-2")
    asyncio.run(c.tool_turn(
        observation="first-observation",
        instruction="solve",
        tools=[{"name":"shell","description":"run","input_schema":{"type":"object"}}],
    ))
    asyncio.run(c.tool_turn(
        observation="second-observation",
        instruction="solve",
        tools=[{"name":"shell","description":"run","input_schema":{"type":"object"}}],
    ))
    prompt = c.provider.prompts[-1]
    assert "recent_episode_turns" in prompt
    assert "first-observation" in prompt
    assert "echo hi" in prompt


def test_baseline_tool_boundary_is_enforced(tmp_path):
    class BadProvider(FakeProvider):
        async def complete(self, prompt, **kwargs):
            return '{"kind":"tool","tool_name":"root_shell","arguments":{"command":"id"}}'

    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    c = ExternalEvalBaselineContestant(
        state_root=tmp_path,
        provider=BadProvider(),
        mode="basic",
    )
    c.begin_episode(benchmark_id="agentbench", episode_id="os-2")
    with pytest.raises(ValueError, match="unavailable tool"):
        asyncio.run(c.tool_turn(
            observation="x",
            instruction="solve",
            tools=[{"name":"shell","description":"run","input_schema":{"type":"object"}}],
        ))


def test_baseline_handler_matches_external_protocol(tmp_path):
    c = make_agent(tmp_path, "basic")
    asyncio.run(handle_external_baseline_request(c, {
        "type":"begin_episode",
        "benchmark_id":"agentbench",
        "episode_id":"os-2",
    }))
    result = asyncio.run(handle_external_baseline_request(c, {
        "type":"choose",
        "observation":"choose",
        "actions":{"0":"stop","1":"continue"},
        "context":{},
    }))
    assert result == {"action_id":"1","declared_confidence":0.8}
