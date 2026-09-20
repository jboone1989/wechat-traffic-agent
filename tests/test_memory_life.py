from __future__ import annotations

from wechat_traffic_agent.memory_life import MemoryLifeRuntime


def test_repeated_real_process_outcomes_become_episode_procedure_relation_and_knowledge(tmp_path):
    life = MemoryLifeRuntime(tmp_path)
    results = []
    for index in range(1, 11):
        results.append(life.observe_process_outcome(
            process_id=f"cycle:{index}",
            topic="resource-research",
            action="fetch-and-verify-resource-evidence",
            success=index != 4,
            outcome="verified evidence persisted" if index != 4 else "provider timeout",
            evidence_refs=[f"receipt:{index}"],
            capabilities=["web.fetch"],
            steps=["fetch public evidence", "verify receipt", "persist evidence"],
            verification=["receipt_id exists", "evidence persisted"],
            failure_mode="provider_timeout" if index == 4 else "",
            recovery_step="retry with bounded fallback" if index == 4 else "",
        ))

    episodes = life.store.query_memories(
        memory_types=["EPISODIC"], statuses=["ACTIVE"], topic="resource-research", limit=20
    )
    assert len(episodes) == 10

    procedures = life.store.query_memories(
        memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=20
    )
    assert len(procedures) == 1
    payload = procedures[0]["payload"]
    assert payload["execution_count"] == 10
    assert payload["success_count"] == 9
    assert payload["failure_count"] == 1
    assert "provider_timeout" in payload["failure_modes"]

    relations = life.store.query_memories(
        memory_types=["RELATIONAL"], statuses=["ACTIVE"], limit=20
    )
    assert any((row.get("payload") or {}).get("object") == "web.fetch" for row in relations)

    assert results[2]["daily_knowledge_id"]
    assert results[-1]["long_term_knowledge_id"]
    long_term = life.store.get_memory(results[-1]["long_term_knowledge_id"])
    assert long_term is not None
    assert long_term["kind"] == "LONG_TERM_PATTERN"
    assert len(long_term["evidence_refs"]) == 10
    assert results[-1]["continuity_checkpoint"] is not None
    assert results[-1]["continuity_checkpoint"]["sequence"] >= 1
