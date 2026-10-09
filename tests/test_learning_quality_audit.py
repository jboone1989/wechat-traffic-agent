from pathlib import Path
import runpy

from wechat_traffic_agent.learning_memory_bridge import LearningMemoryBridge
from wechat_traffic_agent.memory_service import MemoryService
from test_learning_memory_bridge import application_receipt, research_receipt, source


def test_learning_audit_measures_verified_progress_not_memory_volume(tmp_path):
    audit = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "scripts/audit_ferro_learning.py"
    ))["audit"]
    service = MemoryService(tmp_path)
    bridge = LearningMemoryBridge(service)
    src = source()
    service.ingest_source(src, topic="recovery")
    learning = {**research_receipt(src), "progress": "LEARNED",
                "answer": "The worker restores its persisted checkpoint on restart.",
                "question_status": "ANSWERED"}
    bridge.ingest_research(learning)
    repeat = {**learning, "cycle": "repeated", "progress": "REPEATED"}
    bridge.ingest_research(repeat)
    unresolved = application_receipt(cycle="unknown-case")
    unresolved["assessment"]["status"] = "UNRESOLVED"
    unresolved["evidence"] = []
    bridge.ingest_application(unresolved)
    result = audit(tmp_path)
    assert result["memories"]["RESEARCH_OBSERVATION"]["total"] == 1
    assert result["research_question_status"] == {"ANSWERED": 1}
    assert result["application_validation"] == {}
    assert not result["causal_improvement_proven"]
    settled = application_receipt()
    bridge.ingest_application(settled)
    verified = audit(tmp_path)
    assert verified["application_validation"] == {"CONFIRMED": 1}
    assert verified["memories"]["LEARNING_APPLICATION_OUTCOME"]["with_evidence"] == 1


def test_historical_unresolved_outcomes_are_archived_not_deleted(tmp_path):
    archive = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "scripts/archive_unresolved_learning.py"
    ))["archive"]
    service = MemoryService(tmp_path)
    unresolved = {**application_receipt(cycle="legacy-untested"), "assessment": {
        "status": "UNRESOLVED", "rationale": "No new evidence", "correction": "",
    }, "evidence": []}
    store = service.store
    store.append_memory({
        "memory_id": "learning-application:legacy-untested",
        "memory_type": "EPISODIC", "kind": "LEARNING_APPLICATION_OUTCOME",
        "text": "No new evidence, no validated outcome",
        "source_type": "APPLICATION_RECEIPT", "source_ref": "legacy-untested",
        "payload": unresolved,
    })
    bridge = LearningMemoryBridge(service)
    bridge.ingest_application(application_receipt())
    preview = archive(tmp_path)
    assert preview["unresolved_archived"] == 1
    assert store.get_memory("learning-application:legacy-untested")["status"] == "ACTIVE"
    actual = archive(tmp_path, apply=True)
    assert actual["unresolved_archived"] == 1
    assert store.get_memory("learning-application:legacy-untested")["status"] == "ARCHIVED"
    assert store.get_memory("learning-application:cycle-application")["status"] == "ACTIVE"
    assert archive(tmp_path, apply=True)["unresolved_archived"] == 0


def test_action_transfer_metrics_measure_verifiable_trials_only(tmp_path):
    from wechat_traffic_agent.learning_transfer import LearningTransfer
    audit = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "scripts/audit_ferro_learning.py"
    ))["audit"]
    # Seed only the canonical memory schema; action transfer lives in its
    # own SQLite ledger and is not confused with accumulated memory count.
    MemoryService(tmp_path)
    ledger = LearningTransfer(tmp_path)
    candidate = {
        "procedure_id": "process:publish",
        "variant_id": "retry",
        "steps": ["retry checker once"],
        "policy_action": "RETRY_ONCE_SAFE",
    }
    ledger.assign(cycle="trial-1", topic="ai", candidate=candidate)
    ledger.settle(cycle="trial-1", outcome="DRAFT_CREATED", evidence_refs=["draft:1"])
    first = audit(tmp_path)["action_transfer"]
    assert first["measured"] == 0 and first["mature_external"] == 0
    assert sum(v["assigned"] for v in first["arms"].values()) == 1
