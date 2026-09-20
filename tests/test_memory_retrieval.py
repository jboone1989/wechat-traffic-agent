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


def test_access_scope_filters_without_making_every_private_memory_relevant(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(
        store,
        "m:resource",
        "资源压力出现时，先优化现有容量；不足再迁移可重建工作到低信任机器。",
        created_at="2026-09-18T08:00:00Z",
    )
    _add(
        store,
        "m:recent-unrelated",
        "Operator console should show a simple dashboard.",
        created_at="2026-09-20T09:00:00Z",
    )
    router = MemoryRouter(tmp_path, store=store)
    rows = router.recall(
        "If hardware is overloaded and capacity is tight, what comes before moving disposable work to an unreliable machine?",
        limit=1,
        access_scope="PRIVATE_OWNER",
    )
    assert [row["memory_id"] for row in rows] == ["m:resource"]


def test_router_indexes_episode_observation_payload_not_only_generic_summary(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({
        "memory_id": "m:episode-prediction",
        "memory_type": "EPISODIC",
        "kind": "EPISODE",
        "text": "Goal: preserve lived experience in content-prediction | Outcome: 0.875",
        "created_at": "2026-09-19T17:05:58Z",
        "status": "ACTIVE",
        "source_type": "RUNTIME_EXPERIENCE",
        "source_ref": "content-action-outcome:p1:4h",
        "topics": ["virtualbrain-life", "content-prediction"],
        "payload": {
            "goal": "preserve lived experience in content-prediction",
            "lesson": "Content forecasts should be calibrated against mature observed outcomes.",
            "observations": [{
                "actor": "ferro",
                "event_id": "content-action-outcome:p1:4h",
                "text": "Content action prediction for evidence-brief on portfolio-drawdown-clustering at 4h: forecast 3.00 pageviews, observed 4.00; prediction error 0.125.",
            }],
            "attributes": {"source": "content-action-prediction-outcome", "domain": "content-prediction"},
        },
    })
    store.append_memory({
        "memory_id": "m:unrelated",
        "memory_type": "EPISODIC",
        "kind": "EPISODE",
        "text": "Goal: preserve lived experience in content-prediction | Outcome: 0.5",
        "created_at": "2026-09-20T00:00:00Z",
        "status": "ACTIVE",
        "source_type": "RUNTIME_EXPERIENCE",
        "source_ref": "other",
        "topics": ["virtualbrain-life", "content-prediction"],
        "payload": {"observations": [{"text": "A different content experiment about liquidity."}]},
    })
    router = MemoryRouter(tmp_path, store=store)
    rows = router.recall("portfolio-drawdown-clustering 4小时预测结果", limit=1)
    assert [row["memory_id"] for row in rows] == ["m:episode-prediction"]
    assert "portfolio-drawdown-clustering" in rows[0]["payload"]["observations"][0]["text"]


def test_search_index_requests_unbounded_canonical_rows(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(store, "m:needle", "ancient-zircon-needle durable fact", created_at="2020-01-01T00:00:00Z")
    assert [row["memory_id"] for row in store.list_memories(limit=None)] == ["m:needle"]
    original = store.list_memories
    calls = []
    def tracked(*, limit=1000):
        calls.append(limit)
        return original(limit=limit)
    store.list_memories = tracked
    index = MemorySearchIndex(store)
    assert None in calls
    assert index.search("ancient-zircon-needle", limit=3)[0][0] == "m:needle"


def test_router_indexes_procedural_steps_and_recovery_payload(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({
        "memory_id": "m:procedure",
        "memory_type": "PROCEDURAL",
        "kind": "PROCEDURE",
        "text": "Procedure process:content-create-verify-publish",
        "created_at": "2026-09-20T00:00:00Z",
        "status": "ACTIVE",
        "source_type": "RUNTIME_EXPERIENCE",
        "source_ref": "cycle:test",
        "payload": {
            "steps": ["select evidence", "draft content", "run evidence and safety checks", "publish only after checks pass"],
            "recovery": ["repair missing evidence or failed checks before publishing"],
            "verification": ["automated_checks_passed=true", "evidence floor PASS"],
        },
    })
    router = MemoryRouter(tmp_path, store=store)
    rows = router.recall("repair missing evidence after failed checks", limit=1)
    assert [row["memory_id"] for row in rows] == ["m:procedure"]
