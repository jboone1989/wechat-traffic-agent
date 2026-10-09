"""Channel dispatch operates on fields of the canonical article record."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from .models import PublicationEvent, DistributionTask
from .errors import NonRetryablePublication


class PublicationEventStore:
    def __init__(self, state_root):
        self.records = ChannelRecords(state_root)

    def list_events(self):
        return self.records.all("website")

    def list_tasks(self):
        with self.records.transaction():
            return [dict(value, article_id=row["artifact_id"])
                    for row in self.records.connection.execute("SELECT artifact_id,channel_state_json FROM articles")
                    for value in json.loads(row["channel_state_json"]).values()
                    if isinstance(value, dict) and value.get("distribution_id")]

    def save_event(self, event):
        with self.records.transaction():
            prior = self.records.get("website", event.event_id)
            if prior:
                for key in ("event_id", "artifact_id", "canonical_url", "content_hash"):
                    if prior.get(key) != getattr(event, key):
                        raise ValueError("immutable publication event identity conflict: " + key)
                return event.artifact_id
            fields = event.model_dump()
            # Title/summary are already article fields, never duplicate the body.
            with self.records.repo.connect() as db:
                row = db.execute("SELECT artifact_id FROM articles WHERE artifact_id=?",
                                 (event.artifact_id,)).fetchone()
            if row:
                fields.pop("title")
                fields.pop("summary")
                fields.pop("topics")
            return self.records.put(event.artifact_id, "website", fields)

    def load_event(self, event_id):
        raw = self.records.get("website", event_id)
        if raw is None:
            raise KeyError(event_id)
        raw = dict(raw)
        article_id = raw.pop("article_id")
        with self.records.repo.connect() as db:
            row = db.execute("SELECT title,summary,content_json FROM articles WHERE artifact_id=?",
                             (article_id,)).fetchone()
        content = json.loads(row["content_json"])
        raw.setdefault("title", row["title"])
        raw.setdefault("summary", row["summary"])
        raw.setdefault("topics", content.get("tags", []))
        raw["published_at"] = datetime.fromisoformat(raw["published_at"])
        return PublicationEvent(**raw)

    def subscription_started_at(self, channel, *, now=None):
        with self.records.transaction():
            db = self.records.connection
            db.execute("CREATE TABLE IF NOT EXISTS publication_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            key = "subscription:" + channel
            stamp = (now or datetime.now(timezone.utc)).isoformat()
            db.execute("INSERT OR IGNORE INTO publication_settings VALUES(?,?)", (key, stamp))
            return datetime.fromisoformat(db.execute("SELECT value FROM publication_settings WHERE key=?",
                                                     (key,)).fetchone()[0])

    def find_task(self, task_id):
        return next((row for row in self.list_tasks() if row["distribution_id"] == task_id), None)

    def enqueue_distribution(self, task):
        event = self.load_event(task.event_id)
        if event.visibility != "PUBLIC":
            raise ValueError("private/unlisted publication must not be distributed")
        with self.records.transaction():
            existing = self.find_task(task.distribution_id)
            if existing:
                if (existing["event_id"], existing["channel"]) != (task.event_id, task.channel):
                    raise ValueError("distribution identity conflict")
                return existing["article_id"]
            return self.records.put(event.artifact_id, task.channel, task.model_dump())

    def attempt_count(self, *, local_date, timezone_info, channel="x"):
        return sum(datetime.fromisoformat(t).astimezone(timezone_info).date() == local_date
                   for row in self.list_tasks() if channel is None or row["channel"] == channel
                   for t in row.get("attempt_times", []))

    def claim_next(self, channels=None):
        with self.records.transaction():
            for row in sorted(self.list_tasks(), key=lambda r: r["distribution_id"]):
                if (channels is not None and row["channel"] not in channels
                        or row["status"] not in {"PENDING", "RETRY_PENDING"}):
                    continue
                article_id = row.pop("article_id")
                task = DistributionTask(**row)
                task = replace(task, status="RUNNING", attempts=task.attempts + 1,
                               attempt_times=[*task.attempt_times, datetime.now(timezone.utc).isoformat()])
                self.records.put(article_id, task.channel, task.model_dump())
                return task
        return None

    def finish(self, task, *, result_url=None, result_post_id=None, error=None, max_attempts=3):
        with self.records.transaction():
            row = self.find_task(task.distribution_id)
            if not row or row["status"] != "RUNNING":
                raise FileNotFoundError("distribution task not claimed: " + task.distribution_id)
            if result_url:
                done = replace(task, status="SUCCESS", published_url=result_url,
                               published_post_id=result_post_id, last_error=None,
                               published_at=datetime.now(timezone.utc).isoformat(), body=row.get("body"))
            else:
                status = "FAILED_PERMANENT" if task.attempts >= max_attempts else "RETRY_PENDING"
                done = replace(task, status=status, last_error=error or "distribution unverified")
            self.records.put(row["article_id"], task.channel, done.model_dump())
            return done


class DistributionWorker:
    """Run one outbox task using a channel adapter. No X access occurs without an adapter."""

    def __init__(self, store: PublicationEventStore, adapters, *, max_attempts=3):
        self.store = store
        self.adapters = adapters
        self.max_attempts = max_attempts

    def run_once(self):
        task = self.store.claim_next(channels=self.adapters.keys())
        if task is None:
            return None
        try:
            adapter = self.adapters[task.channel]
            event = self.store.load_event(task.event_id)
            outcome = adapter(event, task)
            if not isinstance(outcome, dict) or outcome.get("status") != "POSTED":
                raise RuntimeError("channel did not return verified POSTED receipt")
            url = outcome.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise RuntimeError("POSTED receipt must include a verified HTTPS URL")
            return self.store.finish(task, result_url=url,
                                     result_post_id=str(outcome.get("post_id") or "") or None,
                                     max_attempts=self.max_attempts)
        except NonRetryablePublication as exc:
            return self.store.finish(task, error=f"{type(exc).__name__}: {exc}",
                                     max_attempts=task.attempts)
        except Exception as exc:
            return self.store.finish(task, error=f"{type(exc).__name__}: {exc}", max_attempts=self.max_attempts)
