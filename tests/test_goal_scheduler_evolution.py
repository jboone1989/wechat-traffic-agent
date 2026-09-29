from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.goal_scheduler_evolution import (
    DEFAULT_PRIORITY_BONUS_SECONDS,
    GoalSchedulerEvolution,
)


NOW = datetime(2026, 9, 29, 10, 30, tzinfo=timezone.utc)


def c(goal_id: str, priority: float, age: float) -> dict:
    return {
        "goal_id": goal_id,
        "priority": priority,
        "age_seconds": age,
        "created_at": "2026-09-29T00:00:00+00:00",
    }


def test_default_fairness_and_effect_settlement(tmp_path):
    engine = GoalSchedulerEvolution(tmp_path)
    picked = engine.select(
        [c("high", 1.0, 100.0), c("old", 0.0, 500.0)],
        now=NOW,
    )
    assert picked["goal_id"] == "old"
    assert picked["priority_bonus_seconds"] == DEFAULT_PRIORITY_BONUS_SECONDS

    settled = engine.settle(
        picked["decision_id"],
        outcome={"status": "PUBLISHED", "world_utility": 0.8},
        now=NOW + timedelta(minutes=1),
    )
    assert settled["outcome"]["valid"] is True
    assert settled["outcome"]["score"] == 0.9


def test_execution_failure_is_not_scheduler_failure(tmp_path):
    engine = GoalSchedulerEvolution(tmp_path)
    picked = engine.select(
        [c("a", 0.9, 100.0), c("b", 0.8, 90.0)],
        now=NOW,
    )
    settled = engine.settle(
        picked["decision_id"],
        outcome={"status": "ERROR", "error_type": "BrowserUnavailable"},
        now=NOW + timedelta(seconds=30),
    )
    assert settled["outcome"]["valid"] is False
    assert settled["outcome"]["reason"] == "execution_or_infrastructure_failure"
    assert engine.projection()["settled_valid_count"] == 0


def test_one_mutation_then_real_prospective_awe_gate(tmp_path):
    engine = GoalSchedulerEvolution(tmp_path)
    state = engine._state()
    rows = []

    for index in range(2):
        rows += [
            {
                "decision_id": f"a{index}",
                "selected_at": NOW.isoformat(),
                "arm": "ACTIVE",
                "awe_experiment_id": None,
                "priority_bonus_seconds": 300.0,
                "selected_goal_id": "a",
                "candidates": [c("a", 1.0, 100.0)],
                "outcome": {"valid": True, "score": 1.0},
                "settled_at": NOW.isoformat(),
            },
            {
                "decision_id": f"b{index}",
                "selected_at": NOW.isoformat(),
                "arm": "ACTIVE",
                "awe_experiment_id": None,
                "priority_bonus_seconds": 300.0,
                "selected_goal_id": "b",
                "candidates": [c("b", 0.0, 450.0)],
                "outcome": {"valid": True, "score": 0.0},
                "settled_at": NOW.isoformat(),
            },
        ]

    for index in range(4):
        rows.append({
            "decision_id": f"x{index}",
            "selected_at": NOW.isoformat(),
            "arm": "ACTIVE",
            "awe_experiment_id": None,
            "priority_bonus_seconds": 300.0,
            "selected_goal_id": "b",
            "candidates": [
                c("a", 1.0, 100.0),
                c("b", 0.0, 450.0),
            ],
            "outcome": {"valid": True, "score": 0.0},
            "settled_at": NOW.isoformat(),
        })

    state["decisions"] = rows
    engine._save(state)

    first = engine.select(
        [c("a", 1.0, 100.0), c("b", 0.0, 450.0)],
        now=NOW,
    )
    trial = engine.projection()["trial"]
    assert trial["status"] == "AWAITING_PROSPECTIVE"
    assert trial["baseline_bonus_seconds"] == 300.0
    assert trial["challenger_bonus_seconds"] == 420.0
    assert first["arm"] in {"BASELINE", "CANDIDATE"}

    for index in range(8):
        at = NOW + timedelta(minutes=index + 1)
        choice = engine.select(
            [c("a", 1.0, 100.0), c("b", 0.0, 450.0)],
            now=at,
        )
        engine.settle(
            choice["decision_id"],
            outcome={
                "status": "PUBLISHED",
                "world_utility": 1.0 if choice["goal_id"] == "a" else -1.0,
            },
            now=at + timedelta(seconds=30),
        )
        if engine.projection()["trial"]["status"] == "PROMOTION_APPROVED":
            break

    projection = engine.projection()
    assert projection["trial"]["status"] == "PROMOTION_APPROVED"
    assert projection["status"] == "PROMOTION_APPROVED"
    assert projection["active_priority_bonus_seconds"] == 420.0

    awe = engine.awe.optimizer_view(projection["trial"]["awe_experiment_id"])
    hidden = awe["evaluator_receipt"]["hidden"]
    assert awe["status"] == "PROMOTION_APPROVED"
    assert awe["mutation_count"] == 1
    assert hidden["n_baseline"] >= 3
    assert hidden["n_candidate"] >= 3
    assert hidden["candidate_mean"] > hidden["baseline_mean"]
    assert "goal_id" not in str(hidden)



def test_active_trial_ignores_single_or_non_differentiating_choice(tmp_path):
    engine = GoalSchedulerEvolution(tmp_path)
    awe = engine.awe.start_experiment(
        capability_id="owner_goal_scheduler",
        hypothesis="bounded scheduler test",
        baseline_ref="baseline:300",
        mutation_ref="candidate:420",
        mutation_kind="GOAL_SCHEDULER_PRIORITY_BONUS_CHANGE",
        attempt_ref="single-candidate-test",
        now=NOW,
    )
    state = engine._state()
    state["trial"] = {
        "status": "AWAITING_PROSPECTIVE",
        "awe_experiment_id": awe["experiment_id"],
        "baseline_bonus_seconds": 300.0,
        "challenger_bonus_seconds": 420.0,
        "frozen_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(days=7)).isoformat(),
        "train": {"baseline_mean": 0.4, "candidate_mean": 0.7, "gain": 0.3},
    }
    engine._save(state)

    one = engine.select([c("a", 1.0, 100.0)], now=NOW + timedelta(minutes=1))
    assert one["arm"] == "ACTIVE"
    assert one["awe_experiment_id"] is None

    same = engine.select(
        [c("a", 1.0, 500.0), c("b", 0.0, 100.0)],
        now=NOW + timedelta(minutes=2),
    )
    assert same["arm"] == "ACTIVE"
    assert same["awe_experiment_id"] is None


def test_expired_trial_with_too_few_real_samples_rolls_back(tmp_path):
    engine = GoalSchedulerEvolution(tmp_path)
    awe = engine.awe.start_experiment(
        capability_id="owner_goal_scheduler",
        hypothesis="bounded scheduler expiry test",
        baseline_ref="baseline:300",
        mutation_ref="candidate:420",
        mutation_kind="GOAL_SCHEDULER_PRIORITY_BONUS_CHANGE",
        attempt_ref="expiry-test",
        now=NOW,
    )
    state = engine._state()
    state["trial"] = {
        "status": "AWAITING_PROSPECTIVE",
        "awe_experiment_id": awe["experiment_id"],
        "baseline_bonus_seconds": 300.0,
        "challenger_bonus_seconds": 420.0,
        "frozen_at": (NOW - timedelta(days=8)).isoformat(),
        "expires_at": (NOW - timedelta(days=1)).isoformat(),
        "train": {"baseline_mean": 0.4, "candidate_mean": 0.7, "gain": 0.3},
    }
    state["decisions"] = [{
        "decision_id": "expired-baseline-one",
        "selected_at": (NOW - timedelta(days=2)).isoformat(),
        "arm": "BASELINE",
        "awe_experiment_id": awe["experiment_id"],
        "priority_bonus_seconds": 300.0,
        "selected_goal_id": "a",
        "candidates": [c("a", 1.0, 100.0), c("b", 0.0, 450.0)],
        "outcome": None,
        "settled_at": None,
    }]
    engine._save(state)

    result = engine.settle(
        "expired-baseline-one",
        outcome={"status": "PUBLISHED", "world_utility": 0.2},
        now=NOW,
    )
    assert result["awe_gate"]["status"] == "ROLLBACK_REQUIRED"
    assert result["awe_gate"]["decision"] == "ROLLBACK_INSUFFICIENT_EVIDENCE"
    projection = engine.projection()
    assert projection["trial"]["status"] == "ROLLBACK_REQUIRED"
    assert projection["status"] == "ROLLBACK_REQUIRED"
