from __future__ import annotations

from agent_runtime.memory.retrieval import memory_document

from .memory_system import CanonicalMemoryStore


class SemanticVectorIndex:
    """Legacy vector facade over the shared Runtime retrieval view.

    Embedding and ANN remain capability implementation details in Ferro during
    this migration slice. Canonical projection/retrieval semantics now live in
    Agent Runtime.
    """

    def __init__(self, store: CanonicalMemoryStore, *, models=None):
        from .memory_service import MemoryService

        self.store = store
        self.service = MemoryService(
            store.root.parent.parent,
            models=models,
        )
        self.rebuild_if_stale()

    def rebuild_if_stale(self):
        self.service.sync()
        self.service.prepare_vectors()

    def rebuild(self):
        self.service.rebuild_views()
        self.service.prepare_vectors()
        return len(self.store.list_memories(limit=None))

    def search(self, query, *, limit=50):
        return self.service.dense_search(
            query,
            filter_sql="n.status='ACTIVE' AND n.node_type='memory'",
            limit=limit,
        )

    def rerank(self, query, texts):
        return self.service._cached_model("rerank", query, texts)


__all__ = ["SemanticVectorIndex", "memory_document"]
