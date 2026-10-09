"""Time-indexed SQLite store for first-party website interaction events."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import timezone
from pathlib import Path
import sqlite3


class EngagementEventsTable:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "events.sqlite3"
        self.legacy = self.root / "events"

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=20)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=20000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    route TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL
                ) WITHOUT ROWID
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS events_time ON events(observed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS events_route_time ON events(route,observed_at)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS migration(name TEXT PRIMARY KEY)")
            with conn:
                yield conn

    @staticmethod
    def insert(conn, event, *, stable, duplicate_ok=True) -> str:
        old = conn.execute(
            "SELECT payload FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        if old:
            prior = type(event).model_validate_json(old["payload"])
            if stable(prior) != stable(event):
                raise ValueError("engagement event_id conflicts with existing event")
            return "DUPLICATE"
        conn.execute(
            "INSERT INTO events(event_id,observed_at,route,event_type,payload) "
            "VALUES (?,?,?,?,?)",
            (event.event_id, event.observed_at.astimezone(timezone.utc).isoformat(),
             event.route, event.event_type, event.model_dump_json()),
        )
        return "RECORDED"

    def migrate_legacy(self, conn, *, decode, stable):
        if conn.execute(
            "SELECT 1 FROM migration WHERE name='legacy-event-json-v1'"
        ).fetchone():
            return 0
        paths = sorted(self.legacy.glob("*/*.json"))
        for path in paths:
            event = decode(path.read_text(encoding="utf-8"))
            date = event.observed_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
            if path.name != event.event_id + ".json" or path.parent.name != date:
                raise ValueError("legacy engagement event filename mismatch")
            self.insert(conn, event, stable=stable)
        conn.execute("INSERT INTO migration VALUES('legacy-event-json-v1')")
        return len(paths)

    def record(self, event, *, decode, stable):
        with self.connect() as conn:
            self.migrate_legacy(conn, decode=decode, stable=stable)
            return self.insert(conn, event, stable=stable)

    def events(self, start, end, *, decode, stable):
        if not self.path.exists() and not self.legacy.exists():
            return []
        with self.connect() as conn:
            self.migrate_legacy(conn, decode=decode, stable=stable)
            rows = conn.execute(
                "SELECT payload FROM events WHERE observed_at >= ? "
                "AND observed_at < ? ORDER BY observed_at,event_id",
                (start.astimezone(timezone.utc).isoformat(),
                 end.astimezone(timezone.utc).isoformat()),
            ).fetchall()
        return [decode(row["payload"]) for row in rows]

    def count(self, *, decode, stable):
        with self.connect() as conn:
            imported = self.migrate_legacy(conn, decode=decode, stable=stable)
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {"events": count, "legacy_files_seen": imported}
