"""Constant perception: Stage 2 of the neural-native roadmap.

The organism keeps sensing its environment on each source's own cadence
(market data fast, feeds slow) instead of only gathering evidence when a
content task demands it.  Fresh increments become stimulus currents;
repeated information habituates away (fingerprint dedup).  When a burst
elects a creation-class channel *and* fresh information contributed to
the stimulus, that is an expression urge: the organism posts because it
felt like saying something, not because a quota came due.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import asyncio
import json
import os
from pathlib import Path
from typing import Any

_MARKET_INTERVAL_SECONDS = 60.0
_FEEDS_INTERVAL_SECONDS = 900.0
_MAX_SEEN_FINGERPRINTS = 4096


def _fingerprint(payload: Any) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


class SensoryStream:
    """Poll the environment on per-source cadences; output stimulus currents."""

    def __init__(self, *, state_root: Path, feed_urls: tuple[str, ...] = ()) -> None:
        self.state_root = Path(state_root)
        self.feed_urls = tuple(feed_urls)
        self.path = self.state_root / "application" / "sensory_stream.json"
        raw: dict[str, Any] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.seen: set[str] = set(raw.get("seen") or ())
        self.sources: dict[str, dict[str, Any]] = raw.get("sources") or {}
        self.last_novelty: dict[str, float] = {}

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            seen = list(self.seen)[-_MAX_SEEN_FINGERPRINTS:]
            payload = {
                "schema_version": "sensory-stream.v1",
                "seen": seen,
                "sources": self.sources,
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.path)
            self.seen = set(seen)
        except OSError:
            pass

    def _due(self, name: str, interval: float, now: datetime) -> bool:
        row = self.sources.get(name) or {}
        last = row.get("last_fetched_at")
        if not last:
            return True
        try:
            at = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            return (now - at).total_seconds() >= interval
        except ValueError:
            return True

    def currents(self, now: datetime | None = None) -> dict[str, float]:
        """Fetch whatever is due; return channel currents for fresh novelty."""
        now = now or datetime.now(timezone.utc)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._collect_all(now))
        # A loop is already running (should not happen in the pulse thread);
        # skip this tick rather than nest.
        return {}

    async def _collect_all(self, now: datetime) -> dict[str, float]:
        novelty: dict[str, float] = {}
        if self._due("market", _MARKET_INTERVAL_SECONDS, now):
            try:
                novelty["market"] = await self._collect_market(now)
            except Exception:
                pass  # a failed sense keeps its previous state
        if self.feed_urls and self._due("feeds", _FEEDS_INTERVAL_SECONDS, now):
            try:
                novelty["feeds"] = await self._collect_feeds(now)
            except Exception:
                pass
        self.last_novelty = novelty
        self._persist()
        return novelty

    async def _collect_market(self, now: datetime) -> float:
        from .sources.binance_market_structure import collect_market_structure

        report = await collect_market_structure()
        if not isinstance(report, dict) or not report:
            return 0.0
        fingerprint = _fingerprint(report)
        row = self.sources.get("market") or {}
        previous = row.get("last_fingerprint")
        row.update({
            "last_fetched_at": now.isoformat(),
            "last_fingerprint": fingerprint,
        })
        self.sources["market"] = row
        if fingerprint == previous:
            return 0.0  # unchanged market: habituated
        self.seen.add(fingerprint)
        return 0.35  # fresh market structure is intrinsically novel

    async def _collect_feeds(self, now: datetime) -> float:
        from .content_loop import collect_news

        rows, _failures = await collect_news(list(self.feed_urls), now)
        fresh = 0
        row_state = self.sources.get("feeds") or {}
        seen_titles = set(row_state.get("seen_items") or ())
        for item in rows if isinstance(rows, list) else []:
            title = str(item.get("title") or "")[:200]
            mark = _fingerprint(title)
            if mark in seen_titles:
                continue
            seen_titles.add(mark)
            fresh += 1
        row_state.update({
            "last_fetched_at": now.isoformat(),
            "seen_items": list(seen_titles)[-512:],
        })
        self.sources["feeds"] = row_state
        if fresh == 0:
            return 0.0
        self.seen.update(_fingerprint({"feed": i}) for i in range(fresh))
        return min(1.0, 0.22 * fresh)

    @staticmethod
    def to_channel_currents(novelty: dict[str, float]) -> dict[str, float]:
        """Map sensory novelty onto semantic channels."""
        currents: dict[str, float] = {}
        if novelty.get("feeds"):
            currents["curiosity"] = (
                currents.get("curiosity", 0.0) + float(novelty["feeds"])
            )
        if novelty.get("market"):
            currents["opportunity"] = (
                currents.get("opportunity", 0.0) + 0.6 * float(novelty["market"])
            )
            currents["threat_risk"] = (
                currents.get("threat_risk", 0.0) + 0.3 * float(novelty["market"])
            )
        return {
            channel: min(1.5, max(0.0, value))
            for channel, value in currents.items()
            if value > 0.0
        }


__all__ = ["SensoryStream"]
