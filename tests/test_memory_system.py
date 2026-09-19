from __future__ import annotations

import json
import sqlite3

import pytest

from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.operator_memory import OperatorLongTermMemory


def test_canonical_store_is_durable_and_indexes_metadata(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    created = store.append_memory({
        "memory_id": "memory:test:1",
        "memory_type": "SEMANTIC",
        "kind": "OWNER_PREFERENCE",
        "text": "Owner prefers bounded direct execution.",
        "created_at": "2026-09-19T10:00:00Z",
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:1",
        "entities": ["owner", "ferro"],
        "topics": ["execution"],
    })
    assert created is True

    reloaded = CanonicalMemoryStore(tmp_path)
    rows = reloaded.list_memories()
    assert [row["memory_id"] for row in rows] == ["memory:test:1"]
    assert rows[0]["entities"] == ["ferro", "owner"]
    assert rows[0]["topics"] == ["execution"]
    assert reloaded.events()[0]["event_type"] == "MEMORY_CREATED"


def test_duplicate_id_is_idempotent_but_conflict_is_rejected(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    row = {
        "memory_id": "memory:test:stable",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Stable fact",
        "created_at": "2026-09-19T10:00:00Z",
    }
    assert store.append_memory(row) is True
    assert store.append_memory(row) is False
    with pytest.raises(ValueError, match="different content"):
        store.append_memory({**row, "text": "Changed fact"})


def test_memory_event_ledger_rejects_update_and_delete(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({
        "memory_id": "memory:test:event",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Append only",
        "created_at": "2026-09-19T10:00:00Z",
    })
    connection = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("UPDATE memory_events SET event_type='CHANGED'")
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM memory_events")
    connection.close()


def test_legacy_owner_memory_import_is_idempotent_and_mirrored(tmp_path):
    legacy = tmp_path / "application" / "operator_chat" / "long_term_memory.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "memory_id": "owner-memory:legacy",
        "kind": "OWNER_PREFERENCE",
        "text": "Owner prefers explicit provenance.",
        "importance": 0.9,
        "source_message_id": "operator-chat:legacy",
        "created_at": "2026-09-18T00:00:00Z",
    }) + "\n", encoding="utf-8")

    first = OperatorLongTermMemory(tmp_path)
    assert any(row["memory_id"] == "owner-memory:legacy" for row in first.all())
    event_count = len(first.store.events())

    second = OperatorLongTermMemory(tmp_path)
    assert len(second.store.events()) == event_count
    lines = [json.loads(line) for line in legacy.read_text(encoding="utf-8").splitlines()]
    assert [row["memory_id"] for row in lines].count("owner-memory:legacy") == 1


def test_new_owner_memory_is_canonical_and_jsonl_remains_compatibility_mirror(tmp_path):
    memory = OperatorLongTermMemory(tmp_path)
    saved = memory.remember_many([{
        "kind": "OWNER_PREFERENCE",
        "text": "Owner wants the full memory roadmap followed in order.",
        "importance": 1.0,
    }], source_message_id="operator-chat:new")
    assert len(saved) == 1
    canonical = memory.store.list_memories()
    assert canonical[0]["memory_id"] == saved[0]["memory_id"]
    assert canonical[0]["source_type"] == "OWNER_CONVERSATION"
    legacy_lines = memory.path.read_text(encoding="utf-8").splitlines()
    assert any(saved[0]["memory_id"] in line for line in legacy_lines)
