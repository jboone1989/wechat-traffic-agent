from __future__ import annotations

import json

from wechat_traffic_agent.context_selection_policy import (
    DEFAULT,
    challenger_policies,
    changed_parameter,
    load_policy,
    materialize_policy,
    policy_ref,
)
from wechat_traffic_agent.decision_context import build_decision_context, candidate


def test_context_policy_challengers_change_exactly_one_parameter():
    rows = challenger_policies(DEFAULT)
    assert rows
    assert all(changed_parameter(DEFAULT, row) in DEFAULT for row in rows)
    assert all(
        sum(float(row[key]) != float(DEFAULT[key]) for key in DEFAULT) == 1
        for row in rows
    )


def test_decision_context_reports_loaded_policy(tmp_path):
    promoted = dict(DEFAULT)
    promoted["size_tax"] = 0.16
    materialize_policy(
        tmp_path,
        parameters=promoted,
        experiment_id="awe:test",
        decision="PROMOTE",
        previous_ref=policy_ref(DEFAULT),
    )

    goal_id = "goal:policy-test"
    critical = candidate(
        context_id="critical:policy-test",
        source="experience",
        kind="EVIDENCE",
        payload={"goal_id": goal_id, "target": "policy-test"},
        text="TARGET=policy-test repeated measured outcomes support the current choice",
        importance=0.95,
        confidence=0.95,
        refs=[goal_id, "policy-test"],
    )
    anchor = candidate(
        context_id="goal:policy-test",
        source="goal",
        kind="GOAL",
        payload={"goal_id": goal_id, "target": "policy-test"},
        text="TARGET=policy-test choose the next action",
        importance=1.0,
        confidence=1.0,
        current=True,
        refs=[goal_id, "policy-test"],
    )

    result = build_decision_context(
        prompt="TARGET=policy-test choose the next action",
        decision_frame={
            "decision_type": "SELECTION",
            "decision_question": "TARGET=policy-test choose the next action",
        },
        owner_scope={
            "goal_id": goal_id,
            "description": "TARGET=policy-test choose the next action",
            "topic": "policy-test",
        },
        missions=[],
        local_candidates=[anchor, critical],
        state_root=tmp_path,
        budget_chars=5000,
        max_facts=8,
    )

    assert result["selection_policy"] == promoted
    assert result["selection_policy_ref"] == policy_ref(promoted)
    assert load_policy(tmp_path) == promoted


def test_explicit_policy_override_is_bounded():
    bad = dict(DEFAULT)
    bad["size_tax"] = 9.0

    import pytest

    with pytest.raises(ValueError, match="outside bounds"):
        build_decision_context(
            prompt="test",
            decision_frame={"decision_type": "SELECTION"},
            owner_scope=None,
            missions=[],
            local_candidates=[],
            state_root=None,
            selection_policy=bad,
        )
