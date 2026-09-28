from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.capability_contracts import (
    CapabilityContractMonitor,
    evaluate_context_contract,
    evaluate_task_contract,
)
from wechat_traffic_agent.interoceptive_fault import (
    InteroceptiveFaultSystem,
    classify_task_result,
)


NOW = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)


def _healthy_context():
    return {
        "continuity": {"application_cycle": 10, "mind_tick": 20},
        "decision_frame": {"decision_type": "GENERAL_COGNITION"},
        "decision_context": {
            "candidate_count": 5,
            "selected_count": 2,
            "information_needs": [{"need_id": "current_goal"}],
            "fact_index": [{"context_id": "goal:g1"}],
            "always_on_sources": [
                "phenotype_prior",
                "behavioral_state",
                "interoception",
            ],
        },
        "context_receipt": {
            "candidate_counts": {"missions": 0},
            "selection_summary": {
                "selected_sources": ["goal"],
            },
        },
        "missions": {"active": []},
        "phenotype_prior": {
            "stable_phenotype": {"personality_traits": {"persistence": 0.7}},
            "dynamic_modulation": {"affect": {"curiosity": 0.7}},
        },
        "behavioral_state": {
            "salient_expression_profile": [
                {"dimension": "exploration", "deviation": 0.2}
            ],
            "behavioral_directives": ["Explore unknowns actively."],
            "realization_targets": [],
        },
    }


def test_healthy_context_contract_passes():
    report = evaluate_context_contract(_healthy_context())
    assert report["status"] == "PASS"
    assert report["violations"] == []


def test_context_builder_can_be_sick_without_throwing():
    snapshot = _healthy_context()
    snapshot["decision_context"] = {
        **snapshot["decision_context"],
        "candidate_count": 12,
        "selected_count": 0,
        "fact_index": [],
    }
    snapshot["context_receipt"]["selection_summary"]["selected_sources"] = []

    report = evaluate_context_contract(snapshot)

    assert report["status"] == "VIOLATED"
    kinds = {row["kind"] for row in report["violations"]}
    assert "context_selection_empty" in kinds
    assert all(row["subsystem"] == "context" for row in report["violations"])


def test_owner_goal_omission_is_contract_violation_even_with_valid_dict():
    snapshot = _healthy_context()
    snapshot["decision_frame"]["decision_type"] = "OWNER_GOAL_ACTION"
    snapshot["context_receipt"]["candidate_counts"]["missions"] = 1
    snapshot["missions"] = {"active": []}

    report = evaluate_context_contract(snapshot)

    violation = next(
        row for row in report["violations"]
        if row["kind"] == "owner_goal_context_omitted"
    )
    assert violation["severity"] >= 0.8


def test_salient_neural_state_without_behavioral_realization_is_sick():
    snapshot = _healthy_context()
    snapshot["behavioral_state"]["behavioral_directives"] = []
    snapshot["behavioral_state"]["realization_targets"] = []

    report = evaluate_context_contract(snapshot)

    assert any(
        row["kind"] == "neural_behavior_realization_missing"
        and row["subsystem"] == "neural"
        for row in report["violations"]
    )


def test_no_effect_streak_is_goal_disease_even_when_task_status_ok():
    report = evaluate_task_contract(
        "RESOURCE_REVIEW",
        {
            "status": "OK",
            "autonomous_goal_outcome": {
                "status": "EXECUTED",
                "world_utility": 0.0,
                "drive_feedback": {
                    "no_effect_streak": 23,
                    "last_outcome_status": "EXECUTED",
                },
            },
        },
    )

    violation = next(
        row for row in report["violations"]
        if row["kind"] == "goal_effect_absent"
    )
    assert violation["severity"] >= 0.8
    assert violation["subsystem"] == "goal_execution"


def test_repeated_tool_with_same_result_is_silent_tool_breakpoint():
    report = evaluate_task_contract(
        "CONTENT_COGNITION",
        {
            "status": "OK",
            "owner_agent_episode": {
                "episode_id": "owner-episode:test",
                "status": "DECIDED",
                "steps": [
                    {
                        "decision": {"action": "INSPECT_MISSION"},
                        "result": {"status": "OK", "current": 1800},
                    },
                    {
                        "decision": {"action": "INSPECT_MISSION"},
                        "result": {"status": "OK", "current": 1800},
                    },
                ],
            },
        },
    )

    assert any(
        row["kind"] == "repeated_tool_without_new_evidence"
        and row["subsystem"] == "tool_use"
        for row in report["violations"]
    )


def test_contract_violation_becomes_interoceptive_symptom_with_ok_status(tmp_path):
    report = evaluate_task_contract(
        "RESOURCE_REVIEW",
        {
            "status": "OK",
            "goal": {"no_effect_streak": 8},
        },
    )
    classified = classify_task_result(
        "RESOURCE_REVIEW",
        {
            "status": "OK",
            "capability_contracts": report,
        },
    )
    assert classified["abnormal"] is True
    assert classified["primary_symptom"] == "contract_goal_effect_absent"
    assert classified["subsystem"] == "goal_execution"

    sensed = InteroceptiveFaultSystem(tmp_path).observe(
        "RESOURCE_REVIEW",
        {
            "status": "OK",
            "cycle": "silent-breakpoint",
            "capability_contracts": report,
        },
        now=NOW,
    )
    assert sensed["status"] == "SYMPTOM_OBSERVED"
    assert sensed["observation"]["primary_symptom"] == "contract_goal_effect_absent"
    assert sensed["projection"]["active_episode_ids"]


def test_context_monitor_deduplicates_same_decision_snapshot(tmp_path):
    monitor = CapabilityContractMonitor(tmp_path)
    snapshot = _healthy_context()
    snapshot["decision_context"]["selected_count"] = 0
    snapshot["decision_context"]["fact_index"] = []
    snapshot["context_receipt"]["selection_summary"]["selected_sources"] = []

    first = monitor.observe_context(snapshot, now=NOW)
    second = monitor.observe_context(snapshot, now=NOW + timedelta(seconds=2))

    assert first["status"] == "VIOLATED"
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["observation_id"] == first["observation_id"]
