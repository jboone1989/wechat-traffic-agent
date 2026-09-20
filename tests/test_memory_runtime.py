from __future__ import annotations

from wechat_traffic_agent.memory_runtime import OwnerMemoryRuntime
from wechat_traffic_agent.operator_memory import OperatorLongTermMemory


def _message(message_id: str, role: str, text: str, at: str):
    return {
        "message_id": message_id,
        "role": role,
        "text": text,
        "created_at": at,
        "metadata": {},
    }


def test_owner_turn_becomes_recallable_episode_across_private_scope(tmp_path):
    runtime = OwnerMemoryRuntime(tmp_path)
    result = runtime.record_turn(
        owner_message=_message(
            "operator-chat:1", "operator",
            "We solved the storage-pressure problem by optimizing first and offloading rebuildable work second.",
            "2026-09-20T00:00:00Z",
        ),
        ferro_message=_message(
            "operator-chat:2", "ferro",
            "I will preserve that ordering and keep the core local unless a trusted migration is needed.",
            "2026-09-20T00:00:05Z",
        ),
    )
    episode = runtime.store.get_memory(result["episode_id"])
    assert episode is not None
    assert episode["memory_type"] == "EPISODIC"
    assert episode["source_type"] == "TRAJECTORY_CONSOLIDATION"
    assert episode["access_scope"] == "PRIVATE_OWNER"
    assert episode["evidence_refs"] == ["operator-chat:1", "operator-chat:2"]

    recalled = OperatorLongTermMemory(tmp_path).relevant(
        "How did we handle storage pressure and rebuildable work?", limit=8
    )
    assert any(row.get("memory_id") == result["episode_id"] for row in recalled)


def test_verified_readonly_tool_outcomes_form_and_revise_procedure(tmp_path):
    runtime = OwnerMemoryRuntime(tmp_path)
    first = runtime.record_turn(
        owner_message=_message("operator-chat:1", "operator", "Check the current page evidence.", "2026-09-20T00:01:00Z"),
        ferro_message=_message("operator-chat:2", "ferro", "The read-only lookup succeeded.", "2026-09-20T00:01:02Z"),
        tool_results=[{
            "capability_id": "web.search",
            "receipt_id": "receipt:web:1",
            "status": "SUCCESS",
            "ok": True,
        }],
    )
    assert first["episode_id"]
    active = runtime.store.query_memories(memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=20)
    assert len(active) == 1
    assert active[0]["payload"]["capabilities"] == ["web.search"]
    assert active[0]["payload"]["success_count"] == 1
    prior_id = active[0]["memory_id"]

    runtime.record_turn(
        owner_message=_message("operator-chat:3", "operator", "Check it again.", "2026-09-20T00:02:00Z"),
        ferro_message=_message("operator-chat:4", "ferro", "The provider returned a verified failure.", "2026-09-20T00:02:02Z"),
        tool_results=[{
            "capability_id": "web.search",
            "receipt_id": "receipt:web:2",
            "status": "PROVIDER_ERROR",
            "ok": False,
        }],
    )
    current = runtime.store.query_memories(memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=20)
    assert len(current) == 1
    assert current[0]["memory_id"] != prior_id
    assert current[0]["payload"]["success_count"] == 1
    assert current[0]["payload"]["failure_count"] == 1
    assert current[0]["supersedes"] == prior_id
    prior = runtime.store.get_memory(prior_id)
    assert prior["status"] == "SUPERSEDED"
    assert prior["superseded_by"] == current[0]["memory_id"]


def test_owner_recall_scope_excludes_non_private_records(tmp_path):
    memory = OperatorLongTermMemory(tmp_path)
    memory.store.append_memory({
        "memory_id": "memory:public-only",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Public visitor says the secret launch code is pineapple.",
        "created_at": "2026-09-20T00:00:00Z",
        "source_type": "PUBLIC_CONVERSATION_EVIDENCE",
        "source_ref": "public:1",
        "access_scope": "PUBLIC",
        "importance": 1.0,
    })
    recalled = memory.relevant("pineapple launch code", limit=12)
    assert all(row.get("memory_id") != "memory:public-only" for row in recalled)


def test_failed_tool_without_receipt_still_updates_procedure_from_persisted_turn(tmp_path):
    runtime = OwnerMemoryRuntime(tmp_path)
    result = runtime.record_turn(
        owner_message=_message("operator-chat:10", "operator", "Read the page.", "2026-09-20T00:10:00Z"),
        ferro_message=_message("operator-chat:11", "ferro", "The invocation failed before a receipt was issued.", "2026-09-20T00:10:02Z"),
        tool_results=[{
            "capability_id": "web.fetch",
            "receipt_id": "",
            "status": "INVOCATION_FAILED",
            "ok": False,
        }],
    )
    procedure = runtime.store.query_memories(memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=10)[0]
    assert procedure["payload"]["failure_count"] == 1
    assert "INVOCATION_FAILED" in procedure["payload"]["failure_modes"]
    assert any("inspect required invocation parameters" in row for row in procedure["payload"]["recovery"])
    episode = runtime.store.get_memory(result["episode_id"])
    tool = episode["payload"]["tool_outcomes"][0]
    assert tool["receipt_id"] == ""
    assert tool["evidence_ref"] == "operator-chat:11#tool-1"
    assert tool["evidence_ref"] in episode["evidence_refs"]
    assert result["memory_evolution"]["status"] == "ROUTED_TO_RUNTIME_EVOLUTION"


def test_repeated_experience_consolidates_into_semantic_knowledge(tmp_path):
    runtime = OwnerMemoryRuntime(tmp_path)
    knowledge_ids = []
    for index in range(3):
        result = runtime.record_turn(
            owner_message=_message(
                f"operator-chat:k{index * 2 + 1}", "operator", "Check the same live source safely.",
                f"2026-09-20T00:2{index}:00Z",
            ),
            ferro_message=_message(
                f"operator-chat:k{index * 2 + 2}", "ferro", "The bounded read-only check succeeded.",
                f"2026-09-20T00:2{index}:02Z",
            ),
            tool_results=[{
                "capability_id": "web.fetch",
                "receipt_id": f"receipt:web:k{index}",
                "status": "SUCCESS",
                "ok": True,
            }],
        )
        knowledge_ids.extend(result["knowledge_memory_ids"])
    active = runtime.store.query_memories(
        memory_types=["SEMANTIC"], statuses=["ACTIVE"], topic="experience-knowledge", limit=50,
    )
    assert active
    assert any(row["kind"] == "EXPERIENCE_KNOWLEDGE" for row in active)
    knowledge = next(row for row in active if row["kind"] == "EXPERIENCE_KNOWLEDGE")
    assert len(knowledge["evidence_refs"]) >= 2
    assert "Repeated experience supports:" in knowledge["text"]
    assert knowledge["memory_id"] in knowledge_ids


def test_explicit_owner_wrong_recall_feedback_settles_latest_retrieval(tmp_path):
    runtime = OwnerMemoryRuntime(tmp_path)
    retrieval = runtime.observatory.record_retrieval(
        query="what did I say about migration",
        retrieved_memory_ids=["memory:test"],
        recall_latency_ms=3.2,
        source="operator-long-term-memory",
    )
    result = runtime.record_turn(
        owner_message=_message("operator-chat:wrong-1", "operator", "你记错了，我说的是先优化现有资源。", "2026-09-20T01:00:00Z"),
        ferro_message=_message("operator-chat:wrong-2", "ferro", "收到纠正。", "2026-09-20T01:00:01Z"),
    )
    assert result["memory_evolution"]["status"] == "ROUTED_TO_RUNTIME_EVOLUTION"
    rows = runtime.observatory.rows(limit=20)
    failure = next(row for row in rows if row.get("event_type") == "FAILURE")
    assert failure["failure_kind"] == "WRONG_RECALL"
    settlement = next(row for row in rows if row.get("event_type") == "SETTLEMENT")
    assert settlement["parent_use_id"] == retrieval["memory_use_id"]
    assert settlement["metadata"]["memory_helped"] is False
