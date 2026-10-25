"""Low-cost EventGraph change sensor; never calls a language model.

EventGraph owns its own state. Ferro remembers only a sync cursor and compact
immutable observation records. A poll is not a cognition wake.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Callable

import httpx

from .site_news import _site_origin


class EventGraphChangeSensor:
    def __init__(self, *, state_root: Path, site_url: str,
                 client: httpx.Client | None = None):
        self.origin = _site_origin(site_url)
        self.root = Path(state_root) / "application" / "eventgraph"
        self.root.mkdir(parents=True, exist_ok=True)
        self.cursor_path = self.root / "cursor.json"
        self.client = client or httpx.Client(
            timeout=8, follow_redirects=False, trust_env=False
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _get(self, path: str, params: dict[str, str]) -> dict:
        response = self.client.get(
            self.origin + path, params=params,
            headers={"Accept": "application/json"}
        )
        if len(response.content) > 2_000_000:
            raise ValueError("EventGraph change response exceeds 2 MB")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise ValueError("unexpected EventGraph sync schema")
        return payload

    def _save_cursor(self, cursor: str) -> None:
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("missing EventGraph cursor")
        self._atomic(self.cursor_path, {
            "schema_version": "ferro-eventgraph-cursor.v1",
            "cursor": cursor,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    @staticmethod
    def _atomic(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _cursor(self) -> str | None:
        try:
            value = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value.get("cursor") if isinstance(value.get("cursor"), str) else None

    def _reset_to_snapshot(self) -> str:
        # One item is sufficient: snapshot.cursor is a ledger watermark, not
        # an offset into the snapshot's item pages.
        payload = self._get("/api/v1/selected/snapshot", {
            "fields": "default", "limit": "1",
        })
        cursor = payload.get("cursor")
        self._save_cursor(cursor)
        return cursor

    def record_item(self, item: dict, changed_at: str) -> str:
        ident = str(item.get("id") or "").strip()
        if not ident or len(ident) > 180:
            raise ValueError("EventGraph item ID missing or oversized")
        revision = sha256((ident + "\0" + changed_at).encode()).hexdigest()[:24]
        ref = "eventgraph-event:" + revision
        self._atomic(self.root / (revision + ".json"), {
            "schema_version": "ferro-eventgraph-stimulus.v1",
            "ref": ref,
            "eventgraph_id": ident,
            "changed_at": changed_at,
            "item": {
                key: item.get(key)
                for key in ("id", "title", "summary", "category", "score",
                            "publishedAt", "discoveredAt", "links", "source")
            },
        })
        return ref

    def read_item(self, ref: str) -> dict | None:
        marker = str(ref)
        if not marker.startswith("eventgraph-event:"):
            return None
        digest = marker[len("eventgraph-event:"):]
        if len(digest) != 24 or not all(c in "0123456789abcdef" for c in digest):
            return None
        try:
            row = json.loads((self.root / (digest + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return row if row.get("ref") == ref else None

    def poll(self, on_change: Callable[[dict, str, str], None]) -> dict:
        cursor = self._cursor()
        if not cursor:
            self._reset_to_snapshot()
            return {"status": "BASELINED", "delivered": 0}
        delivered = 0
        for _ in range(4):
            try:
                payload = self._get("/api/v1/selected/changes", {
                    "cursor": cursor, "limit": "100",
                })
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    self._reset_to_snapshot()
                    return {"status": "RESYNCED", "delivered": delivered}
                raise
            changes = payload.get("changes")
            next_cursor = payload.get("cursor")
            if not isinstance(changes, list) or not isinstance(next_cursor, str):
                raise ValueError("invalid EventGraph changes response")
            for change in changes:
                if not isinstance(change, dict) or change.get("op") != "upsert":
                    continue
                item = change.get("item")
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                changed_at = str(change.get("changedAt") or "")
                ref = self.record_item(item, changed_at)
                # A callback failure leaves the cursor untouched, allowing replay.
                on_change(item, changed_at, ref)
                delivered += 1
            self._save_cursor(next_cursor)
            cursor = next_cursor
            if not payload.get("hasMore"):
                break
        return {"status": "POLLED", "delivered": delivered}
