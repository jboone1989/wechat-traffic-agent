from __future__ import annotations

import json

from wechat_traffic_agent import context_builder_evolution as mod
from wechat_traffic_agent.context_builder_evolution import ContextBuilderEvolution
from wechat_traffic_agent.context_selection_policy import DEFAULT, load_policy, policy_ref


def test_generated_train_failure_promotes_generalizing_single_parameter(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        ContextBuilderEvolution,
        "_hidden_seed",
        lambda self: "stable-hidden-context-seed",
    )
    engine = ContextBuilderEvolution(tmp_path)
    result = engine.sync()

    assert result["status"] == "PROMOTION_APPROVED"
    assert result["decision"] == "PROMOTE"
    assert result["changed_parameter"] == "coverage_ratio"
    assert result["failure_clusters"]["IRRELEVANT_CONTEXT_OVERSELECTION"] > 0
    assert set(result["failure_clusters"]) == {"IRRELEVANT_CONTEXT_OVERSELECTION"}
    assert result["hidden"]["gain"] > 0
    assert load_policy(tmp_path)["coverage_ratio"] > DEFAULT["coverage_ratio"]

    settled = ContextBuilderEvolution(tmp_path).sync()
    assert settled["status"] == "HEALTHY_NO_MUTATION"


def test_context_builder_auto_promotes_single_parameter_when_train_and_hidden_transfer(
    tmp_path,
    monkeypatch,
):
    baseline_ref = policy_ref(DEFAULT)

    def fake_evaluate(policy, cases):
        changed = float(policy["size_tax"]) != float(DEFAULT["size_tax"])
        useful = changed and float(policy["size_tax"]) > float(DEFAULT["size_tax"])
        return {
            "critical_recall": 1.0 if useful else 0.90,
            "owner_anchor_recall": 1.0,
            "precision": 0.98 if useful else 0.80,
            "context_reduction": 0.94 if useful else 0.75,
            "avg_selected_count": 2.0 if useful else 4.0,
            "utility": 1.243 if useful else 1.0975,
            "case_count": len(cases),
            "failures": [] if useful else [
                {"case_id": "train-visible", "failure": "CRITICAL_FACT_MISSED"}
            ],
            "healthy": useful,
        }

    monkeypatch.setattr(mod, "_evaluate", fake_evaluate)
    monkeypatch.setattr(
        ContextBuilderEvolution,
        "_hidden_seed",
        lambda self: "private-hidden-seed",
    )

    engine = ContextBuilderEvolution(tmp_path)
    result = engine.sync()

    assert result["status"] == "PROMOTION_APPROVED"
    assert result["decision"] == "PROMOTE"
    assert result["changed_parameter"] == "size_tax"
    assert result["baseline_ref"] == baseline_ref
    assert load_policy(tmp_path)["size_tax"] > DEFAULT["size_tax"]

    awe = json.loads(
        (tmp_path / "application" / "awe_hillclimb.json").read_text(encoding="utf-8")
    )
    encoded = json.dumps(awe, sort_keys=True)
    assert "private-hidden-seed" not in encoded
    assert "train-visible" not in encoded
    assert "hidden_evaluated" not in encoded


def test_context_builder_rolls_back_when_train_gain_does_not_generalize(
    tmp_path,
    monkeypatch,
):
    calls = {"count": 0}

    def fake_evaluate(policy, cases):
        calls["count"] += 1
        changed = policy != DEFAULT
        hidden_phase = calls["count"] > 1 + len(mod.challenger_policies(DEFAULT))
        if not changed:
            utility = 1.0 if not hidden_phase else 1.10
            recall = 0.90 if not hidden_phase else 1.0
        else:
            utility = 1.20 if not hidden_phase else 1.00
            recall = 1.0 if not hidden_phase else 0.90
        return {
            "critical_recall": recall,
            "owner_anchor_recall": 1.0,
            "precision": 0.95,
            "context_reduction": 0.90,
            "avg_selected_count": 2.0,
            "utility": utility,
            "case_count": len(cases),
            "failures": [] if recall == 1.0 else [
                {"case_id": "visible", "failure": "CRITICAL_FACT_MISSED"}
            ],
            "healthy": False,
        }

    monkeypatch.setattr(mod, "_evaluate", fake_evaluate)
    monkeypatch.setattr(
        ContextBuilderEvolution,
        "_hidden_seed",
        lambda self: "private-hidden-seed",
    )

    engine = ContextBuilderEvolution(tmp_path)
    result = engine.sync()

    assert result["decision"] == "ROLLBACK_NO_GENERALIZATION"
    assert result["status"] == "ROLLBACK_REQUIRED"
    assert load_policy(tmp_path) == DEFAULT

    calls["count"] = 0
    repeated = engine.sync()
    assert repeated["status"] == "NO_NEW_FAILURE_EVIDENCE"
    assert repeated["previous_terminal_status"] == "ROLLBACK_REQUIRED"
