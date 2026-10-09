"""Channel copy and delivery state live on the canonical article row."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from .article_repository import ArticleRepository


class ChannelRecords:
    def __init__(self, state_root):
        self.repo = ArticleRepository(Path(state_root) / "content/automatic_website/articles.sqlite3")
        if not self.repo.initialized():
            self.repo.initialize([])
        self.connection = None

    @contextmanager
    def transaction(self):
        if self.connection is not None:
            yield self
            return
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.connection = db
            try:
                yield self
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self.connection = None

    @staticmethod
    def _all(db, channel):
        return [dict(json.loads(row["channel_state_json"])[channel], article_id=row["artifact_id"])
                for row in db.execute("SELECT artifact_id,channel_state_json FROM articles")
                if channel in json.loads(row["channel_state_json"])]

    def all(self, channel):
        if self.connection is not None:
            return self._all(self.connection, channel)
        with self.repo.connect() as db:
            return self._all(db, channel)

    def get(self, channel, identity):
        return next((r for r in self.all(channel) if r.get("draft_id") == identity
                     or r.get("distribution_id") == identity or r.get("event_id") == identity
                     or r.get("cycle") == identity or r["article_id"] == identity), None)

    def put(self, article_id, channel, value):
        if self.connection is None:
            with self.transaction():
                return self.put(article_id, channel, value)
        value = dict(value)
        value.pop("article_id", None)
        if channel == "x" and value.get("published_text") is not None:
            value["body"] = value.pop("published_text")

        row = self.connection.execute("SELECT channel_state_json FROM articles WHERE artifact_id=?",
                                      (article_id,)).fetchone()
        if row is None:
            now = datetime.now(timezone.utc).isoformat()
            self.connection.execute(
                "INSERT INTO articles(artifact_id,kind,slug,route,title,summary,created_at,updated_at,"
                "content_json,content_sha256,record_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (article_id, "note", article_id, "/internal/posts/" + article_id,
                 value.get("reader_question") or value.get("title") or "Social post", "",
                 now, now, "{}", "", "channel"))
            state = {}
        else:
            state = json.loads(row["channel_state_json"])
        state[channel] = value
        self.connection.execute("UPDATE articles SET channel_state_json=? WHERE artifact_id=?",
                                (json.dumps(state, ensure_ascii=False), article_id))
        return article_id
