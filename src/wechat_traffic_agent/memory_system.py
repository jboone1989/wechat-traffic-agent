"""Compatibility facade for Agent Runtime durable memory.

The canonical store is no longer Ferro-owned. This shim preserves one legacy
behavior while old Ferro modules migrate independently: if the historical
retrieval-view tables exist in the same SQLite database, a canonical state
transition invalidates those derived views in the same transaction.

New code must import agent_runtime.memory.durable or use agent.memory.
"""
from __future__ import annotations

import sqlite3

from agent_runtime.memory.durable import (
    CanonicalMemoryStore as RuntimeCanonicalMemoryStore,
    MEMORY_SCHEMA_VERSION,
    MEMORY_STATUSES,
    MEMORY_TYPES,
    TransitionHook,
    memory_type_for_kind,
)


def _invalidate_legacy_retrieval_views(
    db: sqlite3.Connection,
    memory_id: str,
    status: str,
) -> None:
    tables = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('memory_nodes','memory_dependencies','memory_node_changes')"
        ).fetchall()
    }
    required = {
        "memory_nodes",
        "memory_dependencies",
        "memory_node_changes",
    }
    if not required.issubset(tables):
        return
    rows = db.execute(
        """WITH RECURSIVE affected(id) AS (
            SELECT parent_id FROM memory_dependencies WHERE child_id=?
            UNION
            SELECT d.parent_id
            FROM memory_dependencies d
            JOIN affected a ON d.child_id=a.id
        )
        SELECT n.node_id,n.version
        FROM memory_nodes n
        JOIN affected a ON a.id=n.node_id""",
        (memory_id,),
    ).fetchall()
    for row in rows:
        seq = db.execute(
            "INSERT INTO memory_node_changes(node_id,status,version) "
            "VALUES(?,?,?)",
            (row["node_id"], "DIRTY", row["version"]),
        ).lastrowid
        db.execute(
            "UPDATE memory_nodes SET status='DIRTY',updated_seq=? "
            "WHERE node_id=?",
            (seq, row["node_id"]),
        )
    db.execute(
        "UPDATE memory_nodes SET status=? WHERE node_id=?",
        (status, memory_id),
    )


class CanonicalMemoryStore(RuntimeCanonicalMemoryStore):
    """Migration-only compatibility class; implementation lives in Runtime."""

    def __init__(
        self,
        state_root,
        *,
        default_subject_id: str = "ferro",
        on_transition: TransitionHook | None = None,
    ) -> None:
        super().__init__(
            state_root,
            default_subject_id=default_subject_id,
            on_transition=on_transition or _invalidate_legacy_retrieval_views,
        )


__all__ = [
    "CanonicalMemoryStore",
    "MEMORY_SCHEMA_VERSION",
    "MEMORY_STATUSES",
    "MEMORY_TYPES",
    "memory_type_for_kind",
]
