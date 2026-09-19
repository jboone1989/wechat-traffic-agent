from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from .memory_consolidation import recall_weight
from .memory_system import CanonicalMemoryStore
from .memory_vector import EmbeddingProvider, SemanticVectorIndex


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


def _terms(text: str) -> list[str]:
    value = _normalize(text)
    ascii_terms = re.findall(r"[a-z0-9_]{2,}", value)
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", value))
    cjk_terms = [cjk[i:i + 2] for i in range(max(0, len(cjk) - 1))]
    if len(cjk) == 1:
        cjk_terms.append(cjk)
    return list(dict.fromkeys(ascii_terms + cjk_terms))


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RecallPlan:
    query: str
    historical: bool
    created_from: str | None
    created_to: str | None
    memory_types: tuple[str, ...]
    entity: str | None
    topic: str | None


@dataclass(frozen=True)
class RecallWeights:
    rrf_k: float = 60.0
    lexical_weight: float = 1.0
    vector_weight: float = 1.0
    structured_weight: float = 1.0
    importance_weight: float = 0.01
    confidence_weight: float = 0.0025
    temporal_boost: float = 0.01
    search_multiplier: int = 8
    decay_enabled: bool = True

    def __post_init__(self) -> None:
        numeric = {
            "rrf_k": self.rrf_k,
            "lexical_weight": self.lexical_weight,
            "vector_weight": self.vector_weight,
            "structured_weight": self.structured_weight,
            "importance_weight": self.importance_weight,
            "confidence_weight": self.confidence_weight,
            "temporal_boost": self.temporal_boost,
        }
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if any(float(value) < 0 for value in numeric.values()):
            raise ValueError("recall weights must be non-negative")
        if not 1 <= int(self.search_multiplier) <= 64:
            raise ValueError("search_multiplier must be between 1 and 64")

    def as_dict(self) -> dict[str, Any]:
        return {
            "rrf_k": float(self.rrf_k),
            "lexical_weight": float(self.lexical_weight),
            "vector_weight": float(self.vector_weight),
            "structured_weight": float(self.structured_weight),
            "importance_weight": float(self.importance_weight),
            "confidence_weight": float(self.confidence_weight),
            "temporal_boost": float(self.temporal_boost),
            "search_multiplier": int(self.search_multiplier),
            "decay_enabled": bool(self.decay_enabled),
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RecallWeights":
        allowed = set(cls().__dict__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("unknown recall policy fields: " + ",".join(sorted(unknown)))
        return cls(**value)


class MemorySearchIndex:
    """Rebuildable FTS5 index over canonical memory records."""

    def __init__(self, store: CanonicalMemoryStore) -> None:
        self.store = store
        self._ensure()

    def _ensure(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    memory_id UNINDEXED,
                    lexical_text,
                    kind,
                    topics,
                    entities,
                    tokenize='unicode61'
                )
                """
            )
        self.rebuild_if_stale()

    def rebuild_if_stale(self) -> None:
        with self.store._connect() as connection:
            memory_count = int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            index_count = int(connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0])
        if memory_count != index_count:
            self.rebuild()

    def rebuild(self) -> int:
        rows = self.store.list_memories(limit=5000)
        with self.store._connect() as connection:
            connection.execute("DELETE FROM memory_fts")
            for row in rows:
                terms = _terms(str(row.get("text") or ""))
                lexical = " ".join(terms + [str(row.get("text") or "")])
                connection.execute(
                    "INSERT INTO memory_fts(memory_id, lexical_text, kind, topics, entities) VALUES(?,?,?,?,?)",
                    (
                        row["memory_id"], lexical, str(row.get("kind") or ""),
                        " ".join(row.get("topics") or []), " ".join(row.get("entities") or []),
                    ),
                )
        return len(rows)

    def search(self, query: str, *, limit: int = 50) -> list[tuple[str, float]]:
        self.rebuild_if_stale()
        terms = _terms(query)
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:24])
        try:
            with self.store._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT memory_id, bm25(memory_fts, 1.0, 0.4, 0.4, 0.4) AS rank
                    FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?
                    """,
                    (match, max(1, min(int(limit), 200))),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(str(row["memory_id"]), float(row["rank"])) for row in rows]


class MemoryRouter:
    """Single M3 recall entry point for canonical owner/agent memories."""

    def __init__(
        self,
        state_root,
        *,
        store: CanonicalMemoryStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        weights: RecallWeights | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.store = store or CanonicalMemoryStore(self.state_root)
        self.weights = weights or self._load_active_weights()
        self.fts = MemorySearchIndex(self.store)
        self.vector = SemanticVectorIndex(self.store, provider=embedding_provider)

    def _load_active_weights(self) -> RecallWeights:
        path = self.state_root / "application" / "memory_system" / "active_recall_policy.json"
        if not path.exists():
            return RecallWeights()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            value = raw.get("weights") if isinstance(raw, dict) else None
            return RecallWeights.from_mapping(dict(value)) if isinstance(value, dict) else RecallWeights()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return RecallWeights()

    def plan(
        self,
        query: str,
        *,
        now: datetime | None = None,
        memory_types: Iterable[str] = (),
        entity: str | None = None,
        topic: str | None = None,
    ) -> RecallPlan:
        value = _normalize(query)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        historical = bool(re.search(
            r"以前|过去|曾经|当时|之前|历史|used to|previously|earlier|historical",
            value,
            re.IGNORECASE,
        ))
        created_from, created_to = self._time_window(value, current)
        return RecallPlan(
            query=str(query), historical=historical,
            created_from=created_from, created_to=created_to,
            memory_types=tuple(str(item).upper() for item in memory_types if str(item).strip()),
            entity=str(entity) if entity else None,
            topic=str(topic) if topic else None,
        )

    def recall(
        self,
        query: str,
        *,
        limit: int = 12,
        now: datetime | None = None,
        memory_types: Iterable[str] = (),
        source_type: str | None = None,
        entity: str | None = None,
        topic: str | None = None,
    ) -> list[dict[str, Any]]:
        plan = self.plan(
            query, now=now, memory_types=memory_types, entity=entity, topic=topic,
        )
        statuses = None if plan.historical else ["ACTIVE"]
        structured = self.store.query_memories(
            memory_types=list(plan.memory_types) or None,
            statuses=statuses,
            source_type=source_type,
            created_from=plan.created_from,
            created_to=plan.created_to,
            entity=plan.entity,
            topic=plan.topic,
            limit=max(50, int(limit) * int(self.weights.search_multiplier)),
        )
        allowed = {str(row["memory_id"]): row for row in structured}
        search_limit = max(50, int(limit) * int(self.weights.search_multiplier))
        lexical = self.fts.search(query, limit=search_limit)
        vector = self.vector.search(query, limit=search_limit)
        lexical_rank = {memory_id: index + 1 for index, (memory_id, _) in enumerate(lexical)}
        vector_rank = {memory_id: index + 1 for index, (memory_id, _) in enumerate(vector)}
        constrained = bool(
            plan.created_from or plan.created_to or plan.entity or plan.topic
            or plan.memory_types or source_type
        )
        if constrained:
            candidate_ids = set(allowed)
        else:
            candidate_ids = set(allowed) | set(lexical_rank) | set(vector_rank)
        candidates: list[tuple[float, dict[str, Any]]] = []
        for memory_id in candidate_ids:
            row = allowed.get(memory_id) or self.store.get_memory(memory_id)
            if row is None:
                continue
            if not plan.historical and str(row.get("status") or "ACTIVE") != "ACTIVE":
                continue
            if constrained and memory_id not in allowed:
                continue
            score = 0.0
            if memory_id in lexical_rank:
                score += self.weights.lexical_weight / (
                    self.weights.rrf_k + lexical_rank[memory_id]
                )
            if memory_id in vector_rank:
                score += self.weights.vector_weight / (
                    self.weights.rrf_k + vector_rank[memory_id]
                )
            if memory_id in allowed:
                score += self.weights.structured_weight / self.weights.rrf_k
            score += float(row.get("importance") or 0.0) * self.weights.importance_weight
            score += float(row.get("confidence") or 0.0) * self.weights.confidence_weight
            if plan.created_from or plan.created_to:
                score += self.weights.temporal_boost
            if not plan.historical and self.weights.decay_enabled:
                score *= recall_weight(row, now=now)
            candidates.append((score, row))
        candidates.sort(
            key=lambda item: (item[0], str(item[1].get("created_at") or "")),
            reverse=True,
        )
        return [row for _, row in candidates[: max(1, int(limit))]]

    @staticmethod
    def _time_window(value: str, now: datetime) -> tuple[str | None, str | None]:
        if "昨天" in value or "yesterday" in value:
            day = (now - timedelta(days=1)).date()
            start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            return _utc_iso(start), _utc_iso(start + timedelta(days=1))
        if "今天" in value or "today" in value:
            day = now.date()
            start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            return _utc_iso(start), _utc_iso(start + timedelta(days=1))
        match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            start = datetime(year, month, day, tzinfo=timezone.utc)
            return _utc_iso(start), _utc_iso(start + timedelta(days=1))
        match = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", value)
        if match:
            month, day = (int(part) for part in match.groups())
            start = datetime(now.year, month, day, tzinfo=timezone.utc)
            return _utc_iso(start), _utc_iso(start + timedelta(days=1))
        return None, None


__all__ = ["MemoryRouter", "MemorySearchIndex", "RecallPlan", "RecallWeights"]
