from __future__ import annotations

from datetime import datetime, timezone

from wechat_traffic_agent.memory_retrieval import MemoryRouter, MemorySearchIndex
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


def _add(store, memory_id, text, *, created_at, status="ACTIVE", entities=(), topics=()):
    store.append_memory({
        "memory_id": memory_id,
        "memory_type": "SEMANTIC",
        "kind": "OWNER_PREFERENCE",
        "text": text,
        "created_at": created_at,
        "status": status,
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:" + memory_id,
        "entities": list(entities),
        "topics": list(topics),
    })


def test_router_uses_temporal_window_for_yesterday(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(store, "m:old", "Old deployment note", created_at="2026-09-17T12:00:00Z")
    _add(store, "m:yesterday", "Yesterday deployment note", created_at="2026-09-18T12:00:00Z")
    router = MemoryRouter(tmp_path, store=store)
    rows = router.recall(
        "昨天我说过什么 deployment",
        now=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
        source_type="OWNER_CONVERSATION",
    )
    assert [row["memory_id"] for row in rows] == ["m:yesterday"]


def test_router_distinguishes_current_from_historical_status(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(store, "m:past", "Use TinyFish as the primary browser", created_at="2026-09-01T00:00:00Z", status="SUPERSEDED")
    _add(store, "m:now", "Use JEV as the primary browser", created_at="2026-09-19T00:00:00Z")
    router = MemoryRouter(tmp_path, store=store)

    current = router.recall("现在主要 browser", source_type="OWNER_CONVERSATION")
    assert all(row["memory_id"] != "m:past" for row in current)

    historical = router.recall("以前 browser 是什么", source_type="OWNER_CONVERSATION")
    assert {row["memory_id"] for row in historical} >= {"m:past"}


def test_router_structured_entity_and_topic_filters(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(
        store, "m:ferro", "Ferro memory architecture",
        created_at="2026-09-19T00:00:00Z", entities=["Ferro"], topics=["memory"],
    )
    _add(
        store, "m:trading", "Trading research architecture",
        created_at="2026-09-19T00:01:00Z", entities=["Trading"], topics=["research"],
    )
    router = MemoryRouter(tmp_path, store=store)
    rows = router.recall("architecture", entity="Ferro", topic="memory")
    assert [row["memory_id"] for row in rows] == ["m:ferro"]


def test_fts_index_is_rebuildable_and_finds_chinese_bigrams(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(
        store, "m:resource", "服务器磁盘不足时优先优化和拆分服务",
        created_at="2026-09-19T00:00:00Z",
    )
    index = MemorySearchIndex(store)
    assert index.search("磁盘不足")
    with store._connect() as connection:
        connection.execute("DELETE FROM memory_fts")
    assert index.rebuild() == 1
    assert index.search("磁盘不足")[0][0] == "m:resource"
