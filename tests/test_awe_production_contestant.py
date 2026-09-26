from __future__ import annotations

import asyncio

import pytest

from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.awe_production_contestant import ProductionAWEContestant
from wechat_traffic_agent.live_agent import _OWNER_SYSTEM


class _Context:
    def private_context(self):
        return {
            "active_goals": [{
                "goal_id": "goal-production",
                "description": "Persist and complete owner goals",
            }],
            "external_missions": [{"mission_id": "mission-x", "status": "ACTIVE"}],
            "neural_life": {"intention": {"name": "explore", "strength": 0.7}},
        }

    def memories(self, query):
        return [{"memory_id": "mem-1", "text": "Use durable evidence."}]

    def history(self):
        return [{"role": "operator", "text": "Earlier owner context", "created_at": "t0"}]

    def archive(self, query):
        return []


class _Provider:
    durable_dispatch = True

    def __init__(self, *, tool_request=False):
        self.calls = []
        self.tool_request = tool_request

    async def structured(self, prompt, schema, **kwargs):
        self.calls.append((prompt, schema, kwargs))
        return {
            "reply": '{"action_id":"b","declared_beliefs":{"signal":"fresh"},"declared_confidence":0.8}',
            "tool_requests": (
                [{"capability_id": "web.search", "payload": {"query": "x"}, "reason": "x"}]
                if self.tool_request else []
            ),
            "goal_candidate": None,
            "memory_candidates": [],
        }


def _contestant(tmp_path, provider=None):
    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    return ProductionAWEContestant(
        state_root=tmp_path,
        provider=provider or _Provider(),
        context=_Context(),
    )


def test_production_parity_uses_owner_system_and_full_production_context(tmp_path):
    provider = _Provider()
    contestant = _contestant(tmp_path, provider)
    contestant.begin_scenario({"id": "s1", "title": "test", "level": "negotiation"})

    result = asyncio.run(contestant.decide(
        observation="A partner made an offer.",
        actions={"a": "accept", "b": "counter"},
        public_world={"round": 2},
    ))

    assert result == {
        "action_id": "b",
        "declared_beliefs": {"signal": "fresh"},
        "declared_confidence": 0.8,
    }
    prompt, _schema, kwargs = provider.calls[-1]
    assert "goal-production" in prompt
    assert "mission-x" in prompt
    assert "neural_life" in prompt
    assert "mem-1" in prompt
    assert "Earlier owner context" in prompt
    assert "AVAILABLE ACTIONS" in prompt
    assert kwargs["system"] == _OWNER_SYSTEM
    assert "_attempt_timeout_seconds" not in kwargs
    assert "_reserve_timeout_seconds" not in kwargs


def test_production_parity_keeps_evaluation_outcomes_ephemeral(tmp_path):
    contestant = _contestant(tmp_path)
    contestant.begin_scenario({"id": "s1"})
    contestant.observe_outcome(
        action_id="a",
        outcome="partner rejected",
        public_world={"round": 2},
    )

    assert len(contestant.synthetic_history) == 1
    assert "partner rejected" in contestant.synthetic_history[0]["text"]


def test_production_parity_rejects_runtime_tools_outside_awe_boundary(tmp_path):
    contestant = _contestant(tmp_path, _Provider(tool_request=True))
    contestant.begin_scenario({"id": "s1"})

    with pytest.raises(ValueError, match="outside closed AWE action boundary"):
        asyncio.run(contestant.decide(
            observation="choose",
            actions={"a": "wait", "b": "act"},
            public_world={},
        ))


def test_production_parity_requires_isolated_snapshot(tmp_path):
    with pytest.raises(ValueError, match=AWE_SENTINEL):
        ProductionAWEContestant(
            state_root=tmp_path,
            provider=_Provider(),
            context=_Context(),
        )
