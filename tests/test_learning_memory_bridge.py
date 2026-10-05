from __future__ import annotations

import json
from pathlib import Path

from wechat_traffic_agent.learning_memory_bridge import LearningMemoryBridge
from wechat_traffic_agent.memory_service import MemoryService


def source(text="The worker uses a checkpoint to restore state after restart."):
    return {
        "evidence_id": "source-1",
        "title": "Checkpoint source",
        "summary": text,
        "evidence_text": text,
        "url": "https://example.com/checkpoint",
    }


def research_receipt(raw=None, *, cycle="cycle-1"):
    raw = raw or source()
    return {
        "cycle": cycle,
        "topic": "recovery",
        "observed_at": "2026-10-04T00:00:00+00:00",
        "facts": [
            {
                "evidence_id": raw["evidence_id"],
                "quote": raw["summary"],
                "observation": "The worker uses a checkpoint to restore its state.",
            }
        ],
        "sources": [raw],
        "source_urls": [raw["url"]],
        "next_question": "What fails after restart?",
    }


def application_receipt(*, cycle="cycle-application"):
    return {
        "cycle": cycle,
        "topic": "recovery",
        "checked_at": "2026-10-04T01:00:00+00:00",
        "application": {
            "prediction": "The worker will restore state after restart.",
            "confidence": 0.8,
        },
        "assessment": {
            "status": "CONFIRMED",
            "rationale": "The new source shows the worker restoring state.",
            "correction": "",
        },
        "evidence": [
            {
                "evidence_id": "source-1",
                "quote": "The worker restored state.",
            }
        ],
    }


def test_research_receipt_projection_is_outside_memory_service_logic(tmp_path):
    service = MemoryService(tmp_path)
    bridge = LearningMemoryBridge(service)
    raw = source()
    service.ingest_source(raw, topic="recovery")

    bridge.ingest_research(research_receipt(raw))

    rows = service.store.query_memories(
        memory_types=["SEMANTIC"],
        source_type="RESEARCH_RECEIPT",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "RESEARCH_OBSERVATION"
    assert row["status"] == "ACTIVE"
    assert row["evidence_refs"] == ["source-1"]
    assert row["payload"]["cycle"] == "cycle-1"
    assert row["payload"]["derived_from"] == ["source:source-1"]


def test_old_research_receipt_is_disputed_against_newer_source_revision(tmp_path):
    service = MemoryService(tmp_path)
    bridge = LearningMemoryBridge(service)
    old = source()
    newer = source(
        "The worker no longer restores state from a checkpoint after restart."
    )
    service.ingest_source(
        newer,
        topic="recovery",
        observed_at="2026-10-04T02:00:00+00:00",
    )

    bridge.ingest_research(research_receipt(old))

    row = service.store.query_memories(
        memory_types=["SEMANTIC"],
        source_type="RESEARCH_RECEIPT",
    )[0]
    assert row["status"] == "DISPUTED"


def test_application_receipt_projects_to_episodic_memory(tmp_path):
    service = MemoryService(tmp_path)
    bridge = LearningMemoryBridge(service)

    bridge.ingest_application(application_receipt())

    rows = service.store.query_memories(
        memory_types=["EPISODIC"],
        source_type="APPLICATION_RECEIPT",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "LEARNING_APPLICATION_OUTCOME"
    assert row["source_ref"] == "cycle-application"
    assert row["evidence_refs"] == ["source-1"]
    assert row["payload"]["derived_from"] == ["source:source-1"]


def test_receipt_migration_is_incremental_and_idempotent(tmp_path):
    service = MemoryService(tmp_path)
    bridge = LearningMemoryBridge(service)
    cycle = tmp_path / "content" / "continuous" / "cycles" / "cycle-1"
    cycle.mkdir(parents=True)

    raw = source()
    (cycle / "learning_result.json").write_text(
        json.dumps(research_receipt(raw)),
        encoding="utf-8",
    )
    (cycle / "learning_application_result.json").write_text(
        json.dumps(application_receipt(cycle="cycle-1")),
        encoding="utf-8",
    )

    bridge.migrate_receipts(tmp_path)
    first_memories = service.store.list_memories(limit=None)
    with service.store._connect() as db:
        first_imports = json.loads(
            db.execute(
                "SELECT value FROM memory_service_meta "
                "WHERE key='receipt_imports'"
            ).fetchone()[0]
        )

    bridge.migrate_receipts(tmp_path)
    second_memories = service.store.list_memories(limit=None)
    with service.store._connect() as db:
        second_imports = json.loads(
            db.execute(
                "SELECT value FROM memory_service_meta "
                "WHERE key='receipt_imports'"
            ).fetchone()[0]
        )

    assert len(first_memories) == 2
    assert len(second_memories) == 2
    assert first_imports == second_imports
    assert len(first_imports) == 2


def test_memory_service_compatibility_methods_delegate_to_bridge(tmp_path):
    service = MemoryService(tmp_path)
    raw = source()
    service.ingest_source(raw, topic="recovery")

    service.ingest_research(research_receipt(raw))
    service.ingest_application(application_receipt())

    assert len(service.store.list_memories(limit=None)) == 2


def test_production_learning_modules_use_bridge_directly():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    research_source = (root / "learning_research.py").read_text(encoding="utf-8")
    application_source = (root / "learning_application.py").read_text(encoding="utf-8")

    assert "LearningMemoryBridge" in research_source
    assert "LearningMemoryBridge" in application_source
    assert "service.ingest_research(" not in research_source
    assert "service.migrate_receipts(" not in research_source
    assert "MemoryService(state_root).ingest_application" not in application_source


def test_source_grounded_explanation_is_retrievable_after_restart(tmp_path):
    service = MemoryService(tmp_path)
    raw = source()
    service.ingest_source(raw, topic="recovery")
    row = {**research_receipt(raw),
           "answer": "A worker can reload the persisted checkpoint after restart, restoring its earlier state instead of starting from an empty state.",
           "question_status": "PARTIAL",
           "focus": {"question": "How does checkpoint recovery work?"}}
    LearningMemoryBridge(service).ingest_research(row)
    restarted = MemoryService(tmp_path)
    saved = restarted.store.get_memory("research-understanding:cycle-1")
    assert saved["text"] == row["answer"]
    assert saved["payload"]["question_status"] == "PARTIAL"
    assert saved["payload"]["epistemic_status"] == "SOURCE_GROUNDED_SYNTHESIS"
    assert saved["payload"]["derived_from"] == ["source:source-1"]
    cards = restarted.search("checkpoint recovery restoring earlier state", node_types=("memory",), limit=8)
    assert any(card["node_id"] == "research-understanding:cycle-1" for card in cards)
    LearningMemoryBridge(restarted).ingest_research(row)
    assert len([m for m in restarted.store.list_memories(limit=None)
                if m["kind"] == "RESEARCH_UNDERSTANDING"]) == 1


def test_explanation_from_superseded_source_remains_disputed(tmp_path):
    service = MemoryService(tmp_path)
    newer = source("The worker no longer restores state from a checkpoint after restart.")
    service.ingest_source(newer, topic="recovery")
    row = {**research_receipt(source()), "answer": "The old implementation restores state from its persisted checkpoint.", "question_status": "ANSWERED"}
    LearningMemoryBridge(service).ingest_research(row)
    assert service.store.get_memory("research-understanding:cycle-1")["status"] == "DISPUTED"
