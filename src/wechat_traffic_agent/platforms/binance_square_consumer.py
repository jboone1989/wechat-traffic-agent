"""Binance Square independently consumes Ferro's durable PublicationEvent log.

The website publisher, X worker and cognition loop never import this module.
Only the shared publication store and contract are used.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from hashlib import sha256
from pathlib import Path

from wechat_traffic_agent.platforms.binance_square import (
    publish_square, square_enabled, square_writes_enabled,
)
from wechat_traffic_agent.publication.event_store import (
    DistributionWorker, PublicationEventStore,
)
from wechat_traffic_agent.publication.models import DistributionTask


class SquareEventConsumer:
    def __init__(self, state_root: Path, *, publisher=publish_square):
        self.store = PublicationEventStore(state_root)
        self.publisher = publisher

    def enqueue_events(self, *, now: datetime | None = None) -> int:
        """Subscribe without retroactively reposting an entire website archive."""
        started_at = self.store.subscription_started_at("binance_square", now=now)
        queued = 0
        for file in sorted(self.store.events.glob("*.json")):
            try:
                event = self.store.load_event(file.stem)
            except (OSError, ValueError, json.JSONDecodeError):
                continue  # A concurrent event write becomes readable next tick.
            if event.visibility != "PUBLIC" or event.published_at < started_at:
                continue
            identity = sha256(
                json.dumps({"event_id": event.event_id, "channel": "binance_square"},
                           sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            task = DistributionTask(identity, event.event_id, "binance_square")
            if self.store.find_task(task.distribution_id) is None:
                self.store.enqueue_distribution(task)
                queued += 1
        return queued

    def run_once(self, *, now: datetime | None = None):
        if not square_enabled():
            return None
        self.enqueue_events(now=now)
        if not square_writes_enabled() or not os.getenv("BINANCE_SQUARE_OPENAPI_KEY", "").strip():
            return None
        return DistributionWorker(
            self.store, {"binance_square": self.publisher},
        ).run_once()
