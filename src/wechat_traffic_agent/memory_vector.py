from __future__ import annotations

from .memory_system import CanonicalMemoryStore


def memory_document(row: dict) -> str:
    """Build bounded derived recall text from a canonical memory row.

    Canonical payload remains authoritative. This only exposes semantically useful payload fields
    to rebuildable lexical/vector indexes so episodic observations are actually recallable.
    """
    parts = [
        str(row.get("text") or ""),
        str(row.get("kind") or ""),
        " ".join(str(item) for item in row.get("topics") or []),
        " ".join(str(item) for item in row.get("entities") or []),
    ]
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for key in ("goal", "decision", "prediction", "outcome", "lesson"):
        value = payload.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    for key in (
        "observations", "actions", "tool_outcomes", "preconditions", "steps", "recovery",
        "verification", "failure_modes", "capabilities",
    ):
        values = payload.get(key) if isinstance(payload.get(key), list) else []
        for value in values[:64]:
            if isinstance(value, dict):
                for field in ("text", "summary", "action", "result", "status"):
                    if value.get(field) not in (None, ""):
                        parts.append(str(value[field]))
            elif value not in (None, ""):
                parts.append(str(value))
    attributes = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {}
    for key in ("experience_kind", "source", "domain", "topic", "provenance_kind"):
        if attributes.get(key) not in (None, ""):
            parts.append(str(attributes[key]))
    return " ".join(parts)[:12000]


class SemanticVectorIndex:
    """Canonical vector facade over the shared trained memory service."""

    def __init__(self, store: CanonicalMemoryStore, *, models=None):
        from .memory_service import MemoryService
        self.store = store
        self.service = MemoryService(store.root.parent.parent, models=models)
        self.rebuild_if_stale()

    def rebuild_if_stale(self):
        self.service.sync()
        self.service.prepare_vectors()

    def rebuild(self):
        self.service.rebuild_views()
        self.service.prepare_vectors()
        return len(self.store.list_memories(limit=None))

    def search(self, query, *, limit=50):
        return self.service.dense_search(query,
            filter_sql="n.status='ACTIVE' AND n.node_type='memory'", limit=limit)

    def rerank(self, query, texts):
        return self.service._cached_model('rerank', query, texts)


__all__ = ['SemanticVectorIndex', 'memory_document']
