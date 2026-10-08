"""Small durable filesystem outbox; website receipts are independent of X results."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re

from .models import PublicationEvent, DistributionTask
from .errors import NonRetryablePublication


class PublicationEventStore:
    def __init__(self, state_root):
        root = Path(state_root)
        self.events = root / "publication" / "events"
        self.distribution = root / "distribution"
        self.queue = self.distribution / "queue"
        self.processing = self.distribution / "processing"
        self.success = self.distribution / "success"
        self.failed = self.distribution / "failed"

    @contextmanager
    def _lock(self):
        self.distribution.mkdir(parents=True, exist_ok=True)
        with (self.distribution / ".lock").open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            yield

    @staticmethod
    def _save_new(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, sort_keys=True, ensure_ascii=False, indent=2)
            file.write("\n")

    @staticmethod
    def _update(path, payload):
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def save_event(self, event: PublicationEvent):
        path = self.events / f"{event.event_id}.json"
        try:
            self._save_new(path, event.model_dump())
        except FileExistsError:
            prior = json.loads(path.read_text(encoding="utf-8"))
            for field in ("event_id", "artifact_id", "canonical_url", "content_hash"):
                if prior.get(field) != getattr(event, field):
                    raise ValueError("immutable publication event identity conflict: " + field)
        return path

    def load_event(self, event_id: str) -> PublicationEvent:
        raw = json.loads((self.events / f"{event_id}.json").read_text(encoding="utf-8"))
        raw["published_at"] = datetime.fromisoformat(raw["published_at"])
        return PublicationEvent(**raw)

    def subscription_started_at(self, channel: str, *, now=None):
        """Persist a channel's starting watermark; never backfill old events by accident."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,60}", channel):
            raise ValueError("invalid publication subscriber channel")
        path = self.distribution / "subscriptions" / f"{channel}.json"
        with self._lock():
            if not path.exists():
                started = now or datetime.now(timezone.utc)
                if started.utcoffset() is None:
                    raise ValueError("subscription time must have a timezone")
                self._save_new(path, {"started_at": started.astimezone(timezone.utc).isoformat()})
            return datetime.fromisoformat(json.loads(path.read_text())["started_at"])

    def find_task(self, task_id: str):
        for folder in (self.queue, self.processing, self.success, self.failed):
            path = folder / f"{task_id}.json"
            if path.exists():
                return path
        return None

    def enqueue_distribution(self, task: DistributionTask):
        event = self.load_event(task.event_id)
        if event.visibility != "PUBLIC":
            raise ValueError("private/unlisted publication must not be distributed")
        with self._lock():
            existing = self.find_task(task.distribution_id)
            if existing:
                recorded = json.loads(existing.read_text(encoding="utf-8"))
                if (recorded["event_id"], recorded["channel"]) != (task.event_id, task.channel):
                    raise ValueError("distribution identity conflict")
                return existing
            path = self.queue / f"{task.distribution_id}.json"
            self._save_new(path, task.model_dump())
            return path

    def attempt_count(self, *, local_date, timezone_info, channel="x"):
        """Count actual dispatch attempts, not successful X posts or queue creations."""
        total = 0
        for folder in (self.queue, self.processing, self.success, self.failed):
            for path in folder.glob("*.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if channel is not None and raw.get("channel") != channel:
                    continue
                for timestamp in raw.get("attempt_times", []):
                    if datetime.fromisoformat(timestamp).astimezone(timezone_info).date() == local_date:
                        total += 1
        return total

    def claim_next(self, channels=None):
        with self._lock():
            for path in sorted(self.queue.glob("*.json")):
                raw = json.loads(path.read_text(encoding="utf-8"))
                task = DistributionTask(**raw)
                if channels is not None and task.channel not in channels:
                    continue
                if task.status not in {"PENDING", "RETRY_PENDING"}:
                    raise ValueError("unexpected queued status: " + task.status)
                target = self.processing / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
                task = replace(task, status="RUNNING", attempts=task.attempts + 1,
                               attempt_times=[*task.attempt_times, datetime.now(timezone.utc).isoformat()])
                self._update(target, task.model_dump())
                return task
        return None

    def finish(self, task: DistributionTask, *, result_url=None, result_post_id=None,
               error=None, max_attempts=3):
        with self._lock():
            source = self.processing / f"{task.distribution_id}.json"
            if not source.is_file():
                raise FileNotFoundError("distribution task not claimed: " + task.distribution_id)
            if result_url:
                done = replace(task, status="SUCCESS", published_url=result_url,
                               published_post_id=result_post_id, last_error=None)
                destination = self.success / source.name
            else:
                status = "FAILED_PERMANENT" if task.attempts >= max_attempts else "RETRY_PENDING"
                done = replace(task, status=status, last_error=error or "distribution unverified")
                destination = self.failed / source.name if status == "FAILED_PERMANENT" else self.queue / source.name
            self._update(source, done.model_dump())
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
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
