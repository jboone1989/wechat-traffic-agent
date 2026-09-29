from scripts.evaluate_context_builder_effect import ablation_summary


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
