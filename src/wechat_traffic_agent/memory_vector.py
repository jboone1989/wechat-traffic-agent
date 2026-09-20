from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Protocol

from .memory_system import CanonicalMemoryStore


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class SemanticHashEmbeddingProvider:
    """Dependency-free semantic feature hash used as the default rebuildable vector adapter.

    The provider is intentionally replaceable. Small concept aliases provide useful associative
    recall before an external embedding model is configured; canonical memory never depends on it.
    """

    model_id = "semantic-hash-v4-payload-procedure"
    dimensions = 192
    _ALIASES = {
        "hardware": "resource", "machine": "resource", "server": "resource", "capacity": "resource",
        "overloaded": "pressure", "shortage": "pressure", "insufficient": "pressure", "full": "pressure",
        "migrate": "offload", "move": "offload", "relocate": "offload",
        "unreliable": "lowtrust", "untrusted": "lowtrust", "disposable": "replaceable",
        "记不起来": "recall", "想起来": "recall", "回忆": "recall",
        "机器": "resource", "服务器": "resource", "资源": "resource", "磁盘": "resource",
        "不足": "pressure", "撑不住": "pressure", "压力": "pressure", "紧张": "pressure",
        "迁移": "offload", "搬家": "offload", "拆分": "offload",
        "不可靠": "lowtrust", "低信任": "lowtrust",
        "内容": "content", "流程": "process", "检查": "check", "通过": "pass",
        "预测": "prediction", "结果": "outcome", "浏览量": "pageview", "误差": "error",
        "checks": "check", "passed": "pass", "forecast": "prediction", "observed": "outcome",
        "pageviews": "pageview",
    }

    def embed(self, text: str) -> list[float]:
        tokens = self._features(text)
        vector = [0.0] * self.dimensions
        for feature, weight in tokens:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def _features(self, text: str) -> list[tuple[str, float]]:
        value = str(text or "").casefold()
        words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", value)
        features: list[tuple[str, float]] = []
        for word in words:
            canonical = self._ALIASES.get(word, word)
            features.append(("token:" + canonical, 1.0))
            if len(word) >= 2 and re.search(r"[\u3400-\u9fff]", word):
                for i in range(len(word) - 1):
                    gram = word[i:i + 2]
                    features.append(("gram:" + self._ALIASES.get(gram, gram), 0.45))
        for phrase, canonical in self._ALIASES.items():
            if phrase in value:
                features.append(("concept:" + canonical, 1.5))
        return features


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


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class SemanticVectorIndex:
    """Derived vector index; safe to delete and fully rebuild from canonical memory."""

    def __init__(
        self,
        store: CanonicalMemoryStore,
        *,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self.store = store
        self.provider = provider or SemanticHashEmbeddingProvider()
        self._ensure()
        self.rebuild_if_stale()

    def _ensure(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_vector_index (
                    memory_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    vector_json TEXT NOT NULL
                )
                """
            )

    def rebuild_if_stale(self) -> None:
        rows = self.store.list_memories(limit=None)
        canonical = {str(row["memory_id"]): str(row["content_hash"]) for row in rows}
        with self.store._connect() as connection:
            indexed = {
                str(row["memory_id"]): (str(row["content_hash"]), str(row["model_id"]))
                for row in connection.execute(
                    "SELECT memory_id, content_hash, model_id FROM memory_vector_index"
                )
            }
        if len(canonical) != len(indexed) or any(
            memory_id not in indexed
            or indexed[memory_id][0] != content_hash
            or indexed[memory_id][1] != self.provider.model_id
            for memory_id, content_hash in canonical.items()
        ):
            self.rebuild()

    def rebuild(self) -> int:
        rows = self.store.list_memories(limit=None)
        with self.store._connect() as connection:
            connection.execute("DELETE FROM memory_vector_index")
            for row in rows:
                vector = self.provider.embed(self._document(row))
                connection.execute(
                    "INSERT INTO memory_vector_index(memory_id, content_hash, model_id, vector_json) VALUES(?,?,?,?)",
                    (
                        row["memory_id"], row["content_hash"], self.provider.model_id,
                        json.dumps(vector, separators=(",", ":")),
                    ),
                )
        return len(rows)

    def search(self, query: str, *, limit: int = 50) -> list[tuple[str, float]]:
        self.rebuild_if_stale()
        query_vector = self.provider.embed(query)
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT memory_id, vector_json FROM memory_vector_index WHERE model_id=?",
                (self.provider.model_id,),
            ).fetchall()
        scored = [
            (str(row["memory_id"]), cosine(query_vector, json.loads(str(row["vector_json"]))))
            for row in rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: max(1, min(int(limit), 200))]

    @staticmethod
    def _document(row: dict) -> str:
        return memory_document(row)


__all__ = [
    "EmbeddingProvider", "SemanticHashEmbeddingProvider", "SemanticVectorIndex", "cosine", "memory_document",
]
