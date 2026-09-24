from __future__ import annotations

import asyncio

from wechat_traffic_agent.awe_baseline_contestant import ProviderAWEBaseline


class FakeProvider:
    def __init__(self):
        self.prompts = []

    async def structured(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        return {
            "action_id": "a2",
            "declared_beliefs": {"reason": "test"},
            "declared_confidence": 0.75,
        }


def test_raw_baseline_does_not_carry_outcomes():
    provider = FakeProvider()
    agent = ProviderAWEBaseline(provider=provider, mode="raw")
    agent.begin_scenario({"id": "s1", "title": "one", "level": "E1"})
    asyncio.run(agent.decide(observation="obs", actions={"a1": "x", "a2": "y"}, public_world={}))
    agent.observe_outcome(action_id="a2", outcome="won", public_world={"score": 1})
    agent.begin_scenario({"id": "s2", "title": "two", "level": "E2"})
    asyncio.run(agent.decide(observation="next", actions={"a1": "x", "a2": "y"}, public_world={}))
    assert "recent_outcomes" not in provider.prompts[-1]


def test_basic_baseline_carries_bounded_outcome_history():
    provider = FakeProvider()
    agent = ProviderAWEBaseline(provider=provider, mode="basic")
    agent.begin_scenario({"id": "s1", "title": "one", "level": "E1"})
    agent.observe_outcome(action_id="a2", outcome="won", public_world={"score": 1})
    agent.begin_scenario({"id": "s2", "title": "two", "level": "E2"})
    result = asyncio.run(agent.decide(observation="next", actions={"a1": "x", "a2": "y"}, public_world={}))
    assert result["action_id"] == "a2"
    assert "recent_outcomes" in provider.prompts[-1]
    assert "won" in provider.prompts[-1]
