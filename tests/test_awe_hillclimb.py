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
