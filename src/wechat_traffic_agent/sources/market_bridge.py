"""Adapter from durable market events to Ferro's EXISTING research candidates.

No drafts, publication instructions, market-news templates or model decisions.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from .market_pipeline import MarketSignalStore


def market_research_candidates(*, state_root: Path, now: datetime, limit: int = 12) -> list[dict]:
    """Read a bounded batch of facts without claiming research has been done."""
    location = Path(state_root) / "content" / "market_signals.sqlite3"
    if not location.is_file():
        return []
    result = []
    for row in MarketSignalStore(state_root).recent(now=now, limit=limit):
        result.append({
            "evidence_id": row["id"],
            "source_kind": "rss" if row["kind"] == "news" else "public-api",
            "source": row["source"],
            "title": row["title"],
            "summary": row["summary"],
            "evidence_text": row["summary"],
            "url": row["url"],
            "observed_at": row["observed_at"],
            "published_at": row["published_at"],
            "source_revision": row["id"],
            "evidence_refs": (row["id"],),
            "publisher_feed": row["source"],
        })
    return result


async def collect_market_evidence(*, now: datetime, state_root: Path,
                                  baseline_fetcher: Callable[[datetime], Awaitable[list[dict]]]) -> list[dict]:
    """Composition root combines existing OKX source and independently polled events."""
    events = market_research_candidates(state_root=state_root, now=now)
    try:
        baseline = await baseline_fetcher(now)
    except Exception:
        if not events:
            raise
        baseline = []
    return [*events, *baseline][:24]
