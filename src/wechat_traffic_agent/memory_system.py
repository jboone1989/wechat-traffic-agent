from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4


MEMORY_SCHEMA_VERSION = 1
MEMORY_TYPES = frozenset({
    "CORE", "TRAJECTORY", "EPISODIC", "SEMANTIC",
    "RELATIONAL", "PROCEDURAL", "PROSPECTIVE",
})
MEMORY_STATUSES = frozenset({
    "ACTIVE", "SUPERSEDED", "DISPUTED", "RETRACTED", "ARCHIVED",
})


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def memory_type_for_kind(kind: str) -> str:
    normalized = str(kind or "").strip().upper()
    if normalized in {"CORE_IDENTITY", "CORE_PRINCIPLE"}:
        return "CORE"
    if normalized.startswith("PROCEDUR"):
        return "PROCEDURAL"
    if normalized.startswith("EPISOD"):
        return "EPISODIC"
    if normalized.startswith("RELATION"):
        return "RELATIONAL"
    if normalized.startswith("PROSPECT"):
        return "PROSPECTIVE"
    return "SEMANTIC"


class CanonicalMemoryStore:
    """Canonical durable-memory substrate for M1.

    SQLite owns durable memory records and the append-only event ledger.
    JSONL files are compatibility/audit mirrors and can be reconstructed.
    Retrieval semantics intentionally remain outside this class until M3.
    """

    def __init__(self, state_root: Path) -> None:
        self.root = Path(state_root) / "application" / "memory_system"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "memory.db"
        self.event_mirror_path = self.root / "memory_events.jsonl"
        self._ensure_schema()
        self.repair_event_mirror()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    observed_at TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    status TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    supersedes TEXT,
                    superseded_by TEXT,
                    access_scope TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_entities (
                    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
                    entity TEXT NOT NULL,
                    PRIMARY KEY (memory_id, entity)
                );

                CREATE TABLE IF NOT EXISTS memory_topics (
                    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
                    topic TEXT NOT NULL,
                    PRIMARY KEY (memory_id, topic)
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    memory_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS memories_created_at_idx
                    ON memories(created_at);
                CREATE INDEX IF NOT EXISTS memories_type_status_idx
                    ON memories(memory_type, status);
                CREATE INDEX IF NOT EXISTS memories_kind_idx
                    ON memories(kind);
                CREATE INDEX IF NOT EXISTS memories_source_idx
                    ON memories(source_type, source_ref);
                CREATE INDEX IF NOT EXISTS memory_entities_entity_idx
                    ON memory_entities(entity);
                CREATE INDEX IF NOT EXISTS memory_topics_topic_idx
                    ON memory_topics(topic);
                CREATE INDEX IF NOT EXISTS memory_events_memory_idx
                    ON memory_events(memory_id, event_seq);

                CREATE TRIGGER IF NOT EXISTS memory_events_no_update
                BEFORE UPDATE ON memory_events
                BEGIN
                    SELECT RAISE(ABORT, 'memory_events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS memory_events_no_delete
                BEFORE DELETE ON memory_events
                BEGIN
                    SELECT RAISE(ABORT, 'memory_events are append-only');
                END;
                """
            )
            existing = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(MEMORY_SCHEMA_VERSION),),
                )
            elif int(existing["value"]) != MEMORY_SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported memory schema {existing['value']}; expected {MEMORY_SCHEMA_VERSION}"
                )

    def _normalize(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        memory_id = str(raw.get("memory_id") or "").strip()
        if not memory_id:
            raise ValueError("memory_id is required")
        memory_type = str(
            raw.get("memory_type") or memory_type_for_kind(str(raw.get("kind") or ""))
        ).upper()
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory_type: {memory_type}")
        status = str(raw.get("status") or "ACTIVE").upper()
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status}")
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError("memory text is required")
        created_at = str(raw.get("created_at") or _utc_iso())
        source_refs = _string_list(raw.get("source_refs"))
        source_ref = str(
            raw.get("source_ref") or raw.get("source_message_id") or ""
        ).strip()
        if source_ref and source_ref not in source_refs:
            source_refs.insert(0, source_ref)
        evidence_refs = _string_list(raw.get("evidence_refs"))
        entities = _string_list(raw.get("entities"))
        topics = _string_list(raw.get("topics"))
        importance = min(1.0, max(0.0, float(raw.get("importance", 0.7))))
        confidence = min(1.0, max(0.0, float(raw.get("confidence", 1.0))))
        payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
        normalized = {
            "memory_id": memory_id,
            "memory_type": memory_type,
            "kind": str(raw.get("kind") or "UNSPECIFIED").strip().upper()[:80],
            "subject_id": str(raw.get("subject_id") or "ferro").strip()[:200],
            "text": text[:12000],
            "created_at": created_at,
            "observed_at": str(raw.get("observed_at") or created_at),
            "valid_from": str(raw.get("valid_from") or created_at),
            "valid_until": str(raw.get("valid_until") or ""),
            "status": status,
            "importance": importance,
            "confidence": confidence,
            "source_type": str(raw.get("source_type") or "UNKNOWN").strip().upper()[:80],
            "source_ref": source_ref[:512],
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
            "supersedes": str(raw.get("supersedes") or "").strip()[:256],
            "superseded_by": str(raw.get("superseded_by") or "").strip()[:256],
            "access_scope": str(raw.get("access_scope") or "PRIVATE_OWNER").strip().upper()[:80],
            "payload": dict(payload),
            "entities": entities,
            "topics": topics,
            "schema_version": MEMORY_SCHEMA_VERSION,
        }
        normalized["content_hash"] = hashlib.sha256(
            _json(dict(normalized)).encode("utf-8")
        ).hexdigest()
        return normalized

    def append_memory(
        self, raw: Mapping[str, Any], *, event_type: str = "MEMORY_CREATED"
    ) -> bool:
        row = self._normalize(raw)
        event = {
            "event_id": "memory-event:" + uuid4().hex,
            "memory_id": row["memory_id"],
            "event_type": str(event_type or "MEMORY_CREATED").strip().upper()[:80],
            "occurred_at": _utc_iso(),
            "record_hash": row["content_hash"],
        }
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM memories WHERE memory_id=?",
                (row["memory_id"],),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != row["content_hash"]:
                    raise ValueError(
                        f"memory_id {row['memory_id']} already exists with different content"
                    )
                return False
            connection.execute(
                """
                INSERT INTO memories(
                    memory_id, memory_type, kind, subject_id, text, created_at,
                    observed_at, valid_from, valid_until, status, importance,
                    confidence, source_type, source_ref, source_refs_json,
                    evidence_refs_json, supersedes, superseded_by, access_scope,
                    payload_json, content_hash, schema_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["memory_id"], row["memory_type"], row["kind"], row["subject_id"],
                    row["text"], row["created_at"], row["observed_at"], row["valid_from"],
                    row["valid_until"], row["status"], row["importance"], row["confidence"],
                    row["source_type"], row["source_ref"], _json(row["source_refs"]),
                    _json(row["evidence_refs"]), row["supersedes"], row["superseded_by"],
                    row["access_scope"], _json(row["payload"]), row["content_hash"],
                    row["schema_version"],
                ),
            )
            connection.executemany(
                "INSERT INTO memory_entities(memory_id, entity) VALUES(?,?)",
                [(row["memory_id"], item) for item in row["entities"]],
            )
            connection.executemany(
                "INSERT INTO memory_topics(memory_id, topic) VALUES(?,?)",
                [(row["memory_id"], item) for item in row["topics"]],
            )
            connection.execute(
                """
                INSERT INTO memory_events(
                    event_id, memory_id, event_type, occurred_at, record_hash, payload_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    event["event_id"], event["memory_id"], event["event_type"],
                    event["occurred_at"], event["record_hash"],
                    _json({"schema_version": MEMORY_SCHEMA_VERSION}),
                ),
            )
        self.repair_event_mirror()
        return True

    def import_legacy_jsonl(
        self,
        path: Path,
        *,
        memory_type: str = "SEMANTIC",
        source_type: str = "OWNER_CONVERSATION",
    ) -> int:
        path = Path(path)
        if not path.exists():
            return 0
        imported = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict) or not str(raw.get("memory_id") or "").strip():
                    continue
                candidate = dict(raw)
                candidate["memory_type"] = candidate.get("memory_type") or memory_type
                candidate["source_type"] = candidate.get("source_type") or source_type
                candidate["source_ref"] = (
                    candidate.get("source_ref") or candidate.get("source_message_id") or ""
                )
                if self.append_memory(candidate, event_type="LEGACY_IMPORTED"):
                    imported += 1
        return imported

    def list_memories(self, *, limit: int | None = 1000) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if limit is None:
                rows = connection.execute(
                    "SELECT rowid, * FROM memories ORDER BY created_at DESC, rowid DESC"
                ).fetchall()
            else:
                bounded = max(1, min(int(limit), 5000))
                rows = connection.execute(
                    "SELECT rowid, * FROM memories ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
            result = [self._decode_row(connection, row) for row in rows]
        result.reverse()
        return result

    def _decode_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        memory_id = str(row["memory_id"])
        entities = [
            str(item["entity"])
            for item in connection.execute(
                "SELECT entity FROM memory_entities WHERE memory_id=? ORDER BY entity",
                (memory_id,),
            )
        ]
        topics = [
            str(item["topic"])
            for item in connection.execute(
                "SELECT topic FROM memory_topics WHERE memory_id=? ORDER BY topic",
                (memory_id,),
            )
        ]
        decoded = {
            key: row[key]
            for key in row.keys()
            if key not in {"rowid", "source_refs_json", "evidence_refs_json", "payload_json"}
        }
        decoded["source_refs"] = json.loads(str(row["source_refs_json"]))
        decoded["evidence_refs"] = json.loads(str(row["evidence_refs_json"]))
        decoded["payload"] = json.loads(str(row["payload_json"]))
        decoded["entities"] = entities
        decoded["topics"] = topics
        if decoded["source_type"] == "OWNER_CONVERSATION":
            decoded["source_message_id"] = decoded["source_ref"]
        return decoded

    def transition_memory(
        self,
        memory_id: str,
        *,
        status: str,
        superseded_by: str = "",
        event_type: str = "MEMORY_STATE_CHANGED",
    ) -> dict[str, Any]:
        current = self.get_memory(memory_id)
        if current is None:
            raise KeyError(memory_id)
        next_status = str(status or "").upper()
        if next_status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {next_status}")
        updated = dict(current)
        updated["status"] = next_status
        if superseded_by:
            updated["superseded_by"] = str(superseded_by)
        normalized = self._normalize(updated)
        event = {
            "event_id": "memory-event:" + uuid4().hex,
            "memory_id": memory_id,
            "event_type": str(event_type or "MEMORY_STATE_CHANGED").strip().upper()[:80],
            "occurred_at": _utc_iso(),
            "record_hash": normalized["content_hash"],
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE memories SET status=?, superseded_by=?, content_hash=? WHERE memory_id=?",
                (
                    normalized["status"], normalized["superseded_by"],
                    normalized["content_hash"], memory_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_events(
                    event_id, memory_id, event_type, occurred_at, record_hash, payload_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    event["event_id"], event["memory_id"], event["event_type"],
                    event["occurred_at"], event["record_hash"],
                    _json({"status": next_status, "superseded_by": str(superseded_by or "")}),
                ),
            )
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_dependencies'").fetchone():
                from .memory_service import MemoryService
                MemoryService.invalidate(connection,memory_id)
                connection.execute("UPDATE memory_nodes SET status=? WHERE node_id=?",(next_status,memory_id))
        self.repair_event_mirror()
        result = self.get_memory(memory_id)
        if result is None:
            raise RuntimeError("memory transition lost canonical record")
        return result

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT rowid, * FROM memories WHERE memory_id=?",
                (str(memory_id),),
            ).fetchone()
            return self._decode_row(connection, row) if row is not None else None

    def query_memories(
        self,
        *,
        memory_types: list[str] | None = None,
        statuses: list[str] | None = None,
        source_type: str | None = None,
        access_scope: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        entity: str | None = None,
        topic: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        joins: list[str] = []
        if memory_types:
            marks = ",".join("?" for _ in memory_types)
            clauses.append(f"m.memory_type IN ({marks})")
            params.extend(str(item).upper() for item in memory_types)
        if statuses:
            marks = ",".join("?" for _ in statuses)
            clauses.append(f"m.status IN ({marks})")
            params.extend(str(item).upper() for item in statuses)
        if source_type:
            clauses.append("m.source_type=?")
            params.append(str(source_type).upper())
        if access_scope:
            clauses.append("m.access_scope=?")
            params.append(str(access_scope).upper())
        if created_from:
            clauses.append("m.created_at>=?")
            params.append(str(created_from))
        if created_to:
            clauses.append("m.created_at<?")
            params.append(str(created_to))
        if entity:
            joins.append("JOIN memory_entities me ON me.memory_id=m.memory_id")
            clauses.append("me.entity=?")
            params.append(str(entity))
        if topic:
            joins.append("JOIN memory_topics mt ON mt.memory_id=m.memory_id")
            clauses.append("mt.topic=?")
            params.append(str(topic))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT DISTINCT m.rowid, m.* FROM memories m "
            + " ".join(joins)
            + where
            + " ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?"
        )
        params.append(max(1, min(int(limit), 5000)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            result = [self._decode_row(connection, row) for row in rows]
        return result

    def events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_events ORDER BY event_seq"
            ).fetchall()
        return [dict(row) for row in rows]

    def repair_event_mirror(self) -> int:
        existing_ids: set[str] = set()
        if self.event_mirror_path.exists():
            try:
                with self.event_mirror_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            raw = json.loads(line)
                        except (ValueError, json.JSONDecodeError):
                            continue
                        if isinstance(raw, dict) and str(raw.get("event_id") or ""):
                            existing_ids.add(str(raw["event_id"]))
            except OSError:
                existing_ids = set()
        missing = [row for row in self.events() if str(row["event_id"]) not in existing_ids]
        if not missing:
            return 0
        with self.event_mirror_path.open("a", encoding="utf-8") as handle:
            for row in missing:
                handle.write(_json(row) + "\n")
        return len(missing)


__all__ = [
    "CanonicalMemoryStore", "MEMORY_SCHEMA_VERSION", "MEMORY_STATUSES",
    "MEMORY_TYPES", "memory_type_for_kind",
]
