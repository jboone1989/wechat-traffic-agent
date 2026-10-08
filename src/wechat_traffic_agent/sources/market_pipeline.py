"""Source-isolated polling, SQLite deduplication and durable research signals.

The module reads public information only. It never publishes, drafts or calls
Ferro cognition. An external scheduled consumer owns each polling invocation.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

import httpx

from .market_signals import BLOCKBEATS, read_binance, read_etf, read_rss, significant_moves


class MarketSignalStore:
    def __init__(self, state_root: Path):
        self.path = Path(state_root) / "content" / "market_signals.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS provider_state (
              name TEXT PRIMARY KEY, state TEXT NOT NULL, checked_at TEXT NOT NULL,
              error TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS signals (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
              title TEXT NOT NULL, summary TEXT NOT NULL, url TEXT NOT NULL,
              published_at TEXT NOT NULL, observed_at TEXT NOT NULL,
              metrics TEXT NOT NULL, eligible INTEGER NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS market_signal_time ON signals(eligible,published_at DESC)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def state(self, name: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT state FROM provider_state WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else {}

    def record(self, name: str, signals: list[dict], *, now: datetime,
               next_state: dict | None = None) -> dict:
        """First valid provider read is a baseline, not 40 historical alerts."""
        with self._connect() as db:
            prior = db.execute("SELECT state FROM provider_state WHERE name=?", (name,)).fetchone()
            first = prior is None or not json.loads(prior[0]).get("_primed")
            new = eligible = 0
            for event in signals:
                when = datetime.fromisoformat(event["published_at"])
                if when.utcoffset() is None or not str(event["url"]).startswith("https://"):
                    continue
                digest = sha256(json.dumps([name, event["kind"], event["identity"]],
                                          ensure_ascii=False).encode()).hexdigest()[:32]
                recent = now - timedelta(hours=36) <= when <= now + timedelta(minutes=5)
                ready = int(not first and recent)
                cursor = db.execute("""INSERT OR IGNORE INTO signals
                  (id,source,kind,title,summary,url,published_at,observed_at,metrics,eligible)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                    "market:" + digest, name, event["kind"], event["title"][:300],
                    event["summary"][:2000], event["url"][:1000], when.astimezone(timezone.utc).isoformat(),
                    now.astimezone(timezone.utc).isoformat(),
                    json.dumps(event.get("metrics", {}), ensure_ascii=False), ready,
                ))
                new += cursor.rowcount
                eligible += cursor.rowcount * ready
            db.execute("""INSERT INTO provider_state(name,state,checked_at,error) VALUES(?,?,?,NULL)
                ON CONFLICT(name) DO UPDATE SET state=excluded.state,
                checked_at=excluded.checked_at,error=NULL""",
                (name, json.dumps({"_primed": True, **(next_state or {})}), now.isoformat()))
            db.execute("DELETE FROM signals WHERE published_at < ?", (
                (now - timedelta(days=30)).astimezone(timezone.utc).isoformat(),))
        return {"name": name, "status": "OK", "new": new,
                "eligible": eligible, "baseline": first}

    def error(self, name: str, exc: Exception, *, now: datetime) -> dict:
        with self._connect() as db:
            db.execute("""INSERT INTO provider_state(name,state,checked_at,error) VALUES(?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET error=excluded.error""",
                (name, "{}", now.isoformat(), type(exc).__name__))
        return {"name": name, "status": "FAILED", "error_type": type(exc).__name__}

    def recent(self, *, now: datetime, limit: int = 12) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""SELECT id,source,kind,title,summary,url,published_at,observed_at,metrics
                FROM signals WHERE eligible=1 AND published_at BETWEEN ? AND ?
                ORDER BY published_at DESC LIMIT ?""", (
                (now - timedelta(hours=36)).astimezone(timezone.utc).isoformat(),
                now.astimezone(timezone.utc).isoformat(), min(max(1, limit), 40))).fetchall()
        fields = ("id", "source", "kind", "title", "summary", "url",
                  "published_at", "observed_at", "metrics")
        return [dict(zip(fields, row)) for row in rows]


def extra_rss_feeds() -> dict[str, str]:
    """Opt-in URLs only: Jin10 and PANews require verified feed endpoints."""
    value = os.getenv("CONTENT_MARKET_EXTRA_RSS_JSON", "").strip()
    feeds = BLOCKBEATS.copy()
    if not value:
        return feeds
    entries = json.loads(value)
    if not isinstance(entries, list) or len(entries) > 12:
        raise ValueError("expected at most 12 explicit RSS source configurations")
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("RSS source must be a dictionary")
        name, url = str(item.get("name") or ""), str(item.get("url") or "")
        parsed = urlsplit(url)
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid RSS source name")
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("RSS source must have an HTTPS URL without credentials")
        feeds[name] = url
    return feeds


class MarketSignalCollector:
    def __init__(self, state_root: Path, *, client: httpx.AsyncClient | None = None):
        self.store = MarketSignalStore(state_root)
        self.client = client

    async def _news(self, client, name, url, now):
        try:
            rows = await read_rss(client, name=name, url=url, now=now)
            return self.store.record(name, rows, now=now)
        except Exception as exc:
            return self.store.error(name, exc, now=now)

    async def _binance(self, client, now):
        name = "binance-spot"
        try:
            old = self.store.state(name).get("tickers", {})
            rows = await read_binance(client)
            changes = significant_moves(rows, old)
            latest = {row["symbol"]: row["change_pct"] for row in rows}
            return self.store.record(name, changes, now=now, next_state={"tickers": latest})
        except Exception as exc:
            return self.store.error(name, exc, now=now)

    async def _etf(self, client, now, key):
        name = "coinglass-btc-etf"
        try:
            rows = await read_etf(client, key)
            return self.store.record(name, rows, now=now)
        except Exception as exc:
            return self.store.error(name, exc, now=now)

    async def run_once(self, *, now: datetime | None = None) -> dict:
        when = now or datetime.now(timezone.utc)
        if when.utcoffset() is None:
            raise ValueError("observation time must include a timezone")
        feeds = extra_rss_feeds()
        client = self.client or httpx.AsyncClient(timeout=12, follow_redirects=False)
        try:
            status = [await self._news(client, name, url, when) for name, url in feeds.items()]
            status.append(await self._binance(client, when))
            if key := os.getenv("COINGLASS_API_KEY", "").strip():
                status.append(await self._etf(client, when, key))
        finally:
            if self.client is None:
                await client.aclose()
        return {"status": "OK" if all(s["status"] == "OK" for s in status) else "PARTIAL",
                "observed_at": when.isoformat(), "providers": status,
                "new_research_events": sum(s.get("eligible", 0) for s in status),
                "publication_authority": False}
