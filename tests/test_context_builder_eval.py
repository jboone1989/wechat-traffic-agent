from scripts.evaluate_context_builder_effect import PAIRS, ablation_summary, make_case, structural_only_report
from wechat_traffic_agent.context_selection_policy import DEFAULT


def test_context_builder_ablation_summary_reports_effect_and_context_reduction():
    summary = ablation_summary({
        "current": {"accuracy": 0.50, "avg_context_chars": 800.0},
        "full": {"accuracy": 0.60, "avg_context_chars": 10000.0},
        "builder": {"accuracy": 0.80, "avg_context_chars": 2500.0},
        "oracle": {"accuracy": 0.90, "avg_context_chars": 1000.0},
    })

    assert summary == {
        "accuracy_gain_vs_current": 0.30,
        "accuracy_gain_vs_full": 0.20,
        "accuracy_gap_to_oracle": -0.10,
        "context_char_reduction_vs_full": 0.75,
    }



def _structural(policy):
    cases = [
        make_case(spec, variant, selection_policy=policy)
        for spec in PAIRS
        for variant in ("a", "b")
    ]
    report = structural_only_report(
        cases,
        model_reason="STRUCTURAL_ONLY",
    )["structural"]
    return {key: value for key, value in report.items() if key != "cases"}


def test_promoted_context_ratio_preserves_fixed_benchmark():
    candidate = dict(DEFAULT)
    candidate["coverage_ratio"] = 0.73

    baseline = _structural(DEFAULT)
    promoted = _structural(candidate)

    assert promoted["critical_fact_recall"] >= baseline["critical_fact_recall"]
    assert promoted["precision"] >= baseline["precision"]
    assert promoted["context_reduction"] >= baseline["context_reduction"] - 0.001
    assert promoted["budget_pass_rate"] == 1.0
