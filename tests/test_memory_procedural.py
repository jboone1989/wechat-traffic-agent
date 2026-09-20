from __future__ import annotations

from wechat_traffic_agent.memory_procedural import ProceduralMemoryService


def test_procedure_is_retrievable_and_structured(tmp_path):
    service = ProceduralMemoryService(tmp_path)
    proc = service.learn_procedure(
        name="publish_x_post",
        description="Publish one verified X post through the bound account.",
        preconditions=["authenticated X account"],
        capabilities=["x.post"],
        steps=["compose", "publish"],
        verification=["POSTED receipt"],
        failure_modes=["session expired"],
        recovery=["refresh persistent profile"],
        expected_cost={"model_calls": 1},
        source_type="VERIFIED_EXECUTION_OUTCOME",
        source_ref="receipt:x:1",
        evidence_refs=["receipt:x:1"],
    )
    stored = service.store.get_memory(proc["memory_id"])
    assert stored["memory_type"] == "PROCEDURAL"
    assert stored["payload"]["verification"] == ["POSTED receipt"]
    recalled = service.retrieve("publish X post")
    assert recalled and recalled[0]["memory_id"] == proc["memory_id"]


def test_verified_outcome_revises_procedure_without_rewriting_history(tmp_path):
    service = ProceduralMemoryService(tmp_path)
    first = service.learn_procedure(
        name="repair_service",
        description="Repair a bounded service defect.",
        preconditions=["reproducible failure"], capabilities=["code.patch"],
        steps=["diagnose", "patch", "test"], verification=["focused tests pass"],
        source_type="VERIFIED_EXECUTION_OUTCOME", source_ref="repair:1",
        evidence_refs=["repair:1"],
    )
    revised = service.record_outcome(
        first["memory_id"], success=False, evidence_ref="repair:2",
        failure_mode="focused test exposed a second defect",
        recovery_step="re-diagnose before extending patch",
    )
    old = service.store.get_memory(first["memory_id"])
    assert old["status"] == "SUPERSEDED"
    assert revised["payload"]["failure_count"] == 1
    assert revised["payload"]["execution_count"] == 1
    assert revised["payload"]["last_outcome"] == "FAILURE"
    assert "repair:2" in revised["evidence_refs"]
    assert "focused test exposed a second defect" in revised["payload"]["failure_modes"]


def test_procedure_requires_verified_evidence(tmp_path):
    service = ProceduralMemoryService(tmp_path)
    try:
        service.learn_procedure(
            name="unsafe", description="No evidence", preconditions=[], capabilities=[],
            steps=["guess"], verification=[], source_type="MODEL_GUESS",
            source_ref="guess:1", evidence_refs=[],
        )
    except ValueError as exc:
        assert "verified evidence" in str(exc)
    else:
        raise AssertionError("procedure without evidence must fail")


def test_schema1_procedure_can_be_selected_as_baseline_variant_before_next_revision(tmp_path):
    service = ProceduralMemoryService(tmp_path)
    service.store.append_memory({
        "memory_id": "legacy-proc",
        "memory_type": "PROCEDURAL",
        "kind": "PROCEDURE",
        "text": "Procedure legacy: old but valid path",
        "created_at": "2026-09-20T00:00:00Z",
        "status": "ACTIVE",
        "source_type": "VERIFIED_EXECUTION_OUTCOME",
        "source_ref": "receipt:legacy",
        "access_scope": "PRIVATE_OWNER",
        "evidence_refs": ["receipt:legacy"],
        "payload": {
            "procedure_schema": 1,
            "name": "legacy",
            "preconditions": ["ready"],
            "capabilities": ["web.fetch"],
            "steps": ["fetch", "verify"],
            "verification": ["receipt"],
            "success_count": 1,
            "failure_count": 1,
            "execution_count": 2,
        },
    })
    selected = service.select_variant(
        "legacy-proc", context={"satisfied_preconditions": ["ready"]}
    )
    assert selected is not None
    assert selected["steps"] == ["fetch", "verify"]
    assert selected["execution_count"] == 2
    assert selected["policy_reason"] == "BASELINE_UNTIL_MATURE"
