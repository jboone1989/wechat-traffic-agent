"""Paired, frozen, isolated evaluation on exact same Ferro decision context."""
import asyncio
import json
import sqlite3

import pytest

from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.learning_evaluation import (
    evaluate_learning, load_benchmark, paired_p_value, scorecard,
    LearningEvalLedger, compare_runs, load_review_receipt,
)


class MemorySensitiveProvider:
    def __init__(self):
        self.prompts = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        # Simulates a provider relying on a retrieved, previously learned
        # fact; this is a harness test, NOT evidence that a real LLM learned.
        memory_context = json.loads(
            prompt.partition("FERRO CONTEXT")[2].split("BENCHMARK CONTEXT")[0].strip()
        )
        with_memory = any("OrchidCoin correction" in str(row.get("text"))
                          for row in memory_context.get("recalled_memories", []))
        return json.dumps({
            "action_id": "hold" if with_memory else "rush",
            "declared_confidence": 0.6,
        })


def _fixture(tmp_path):
    root = tmp_path / "isolated"
    root.mkdir()
    (root / AWE_SENTINEL).write_text("isolated evaluation\n")
    CanonicalMemoryStore(root).append_memory({
        "memory_id": "research:orchid-correction",
        "memory_type": "SEMANTIC",
        "kind": "RESEARCH_UNDERSTANDING",
        "text": "OrchidCoin correction: API timestamps are next-day reports, not intraday observations; avoid same-day causal claims.",
        "created_at": "2026-07-01T00:00:00Z",
        "status": "ACTIVE",
        "source_type": "RESEARCH_RECEIPT",
        "source_ref": "verified-case-study",
        "evidence_refs": ["published-report:orchid-v1"],
        "topics": ["finance"],
    })
    benchmark = {
        "benchmark_id": "ferro-knowledge-transfer",
        "version": "example-v1",
        "cases": [
            {
                "case_id": "retention-1",
                "split": "retention",
                "observation": "A dated OrchidCoin correction warns the venue's ETF flow data arrive after the equity session. What is defensible?",
                "actions": {
                    "rush": "Treat Asian-morning flows as contemporaneous US-close proof.",
                    "hold": "Identify publication lag before describing intraday causality.",
                },
                "gold_action": "hold",
                "source_refs": ["published-report:orchid-v1"],
            },
            {
                "case_id": "near-1",
                "split": "near_transfer",
                "observation": "For OrchidCoin correction, apply the learned next-day ETF report rule to a different country. What is defensible?",
                "actions": {
                    "rush": "Treat all timestamps as same-session observations.",
                    "hold": "Align the time windows before making any causal inference.",
                },
                "gold_action": "hold",
                "source_refs": ["published-report:orchid-v1"],
            },
        ],
    }
    f = tmp_path / "bench.json"
    f.write_text(json.dumps(benchmark))
    return root, f


def test_identical_provider_both_arms_with_rubric_hidden(tmp_path):
    root, path = _fixture(tmp_path)
    provider = MemorySensitiveProvider()
    result = asyncio.run(evaluate_learning(
        provider=provider, snapshot_root=root,
        benchmark_path=path, ledger_path=tmp_path / "out/evals.sqlite3",
        model_id="test-provider",
    ))
    assert result["scorecard"]["cases_scored"] == 2
    assert result["scorecard"]["wins"] == 2
    assert result["scorecard"]["long_term_capability_improvement_proven"] is False
    assert all("gold_action" not in p for p in provider.prompts)
    assert len(provider.prompts) == 4
    assert all("FERRO CONTEXT" in p for p in provider.prompts)
    with sqlite3.connect(tmp_path / "out/evals.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert (root / AWE_SENTINEL).exists()


def test_missing_isolation_refuses_production_root_before_provider(tmp_path):
    _, benchmark = _fixture(tmp_path)
    with pytest.raises(ValueError, match="isolated"):
        asyncio.run(evaluate_learning(
            provider=MemorySensitiveProvider(), snapshot_root=tmp_path,
            benchmark_path=benchmark, ledger_path=tmp_path / "out.sqlite3",
            model_id="test",
        ))


def test_invalid_cases_and_scores_never_claim_learning(tmp_path):
    _, path = _fixture(tmp_path)
    bad = json.loads(path.read_text())
    bad["cases"][1]["gold_action"] = "not-an-option"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="expected action"):
        load_benchmark(path)
    assert paired_p_value(5, 0) == .0625
    assert scorecard([])["evidence_of_memory_benefit"] is False
    assert scorecard([{"split": "retention", "status": "ERROR"}])["cases_scored"] == 0


def test_frozen_runs_detect_forgetting_without_pretending_causality(tmp_path):
    ledger = LearningEvalLedger(tmp_path / "comparisons.sqlite3")
    def save(run_id, snapshot, memory_correct):
        case = {
            "case_id": "remembered-rule", "split": "retention",
            "status": "SCORED", "without_memory_action": "wrong",
            "with_memory_action": "correct" if memory_correct else "wrong",
            "without_memory_correct": 0,
            "with_memory_correct": int(memory_correct),
            "source_refs": ["evidence:stable"],
        }
        ledger.save({
            "run_id": run_id, "benchmark_id": "same",
            "benchmark_version": "v1", "benchmark_sha256": "abc",
            "snapshot_sha256": snapshot, "model_id": "same-model",
            "started_at": "2026-10-08T00:00:00Z",
            "scorecard": scorecard([case]),
        }, [case])
    save("before", "snapshot:1", True)
    save("after", "snapshot:2", False)
    report = compare_runs(ledger.path, "before", "after")
    assert report["by_split"]["retention"]["regressions"] == 1
    assert report["by_split"]["retention"]["improvements"] == 0
    assert report["no_regressions_observed"] is False
    assert report["long_term_capability_improvement_proven"] is False
    with pytest.raises(ValueError, match="changed memory snapshot"):
        compare_runs(ledger.path, "before", "before")


def test_rephrasing_one_source_cannot_falsely_prove_learning():
    # Twenty-four winning questions about one article must NOT be treated
    # as twenty-four independent findings.
    rows = [
        {
            "case_id": f"paraphrase-{i}",
            "split": ("retention", "near_transfer", "far_transfer")[i % 3],
            "status": "SCORED", "source_refs": ["paper:one"],
            "with_memory_correct": 1, "without_memory_correct": 0,
        }
        for i in range(48)
    ]
    result = scorecard(rows)
    assert result["wins"] == 48
    assert result["independent_source_groups"] == 1
    assert result["source_group_wins"] == 1
    assert result["evidence_of_memory_benefit"] is False


def test_independent_group_gate_and_zero_validity():
    rows = [
        {
            "case_id": f"source-{i}",
            "split": ("retention", "near_transfer", "far_transfer")[i % 3],
            "status": "SCORED", "source_refs": [f"published:source:{i}"],
            "with_memory_correct": 1, "without_memory_correct": 0,
        }
        for i in range(24)
    ]
    result = scorecard(rows)
    assert result["independent_source_groups"] == 24
    assert result["source_group_wins"] == 24
    # Statistical evidence is only one gate; independent review also required.
    assert result["evidence_of_memory_benefit"] is True
    rows[0]["status"] = "ERROR"
    assert scorecard(rows)["cases_scored"] == 23
    assert scorecard(rows)["evidence_of_memory_benefit"] is False


def test_transitively_shared_sources_count_as_one_independent_cluster():
    rows = [
        {"case_id": "a", "split": "retention", "status": "SCORED",
         "source_refs": ["source:1", "source:2"],
         "with_memory_correct": 1, "without_memory_correct": 0},
        {"case_id": "b", "split": "near_transfer", "status": "SCORED",
         "source_refs": ["source:2", "source:3"],
         "with_memory_correct": 1, "without_memory_correct": 0},
        {"case_id": "c", "split": "far_transfer", "status": "SCORED",
         "source_refs": ["source:3", "source:4"],
         "with_memory_correct": 1, "without_memory_correct": 0},
    ]
    result = scorecard(rows)
    assert result["wins"] == 3
    assert result["independent_source_groups"] == 1
    assert result["source_group_wins"] == 1
    assert result["evidence_of_memory_benefit"] is False


def test_approval_label_without_independent_receipt_cannot_certify_evidence(tmp_path):
    root, path = _fixture(tmp_path)
    benchmark = json.loads(path.read_text())
    benchmark["review_status"] = "APPROVED"
    path.write_text(json.dumps(benchmark))
    result = asyncio.run(evaluate_learning(
        provider=MemorySensitiveProvider(), snapshot_root=root,
        benchmark_path=path, ledger_path=tmp_path / "status.sqlite3",
        model_id="test-provider",
    ))
    assert result["scorecard"]["benchmark_review_status"] == "PROVISIONAL"
    assert result["scorecard"]["evidence_of_memory_benefit"] is False


def test_review_receipt_requires_same_frozen_case_ids_and_sha(tmp_path):
    _, path = _fixture(tmp_path)
    from wechat_traffic_agent.learning_evaluation import load_benchmark
    spec, cases, digest = load_benchmark(path)
    review = {
        "benchmark_sha256": digest,
        "reviewed_case_ids": [c.case_id for c in cases],
        "reviewed_by": "external-reviewer",
        "reviewed_at": "2026-10-08T14:00:00Z",
        "source_evidence_checked": True,
    }
    receipt = tmp_path / "review.json"
    receipt.write_text(json.dumps(review))
    valid = load_review_receipt(
        receipt, benchmark_sha256=digest, case_ids=[c.case_id for c in cases]
    )
    assert valid["status"] == "REVIEW_RECEIPT_RECORDED"
    review["benchmark_sha256"] = "stale-edited-benchmark"
    receipt.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="frozen benchmark"):
        load_review_receipt(
            receipt, benchmark_sha256=digest,
            case_ids=[c.case_id for c in cases],
        )
