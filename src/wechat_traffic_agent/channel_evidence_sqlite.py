"""Indexed SQLite backend for versioned public channel evidence.

The row record is the authority. Immutable source captures and learning memory
remain separate. Legacy JSON rows are imported idempotently before activation.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class EvidenceTable:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "evidence.sqlite3"

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=20)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=20000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evidence (
                    identity_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    account TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (identity_hash, revision)
                ) WITHOUT ROWID
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS evidence_time "
                "ON evidence(observed_at, identity_hash, revision)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS evidence_channel "
                "ON evidence(channel, account, observed_at)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS migration (name TEXT PRIMARY KEY)"
            )
            with conn:
                yield conn

    @staticmethod
    def upsert(conn, row, *, identity_hash: str, duplicate_ok: bool) -> None:
        payload = row.model_dump_json()
        old = conn.execute(
            "SELECT payload FROM evidence WHERE identity_hash=? AND revision=?",
            (identity_hash, row.revision),
        ).fetchone()
        if old:
            if old["payload"] != payload:
                raise FileExistsError("evidence identity/revision conflict")
            if duplicate_ok:
                return
            return
        conn.execute(
            "INSERT INTO evidence(identity_hash,revision,channel,account,observed_at,payload) "
            "VALUES(?,?,?,?,?,?)",
            (
                identity_hash, row.revision, row.channel, row.account,
                row.observed_at.astimezone(timezone.utc).isoformat(), payload,
            ),
        )

    def import_legacy(self, conn, *, decode, identity_hash):
        if conn.execute(
            "SELECT 1 FROM migration WHERE name='legacy-json-v1'"
        ).fetchone():
            return 0
        paths = sorted(self.root.glob("*/*.json"))
        for path in paths:
            row = decode(path.read_text(encoding="utf-8"))
            key = identity_hash(row.identity())
            if path.parent.name != key or path.name != f"{row.revision:05d}.json":
                raise ValueError("legacy evidence file path/identity mismatch")
            self.upsert(conn, row, identity_hash=key, duplicate_ok=True)
        conn.execute("INSERT INTO migration(name) VALUES('legacy-json-v1')")
        return len(paths)

    def as_of(self, cutoff: datetime, *, decode, identity_hash) -> list:
        if cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        if not self.path.is_file() and not self.root.exists():
            return []
        with self.connect() as conn:
            self.import_legacy(conn, decode=decode, identity_hash=identity_hash)
            # Ordered by identity, descending revision: first row in each
            # identity group is the correct revision as of this cutoff.
            records = conn.execute(
                "SELECT identity_hash, payload FROM evidence "
                "WHERE observed_at <= ? ORDER BY identity_hash, revision DESC",
                (cutoff.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        latest = {}
        for record in records:
            key = record["identity_hash"]
            if key not in latest:
                latest[key] = decode(record["payload"])
        return list(latest.values())

    def write(self, row, *, decode, identity_hash) -> Path:
        with self.connect() as conn:
            self.import_legacy(conn, decode=decode, identity_hash=identity_hash)
            self.upsert(conn, row, identity_hash=identity_hash(row.identity()), duplicate_ok=True)
        return self.path

    def count(self, *, decode, identity_hash):
        with self.connect() as conn:
            imported = self.import_legacy(conn, decode=decode, identity_hash=identity_hash)
            count = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        return {"records": count, "legacy_files_seen": imported}

    def evidence_refs(self, *, decode, identity_hash) -> set[str]:
        with self.connect() as conn:
            self.import_legacy(conn, decode=decode, identity_hash=identity_hash)
            records = conn.execute("SELECT payload FROM evidence").fetchall()
        refs = set()
        for record in records:
            refs.update(decode(record["payload"]).evidence_refs)
        return refs
