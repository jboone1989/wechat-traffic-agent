from __future__ import annotations

from wechat_traffic_agent.memory_observation import MemoryLifeObservatory


def test_memory_life_observatory_tracks_real_use_failure_and_settlement(tmp_path):
    obs = MemoryLifeObservatory(tmp_path)
    obs.record_retrieval(
        query="recall resource policy",
        retrieved_memory_ids=["m:1", "m:2"],
        recall_latency_ms=8.5,
        source="owner",
    )
    decision = obs.record_decision_use(
        query="choose path",
        retrieved_memory_ids=["m:1", "m:2"],
        used_memory_ids=["m:2"],
        decision_ref="decision:1",
        evidence_refs=["evidence:1"],
    )
    obs.record_failure(
        "MISSED_RECALL",
        query="forgot durable fact",
        detail="expected memory absent",
        evidence_refs=["owner:correction"],
    )
    obs.settle(
        decision["memory_use_id"],
        outcome_ref="outcome:1",
        outcome_success=True,
        memory_helped=True,
        evidence_ref="world:1",
    )
    metrics = obs.metrics()
    assert metrics["retrieval_count"] == 1
    assert metrics["decision_use_count"] == 1
    assert metrics["failure_counts"] == {"MISSED_RECALL": 1}
    assert metrics["memory_help_rate"] == 1.0
    assert obs.decision_use("decision:1")["used_memory_ids"] == ["m:2"]
