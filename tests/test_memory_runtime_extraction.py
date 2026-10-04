from __future__ import annotations

from agent_runtime.memory.durable import CanonicalMemoryStore as RuntimeCanonicalMemoryStore
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


def test_ferro_memory_store_is_only_a_runtime_compatibility_subclass(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    assert isinstance(store, RuntimeCanonicalMemoryStore)
    assert store.db_path == tmp_path / "application" / "memory_system" / "memory.db"


def test_direct_legacy_store_transition_still_invalidates_existing_retrieval_views(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({
        "memory_id": "fact",
        "kind": "FACT",
        "text": "A canonical fact.",
    })
    with store._connect() as db:
        db.executescript(
            """
            CREATE TABLE memory_nodes (
                node_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_seq INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE memory_dependencies (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                PRIMARY KEY(parent_id, child_id)
            );
            CREATE TABLE memory_node_changes (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO memory_nodes(node_id,version,status) VALUES('fact','v1','ACTIVE')"
        )
        db.execute(
            "INSERT INTO memory_nodes(node_id,version,status) VALUES('summary','v2','ACTIVE')"
        )
        db.execute(
            "INSERT INTO memory_dependencies(parent_id,child_id) VALUES('summary','fact')"
        )

    store.transition_memory("fact", status="RETRACTED")

    with store._connect() as db:
        fact = db.execute(
            "SELECT status FROM memory_nodes WHERE node_id='fact'"
        ).fetchone()
        summary = db.execute(
            "SELECT status FROM memory_nodes WHERE node_id='summary'"
        ).fetchone()
        event = db.execute(
            "SELECT status FROM memory_node_changes WHERE node_id='summary'"
        ).fetchone()
    assert fact["status"] == "RETRACTED"
    assert summary["status"] == "DIRTY"
    assert event["status"] == "DIRTY"
