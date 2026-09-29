from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.awe_hillclimb import FerroAWEHillclimb


NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)


def _bridge(tmp_path):
    return FerroAWEHillclimb(tmp_path)


def test_one_mutation_experiment_and_promote_receipt(tmp_path):
    bridge = _bridge(tmp_path)
    exp = bridge.start_experiment(
        capability_id="context_builder",
        hypothesis="increase fresh outcome recall",
        baseline_ref="context-policy:a",
        mutation_ref="context-policy:b",
        mutation_kind="SALIENCE_WEIGHT_CHANGE",
        target_domains=("grounding", "learning"),
        now=NOW,
    )
    assert exp["mutation_count"] == 1
    assert exp["hidden_cases_stored_in_ferro"] is False

    decided = bridge.ingest_evaluator_receipt(
        exp["experiment_id"],
        {
            "decision": "PROMOTE",
            "reason": "train and hidden improved beyond noise",
            "train": {"gain": 0.18, "failures": [{"scenario_id": "train.visible"}]},
            "hidden": {
                "baseline_mean": 0.60,
                "candidate_mean": 0.72,
                "gain": 0.12,
                "noise_floor": 0.03,
                "n_baseline": 2,
                "n_candidate": 2,
                "infrastructure_error_count": 0,
            },
            "regression": {"gain": 0.0, "max_regression": 0.01},
        },
        now=NOW,
    )
    assert decided["status"] == "PROMOTION_APPROVED"
    assert bridge.projection()["promotion_count"] == 1


def test_hidden_failure_details_cannot_cross_boundary(tmp_path):
    bridge = _bridge(tmp_path)
    exp = bridge.start_experiment(
        capability_id="context_builder",
        hypothesis="test",
        baseline_ref="a",
        mutation_ref="b",
        mutation_kind="ONE_CHANGE",
        now=NOW,
    )
    with pytest.raises(ValueError, match="hidden AWE receipt"):
        bridge.ingest_evaluator_receipt(
            exp["experiment_id"],
            {
                "decision": "ROLLBACK_NO_GENERALIZATION",
                "train": {},
                "hidden": {
                    "baseline_mean": 0.5,
                    "candidate_mean": 0.5,
                    "gain": 0.0,
                    "noise_floor": 0.01,
                    "n_baseline": 2,
                    "n_candidate": 2,
                    "infrastructure_error_count": 0,
                    "scenario_ids": ["secret.case"],
                },
            },
            now=NOW,
        )


def test_infrastructure_invalid_does_not_become_rollback(tmp_path):
    bridge = _bridge(tmp_path)
    exp = bridge.start_experiment(
        capability_id="goal_loop",
        hypothesis="test",
        baseline_ref="a",
        mutation_ref="b",
        mutation_kind="ONE_CHANGE",
        now=NOW,
    )
    result = bridge.ingest_evaluator_receipt(
        exp["experiment_id"],
        {
            "decision": "INVALID_INFRASTRUCTURE",
            "reason": "provider timeout",
            "train": {},
            "hidden": {
                "baseline_mean": None,
                "candidate_mean": None,
                "gain": None,
                "noise_floor": None,
                "n_baseline": 2,
                "n_candidate": 1,
                "infrastructure_error_count": 1,
            },
        },
        now=NOW,
    )
    assert result["status"] == "EVAL_INFRASTRUCTURE_INVALID"
    assert bridge.projection()["rollback_count"] == 0


def test_research_dream_trial_creates_awe_transfer_gate(tmp_path):
    bridge = _bridge(tmp_path)
    result = bridge.observe_research_dream(
        {
            "dream": {
                "dream_id": "research-dream:7",
                "decision": "START_RESEARCH_POLICY_TRIAL",
                "prospective_trial_id": "research-trial:7",
                "incumbent": {"policy_id": "policy:a"},
                "winner": {"policy_id": "policy:b"},
            }
        },
        now=NOW,
    )
    assert result is not None
    assert result["capability_id"] == "repair_priority_policy"
    assert result["status"] == "AWAITING_AWE"
    assert result["source_ref"] == "research-dream:7"
    assert result["target_ref"] == "research-trial:7"


def test_wakeful_dream_registers_awe_experiment(tmp_path, monkeypatch):
    from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    bound = []
    monkeypatch.setattr(runtime.dream_engine, "dream", lambda **_kwargs: {"status": "NEURAL_OK"})
    monkeypatch.setattr(
        runtime.research_dream,
        "dream",
        lambda **_kwargs: {
            "dream": {
                "dream_id": "research-dream:9",
                "decision": "START_RESEARCH_POLICY_TRIAL",
                "prospective_trial_id": "research-trial:9",
                "incumbent": {"policy_id": "policy:a"},
                "winner": {"policy_id": "policy:b"},
            }
        },
    )
    monkeypatch.setattr(
        runtime.research_dream,
        "bind_awe_experiment",
        lambda **kwargs: bound.append(kwargs) or kwargs,
    )

    result = runtime.run_neural_dream(now=NOW)

    assert result["awe_hillclimb"]["status"] == "AWAITING_AWE"
    assert bound[0]["trial_id"] == "research-trial:9"
    assert bound[0]["experiment_id"] == result["awe_hillclimb"]["experiment_id"]
    projection = runtime.dream_projection()
    assert projection["awe_hillclimb"]["active_count"] == 1


def test_runtime_accepts_non_research_capability_receipt_without_research_gate(tmp_path):
    from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    exp = runtime.awe_hillclimb.start_experiment(
        capability_id="context_builder",
        hypothesis="fresh outcome selection improves decisions",
        baseline_ref="context:a",
        mutation_ref="context:b",
        mutation_kind="SALIENCE_WEIGHT_CHANGE",
        now=NOW,
    )
    result = runtime.record_awe_hillclimb_receipt(
        exp["experiment_id"],
        {
            "decision": "PROMOTE",
            "reason": "generalized",
            "train": {"gain": 0.1},
            "hidden": {
                "baseline_mean": 0.5,
                "candidate_mean": 0.7,
                "gain": 0.2,
                "noise_floor": 0.02,
                "n_baseline": 2,
                "n_candidate": 2,
                "infrastructure_error_count": 0,
            },
        },
        now=NOW,
    )

    assert result["status"] == "PROMOTION_APPROVED"
    assert result["research_trial"] is None


def test_open_research_trial_can_recover_missing_awe_registration(tmp_path):
    bridge = _bridge(tmp_path)
    exp = bridge.ensure_research_trial_experiment(
        {
            "trial_id": "research-trial:migrated",
            "baseline_policy": {"policy_id": "policy:a"},
            "challenger_policy": {"policy_id": "policy:b"},
        },
        source_ref="migration",
        now=NOW,
    )

    assert exp["target_ref"] == "research-trial:migrated"
    assert exp["status"] == "AWAITING_AWE"



def test_wakeful_dream_auto_consumes_repair_policy_hidden_receipt(
    tmp_path,
    monkeypatch,
):
    from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    experiment = runtime.awe_hillclimb.start_experiment(
        capability_id="repair_priority_policy",
        hypothesis="generalize repair ranking",
        baseline_ref="policy:a",
        mutation_ref="policy:b",
        mutation_kind="RESEARCH_POLICY_WEIGHT_MUTATION",
        target_ref="research-trial:auto",
        now=NOW,
    )
    monkeypatch.setattr(
        runtime.dream_engine,
        "dream",
        lambda **_kwargs: {"status": "NEURAL_OK"},
    )
    monkeypatch.setattr(
        runtime.research_dream,
        "dream",
        lambda **_kwargs: {
            "status": "RESEARCH_DREAMED",
            "dream": {"decision": "WAIT_FOR_AWE_GENERALIZATION"},
        },
    )
    monkeypatch.setattr(
        runtime.research_dream,
        "projection",
        lambda: {
            "prospective_trial": {
                "trial_id": "research-trial:auto",
                "status": "AWAITING_AWE_GATE",
                "awe_experiment_id": experiment["experiment_id"],
            }
        },
    )
    monkeypatch.setattr(
        runtime.research_dream,
        "awe_hidden_replay_receipt",
        lambda **_kwargs: {
            "decision": "WAIT_FOR_EVIDENCE",
            "reason": "new hidden outcomes have not arrived",
            "train": {"gain": 0.2},
            "hidden": {
                "baseline_mean": None,
                "candidate_mean": None,
                "gain": None,
                "noise_floor": 0.0,
                "n_baseline": 0,
                "n_candidate": 0,
                "infrastructure_error_count": 0,
            },
        },
    )
    gated = []
    monkeypatch.setattr(
        runtime.research_dream,
        "apply_awe_gate",
        lambda **kwargs: gated.append(kwargs) or {
            "status": "AWAITING_AWE_GATE",
            "decision": "WAIT_FOR_AWE_GENERALIZATION",
        },
    )

    result = runtime.run_neural_dream(now=NOW)

    assert result["awe_hillclimb_evaluation"]["decision"] == "WAIT_FOR_EVIDENCE"
    assert result["awe_hillclimb"]["status"] == "AWAITING_AWE"
    assert gated == [{
        "experiment_id": experiment["experiment_id"],
        "decision": "WAIT_FOR_EVIDENCE",
        "now": NOW,
    }]



def test_same_mutation_can_be_retried_only_with_new_attempt_ref(tmp_path):
    bridge = _bridge(tmp_path)
    first = bridge.start_experiment(
        capability_id="context_builder",
        hypothesis="retry lifecycle",
        baseline_ref="context:a",
        mutation_ref="context:b",
        mutation_kind="ONE_CHANGE",
        attempt_ref="evidence-epoch:1",
        now=NOW,
    )
    duplicate = bridge.start_experiment(
        capability_id="context_builder",
        hypothesis="retry lifecycle",
        baseline_ref="context:a",
        mutation_ref="context:b",
        mutation_kind="ONE_CHANGE",
        attempt_ref="evidence-epoch:1",
        now=NOW,
    )
    second = bridge.start_experiment(
        capability_id="context_builder",
        hypothesis="retry lifecycle",
        baseline_ref="context:a",
        mutation_ref="context:b",
        mutation_kind="ONE_CHANGE",
        attempt_ref="evidence-epoch:2",
        now=NOW,
    )

    assert duplicate["experiment_id"] == first["experiment_id"]
    assert second["experiment_id"] != first["experiment_id"]
    assert first["attempt_ref"] == "evidence-epoch:1"
    assert second["attempt_ref"] == "evidence-epoch:2"



def test_multiple_awe_instances_refresh_shared_ledger_before_writes(tmp_path):
    first = FerroAWEHillclimb(tmp_path)
    second = FerroAWEHillclimb(tmp_path)

    exp_a = first.start_experiment(
        capability_id="context_builder",
        hypothesis="first bounded mutation",
        baseline_ref="context:base",
        mutation_ref="context:challenger",
        mutation_kind="CONTEXT_POLICY_CHANGE",
        attempt_ref="attempt-a",
        now=NOW,
    )
    exp_b = second.start_experiment(
        capability_id="owner_goal_scheduler",
        hypothesis="second bounded mutation",
        baseline_ref="goal:base",
        mutation_ref="goal:challenger",
        mutation_kind="GOAL_SCHEDULER_PRIORITY_BONUS_CHANGE",
        attempt_ref="attempt-b",
        now=NOW,
    )

    projection = first.projection()
    ids = {
        row["experiment_id"]
        for row in projection["recent_experiments"]
    }
    assert exp_a["experiment_id"] in ids
    assert exp_b["experiment_id"] in ids

    second.ingest_evaluator_receipt(
        exp_a["experiment_id"],
        {
            "decision": "PROMOTE",
            "reason": "hidden generalization passed",
            "train": {"gain": 0.1},
            "hidden": {
                "baseline_mean": 0.5,
                "candidate_mean": 0.7,
                "gain": 0.2,
                "noise_floor": 0.0,
                "n_baseline": 4,
                "n_candidate": 4,
                "infrastructure_error_count": 0,
            },
            "regression": {},
        },
        now=NOW,
    )

    assert first.optimizer_view(exp_a["experiment_id"])["status"] == "PROMOTION_APPROVED"
    assert first.optimizer_view(exp_b["experiment_id"])["status"] == "AWAITING_AWE"
