"""SQLite source of truth for Ferro's published article records.

Nginx-facing HTML and /data/articles.json are disposable presentation caches,
not authoritative article storage.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

from wechat_traffic_agent.content_artifact import ContentArtifact
from .routing import _route_for
from .fingerprint import digest


class ArticleRepository:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise FileNotFoundError("article database not initialized: " + str(self.path))
        with closing(sqlite3.connect(self.path, timeout=20)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=20000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn

    def initialized(self):
        if not self.path.is_file():
            return False
        with self.connect() as conn:
            return conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
            ).fetchone() is not None

    @staticmethod
    def _create_schema(conn):
        conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS articles (
                artifact_id TEXT PRIMARY KEY NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('article', 'research', 'guide', 'note')),
                slug TEXT NOT NULL,
                route TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                published_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(kind, slug)
            );
            CREATE INDEX IF NOT EXISTS articles_recent ON articles(updated_at DESC, artifact_id);
            CREATE TABLE IF NOT EXISTS article_topics (
                artifact_id TEXT NOT NULL REFERENCES articles(artifact_id) ON DELETE CASCADE,
                topic TEXT NOT NULL,
                PRIMARY KEY (artifact_id, topic)
            );
            CREATE INDEX IF NOT EXISTS topic_lookup ON article_topics(topic, artifact_id);
            PRAGMA user_version=1;
        """)

    @staticmethod
    def _insert(conn, article: ContentArtifact):
        if not article.public_safe:
            raise ValueError("SQLite article store accepts public-approved articles only")
        document = article.model_dump(mode="json")
        content_json = json.dumps(document, ensure_ascii=False, sort_keys=True)
        row = (
            article.artifact_id, article.kind.value, article.slug,
            _route_for(article), article.title, article.summary,
            article.created_at.isoformat(),
            (article.updated_at or article.created_at).isoformat(),
            content_json, digest(document),
        )
        old = conn.execute(
            "SELECT content_sha256 FROM articles WHERE artifact_id=?", (article.artifact_id,)
        ).fetchone()
        if old:
            if old["content_sha256"] != row[-1]:
                raise ValueError("immutable article identity conflict: " + article.artifact_id)
            return False
        conn.execute(
            "INSERT INTO articles (artifact_id,kind,slug,route,title,summary,created_at,updated_at,content_json,content_sha256) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", row
        )
        for topic in sorted(set(article.tags)):
            conn.execute(
                "INSERT INTO article_topics (artifact_id,topic) VALUES (?,?)",
                (article.artifact_id, topic),
            )
        return True

    def initialize(self, initial_articles):
        """One-time transaction. A pre-existing DB is never overwritten by JSON."""
        if self.path.exists():
            if not self.initialized():
                raise ValueError("article database exists but schema is invalid")
            raise FileExistsError("article database already initialized")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = self.path.with_name(self.path.name + ".importing")
        if db.exists():
            raise RuntimeError("unfinished SQLite migration file exists: " + str(db))
        try:
            with sqlite3.connect(db, timeout=20) as conn:
                conn.row_factory = sqlite3.Row
                self._create_schema(conn)
                with conn:
                    for article in initial_articles:
                        self._insert(conn, article)
                count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
                if count != len(initial_articles):
                    raise ValueError("duplicate article ID in database migration")
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise ValueError("SQLite integrity check failed: " + result)
            db.chmod(0o600)
            db.replace(self.path)
            # Enable concurrent readers in the public Web service and the
            # single writer. Create WAL files only after the DB is at its
            # permanent path (never while it still has the import suffix).
            with sqlite3.connect(self.path, timeout=20) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            db.unlink(missing_ok=True)
            raise
        return count

    def list_artifacts(self):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT content_json FROM articles ORDER BY created_at, artifact_id"
            ).fetchall()
        return [ContentArtifact.model_validate_json(row["content_json"]) for row in rows]

    def count(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def insert(self, article):
        with self.connect() as conn:
            with conn:
                return self._insert(conn, article)

    def get(self, kind, slug):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT content_json FROM articles WHERE kind=? AND slug=?",
                (kind, slug),
            ).fetchone()
        return None if row is None else ContentArtifact.model_validate_json(row["content_json"])

    def topics(self, topic):
        with self.connect() as conn:
            return [
                row["artifact_id"] for row in conn.execute(
                    "SELECT artifact_id FROM article_topics WHERE topic=? ORDER BY artifact_id", (topic,)
                ).fetchall()
            ]
