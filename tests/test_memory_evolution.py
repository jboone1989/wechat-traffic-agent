from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.memory_evolution import (
    MemoryBenchmarkCase,
    MemoryEvolutionEngine,
    architecture_fingerprint,
)
from wechat_traffic_agent.memory_retrieval import MemoryRouter, RecallWeights
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


def _seed(root):
    store = CanonicalMemoryStore(root)
    store.append_memory({
        "memory_id": "m:resource",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "When resource pressure rises, offload replaceable workloads first.",
        "created_at": "2026-09-18T12:00:00Z",
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:resource",
        "entities": ["Ferro"],
        "topics": ["survival-resources"],
    })
    store.append_memory({
        "memory_id": "m:social",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Write concise social posts with verified evidence.",
        "created_at": "2026-09-19T12:00:00Z",
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:social",
        "entities": ["Social"],
        "topics": ["writing"],
    })
    return store


def _poor_weights():
    return RecallWeights(
        lexical_weight=0.0,
        vector_weight=0.0,
        structured_weight=1.0,
        importance_weight=0.0,
        confidence_weight=0.0,
        temporal_boost=0.0,
        decay_enabled=False,
    )


def test_evolution_signals_are_bounded_and_evidence_backed(tmp_path):
    engine = MemoryEvolutionEngine(tmp_path, store=_seed(tmp_path))
    row = engine.observe_signal(
        "RECALL_FAILURE",
        evidence_ref="evaluation:case-1",
        detail="semantic paraphrase missed the relevant memory",
        metric=0.0,
    )
    assert row["kind"] == "RECALL_FAILURE"
    proposed = engine.propose_from_signals()
    assert proposed.vector_weight > RecallWeights().vector_weight
    with pytest.raises(ValueError, match="requires evidence_ref"):
        engine.observe_signal("RECALL_FAILURE", evidence_ref="", detail="missing")
    with pytest.raises(ValueError, match="unsupported"):
        engine.observe_signal("CHANGE_SCHEMA", evidence_ref="x", detail="not allowed")


def test_recall_policy_rejects_unknown_architecture_fields():
    values = RecallWeights().as_dict()
    values["rewrite_memory_truth"] = True
    with pytest.raises(ValueError, match="unknown recall policy fields"):
        RecallWeights.from_mapping(values)
    assert architecture_fingerprint()


def test_candidate_promotion_requires_benchmark_improvement_without_protected_regression(tmp_path):
    store = _seed(tmp_path)
    engine = MemoryEvolutionEngine(tmp_path, store=store, baseline_weights=_poor_weights())
    cases = [
        MemoryBenchmarkCase(
            case_id="semantic-paraphrase",
            query="hardware overloaded machine capacity",
            expected_ids=("m:resource",),
            category="semantic",
            limit=1,
        ),
        MemoryBenchmarkCase(
            case_id="temporal-exact-day",
            query="2026-09-18 resource",
            expected_ids=("m:resource",),
            category="temporal",
            limit=1,
            now=datetime(2026, 9, 19, tzinfo=timezone.utc),
        ),
    ]
    challenger = RecallWeights(
        lexical_weight=1.0,
        vector_weight=3.0,
        structured_weight=1.0,
        importance_weight=0.0,
        confidence_weight=0.0,
        temporal_boost=0.01,
        decay_enabled=False,
    )
    experiment = engine.evaluate_candidate(
        challenger,
        cases,
        evidence_refs=["memory-signal:recall-failure"],
        rationale="increase associative recall after a measured paraphrase miss",
    )
    assert experiment["challenger_result"]["score"] > experiment["baseline_result"]["score"]
    decision = engine.decide(experiment["experiment_id"])
    assert decision["status"] == "PROMOTED"
    assert decision["protected_regressions"] == []

    # A new router consumes only the promoted, benchmark-backed policy.
    engine.baseline_weights = None
    router = MemoryRouter(tmp_path, store=store)
    assert router.weights.vector_weight == 3.0
    assert router.recall("hardware overloaded machine capacity", limit=1)[0]["memory_id"] == "m:resource"


def test_protected_category_regression_blocks_promotion(tmp_path):
    store = _seed(tmp_path)
    good = RecallWeights(
        lexical_weight=1.0,
        vector_weight=3.0,
        structured_weight=1.0,
        importance_weight=0.0,
        confidence_weight=0.0,
        decay_enabled=False,
    )
    engine = MemoryEvolutionEngine(tmp_path, store=store, baseline_weights=good)
    cases = [
        MemoryBenchmarkCase(
            case_id="protected-exact-resource",
            query="hardware overloaded machine capacity",
            expected_ids=("m:resource",),
            category="exact",
            limit=1,
        ),
        MemoryBenchmarkCase(
            case_id="semantic-social",
            query="concise verified social posts",
            expected_ids=("m:social",),
            category="semantic",
            limit=1,
        ),
    ]
    bad = _poor_weights()
    experiment = engine.evaluate_candidate(
        bad,
        cases,
        evidence_refs=["memory-signal:index-cost"],
        rationale="over-aggressive simplification",
    )
    decision = engine.decide(experiment["experiment_id"])
    assert decision["status"] == "REJECTED"
    assert "exact" in decision["protected_regressions"] or decision["improved"] is False
