from __future__ import annotations

from wechat_traffic_agent.memory_retrieval import MemoryRouter
from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.memory_vector import SemanticVectorIndex


def _add(store, memory_id, text, *, importance=0.7):
    store.append_memory({
        "memory_id": memory_id,
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": text,
        "importance": importance,
        "created_at": "2026-09-19T00:00:00Z",
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:" + memory_id,
    })


def test_semantic_vector_improves_paraphrase_recall(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(store, "m:resource", "When resource pressure rises, offload replaceable workloads first.")
    _add(store, "m:social", "Write concise social posts with verified evidence.")
    router = MemoryRouter(tmp_path, store=store)
    rows = router.recall("hardware overloaded machine capacity", limit=1)
    assert rows[0]["memory_id"] == "m:resource"


def test_vector_index_is_derived_and_rebuildable(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(store, "m:one", "Low trust machines should carry replaceable work.")
    index = SemanticVectorIndex(store)
    assert index.search("unreliable disposable server")
    with store._connect() as connection:
        connection.execute("UPDATE memory_nodes SET vector=NULL, model_id=''")
    assert store.get_memory("m:one") is not None
    assert index.rebuild() == 1
    assert index.search("unreliable disposable server")[0][0] == "m:one"


def test_vector_index_tracks_canonical_revision_hash(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _add(store, "m:state", "TinyFish browser state")
    index = SemanticVectorIndex(store)
    before = index.search("browser")
    assert before
    store.transition_memory("m:state", status="ARCHIVED")
    index.rebuild_if_stale()
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status FROM memory_nodes WHERE node_id='m:state'"
        ).fetchone()
    assert row["status"] == store.get_memory("m:state")["status"]
