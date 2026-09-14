"""Content-facing adapter over Agent Runtime's provider-neutral web.search."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from ..web_editorial import digest

MAX_SEARCH_RESULTS = 8


class RuntimeWebSearch:
    """Turn Runtime web.search receipts into bounded Content evidence rows."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def search(self, *, query: str, now: datetime) -> list[dict[str, Any]]:
        normalized = " ".join(str(query).split())
        if not 2 <= len(normalized) <= 180:
            raise ValueError("web search query length is outside bounds")
        receipt = await asyncio.to_thread(
            self.runtime.invoke,
            "web.search",
            {"query": normalized, "limit": MAX_SEARCH_RESULTS, "safesearch": 1},
        )
        self.runtime.persist()
        payload = dict(receipt.payload)
        if not payload.get("ok"):
            raise RuntimeError("runtime web.search returned a failed result")
        if payload.get("capability_id") != "web.search":
            raise RuntimeError("runtime web.search returned the wrong capability")

        rows: list[dict[str, Any]] = []
        for rank, result in enumerate(payload.get("results") or (), start=1):
            if not isinstance(result, dict):
                continue
            title = " ".join(str(result.get("title") or "").split())[:300]
            summary = " ".join(str(result.get("snippet") or "").split())[:2000]
            url = str(result.get("url") or "").strip()
            if not title or not summary or not url:
                continue
            revision = digest({
                "contract": "runtime-web-search-evidence:v1",
                "receipt_id": receipt.receipt_id,
                "url": url,
                "title": title,
                "summary": summary,
                "rank": rank,
            })
            rows.append({
                "title": title,
                "summary": summary,
                "url": url,
                "source": "runtime:web.search:" + str(receipt.provider),
                "source_revision": revision,
                "evidence_refs": [
                    receipt.receipt_id,
                    "web-url-sha256:" + digest(url),
                ],
                "search_query": normalized,
                "search_rank": rank,
                "observed_at": now.isoformat(),
            })
        return rows


__all__ = ["MAX_SEARCH_RESULTS", "RuntimeWebSearch"]
