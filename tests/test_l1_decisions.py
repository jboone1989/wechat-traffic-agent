from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.fast_decision import (
    FastDecisionError,
    FastDecisionResult,
)
from wechat_traffic_agent.l1_decisions import FerroL1Decisions


NOW = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


class _Provider:
    provider_name = "TEST"

    def __init__(self, choices):
        self.choices = dict(choices)
        self.calls = []

    def _result(self, decision, choices):
        choice = self.choices[decision]
        assert choice in choices
        probabilities = {key: 0.0 for key in choices}
        probabilities[choice] = 1.0
        return FastDecisionResult(
            decision_id=f"fastdec-{decision}",
            provider="TEST",
            model="test",
            choice=choice,
            confidence=1.0,
            probabilities=probabilities,
            latency_ms=1,
            decided_at=NOW.isoformat(),
        )

    def choose(self, *, decision, state, choices, instructions=None):
        self.calls.append((decision, state, choices, instructions))
        return self._result(decision, choices)

    def choose_many(self, *, state, decisions, instructions=None):
        self.calls.append(("BATCH", state, decisions, instructions))
        return {
            decision: self._result(decision, choices)
            for decision, choices in decisions.items()
        }


def test_context_route_uses_l1_before_context_selection(tmp_path):
    provider = _Provider({"context_route": "REPAIR"})
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)

    route = l1.route_context(
        prompt="Diagnose why the X publication capability keeps stalling.",
        surface="agent_runtime",
    )

    assert route["route"] == "REPAIR"
    assert route["decision_type"] == "REPAIR"
    assert route["time_horizon"] == "CURRENT_STEP"
    assert provider.calls[0][0] == "context_route"


def test_explicit_dialogue_surface_does_not_spend_l1_call(tmp_path):
    provider = _Provider({})
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)

    route = l1.route_context(
        prompt="hello",
        surface="public_chat",
    )

    assert route["decision_type"] == "PUBLIC_DIALOGUE"
    assert route["provider"] == "SURFACE_CONTRACT"
    assert provider.calls == []


def test_health_l1_can_surface_stuck_without_hard_error(tmp_path):
    provider = _Provider({"health_state": "STUCK__goal_execution"})
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)

    result = l1.assess_health(
        task="CONTENT_COGNITION",
        deterministic={
            "primary_status": "OK",
            "abnormal": False,
            "subsystem": "goal_execution",
            "base_severity": 0.0,
            "prediction_error": 0.0,
            "negative_world_utility": 0.0,
            "symptoms": [],
        },
        recent_observations=[
            {
                "abnormal": False,
                "subsystem": "goal_execution",
                "primary_status": "OK",
            }
        ] * 4,
    )

    assert result["health_state"] == "STUCK"
    assert result["subsystem"] == "goal_execution"


def test_health_l1_cannot_hide_observed_failure(tmp_path):
    provider = _Provider({"health_state": "HEALTHY__none"})
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)

    with pytest.raises(FastDecisionError):
        l1.assess_health(
            task="CHECK_X",
            deterministic={
                "primary_status": "ERROR",
                "abnormal": True,
                "subsystem": "network",
                "base_severity": 0.86,
                "prediction_error": 0.0,
                "negative_world_utility": 0.0,
                "symptoms": [{
                    "kind": "hard_error",
                    "severity": 0.86,
                    "path": "result.status",
                    "detail": "ERROR",
                }],
            },
        )


def test_x_strategy_batches_bounded_choices_without_generating_text(tmp_path):
    provider = _Provider({
        "post_timing": "POST_NOW",
        "x_format": "LONG_POST",
        "exploration_mode": "EXPLORE",
        "angle": "BUILD_IN_PUBLIC",
    })
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)

    result = l1.choose_x_strategy(
        cycle="cycle-1",
        cognition={"worth_saying": True, "expression_kind": "METHOD_NOTE"},
        evidence={"resolution": {"action": "DRAFT", "useful_facts": ["f1"]}},
        mission={"status": "ACTIVE", "exploration_rate": 0.6},
        performance={"status": "INSUFFICIENT_SAMPLES"},
    )

    assert result["post_timing"] == "POST_NOW"
    assert result["x_format"] == "LONG_POST"
    assert result["exploration_mode"] == "EXPLORE"
    assert result["angle"] == "BUILD_IN_PUBLIC"
    assert result["interaction_mode"] == "ORIGINAL"
    batch = provider.calls[0]
    assert batch[0] == "BATCH"
    assert set(batch[2]) == {
        "post_timing",
        "x_format",
        "exploration_mode",
        "angle",
    }


def test_social_attention_routes_reply_before_l2_wording(tmp_path):
    provider = _Provider({
        "social_action": "REPLY",
        "social_category": "QUESTION",
        "evidence_value": "NOT_VALUABLE",
    })
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)

    result = l1.choose_social_attention(
        mention={"summary": "How does Ferro remember this?", "x_author_id": "u1"},
        relationship={"familiarity": 0.7},
        mission={"status": "ACTIVE"},
    )

    assert result["action"] == "REPLY"
    assert result["category"] == "QUESTION"
    assert result["valuable_evidence"] is False
    assert provider.calls[0][0] == "BATCH"

@pytest.mark.parametrize("timing", ["POST_NOW", "HOLD"])
def test_x_strategy_receives_actual_cognition_information_and_supported_facts(tmp_path, timing):
    provider = _Provider({"post_timing":timing,"x_format":"SHORT_POST","exploration_mode":"EXPLORE","angle":"BUILD_IN_PUBLIC"})
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)
    result = l1.choose_x_strategy(
        cycle="owner-cycle",
        cognition={
            "worth_saying":True,"expression_kind":"THOUGHT",
            "belief_proposition":"Durable jobs can outlive chat turns.",
            "what_changed":"Repository evidence strengthened this architectural belief.",
            "what_matters":"Bounded tools preserve control while detached execution preserves continuity.",
            "rationale":"The project documents the design; reliability outcomes remain unproven.",
        },
        evidence={"resolution":{"action":"DRAFT","useful_facts":["The repository documents persistent checkpoints."]}},
        mission={"status":"ACTIVE"},performance={},
        owner_goal={"goal_id":"owner-growth","origin":"EXTERNAL_MISSION","description":"Promote LivingRuntime Remote through a real X growth experiment."},
    )
    state = provider.calls[0][1]
    assert state["cognition"]["what_matters"].startswith("Bounded tools")
    assert state["cognition"]["belief_proposition"] == "Durable jobs can outlive chat turns."
    assert state["evidence"]["useful_facts"] == ["The repository documents persistent checkpoints."]
    assert result["post_timing"] == timing
    assert state["owner_goal"]["goal_id"] == "owner-growth"
    assert state["owner_goal"]["context_authority"] == "STRATEGY_ONLY"


def test_x_strategy_bounds_information_without_losing_the_fact_count(tmp_path):
    provider = _Provider({"post_timing":"HOLD","x_format":"SHORT_POST","exploration_mode":"EXPLORE","angle":"BUILD_IN_PUBLIC"})
    l1 = FerroL1Decisions(state_root=tmp_path, provider=provider)
    l1.choose_x_strategy(
        cycle="c",cognition={"what_matters":"m"*5000},
        evidence={"resolution":{"action":"DRAFT","useful_facts":["f"*2000]*20}},
        mission={},performance={},
    )
    state = provider.calls[0][1]
    assert len(state["cognition"]["what_matters"]) <= 1200
    assert state["evidence"]["useful_fact_count"] == 20
    assert len(state["evidence"]["useful_facts"]) == 6
    assert all(len(fact) <= 500 for fact in state["evidence"]["useful_facts"])
