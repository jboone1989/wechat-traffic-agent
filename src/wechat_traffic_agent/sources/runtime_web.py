"""Content-facing adapter over Agent Runtime's provider-neutral web.search."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from ..web_editorial import digest

MAX_SEARCH_RESULTS = 8
MAX_FETCH_PAGES = 4
MAX_EVIDENCE_CHARS = 2000


class RuntimeWebEvidenceError(RuntimeError):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        self.source = source


class RuntimeWebSearch:
    """Use Runtime search for discovery and Runtime fetch for factual evidence."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def search(self, *, query: str, now: datetime) -> list[dict[str, Any]]:
        normalized = " ".join(str(query).split())
        if not 2 <= len(normalized) <= 180:
            raise ValueError("web search query length is outside bounds")
        search_receipt = await asyncio.to_thread(
            self.runtime.invoke,
            "web.search",
            {"query": normalized, "limit": MAX_SEARCH_RESULTS, "safesearch": 1},
        )
        self.runtime.persist()
        payload = dict(search_receipt.payload)
        if not payload.get("ok") or payload.get("capability_id") != "web.search":
            raise RuntimeWebEvidenceError("runtime:web.search", "runtime web.search failed")
        results = [row for row in (payload.get("results") or ()) if isinstance(row, dict)]
        if not results:
            return []
        available = set(self.runtime.available_capabilities())
        if "web.fetch" not in available:
            raise RuntimeWebEvidenceError(
                "runtime:web.fetch",
                "runtime web.fetch is required before search discoveries become factual evidence",
            )

        rows: list[dict[str, Any]] = []
        for rank, result in enumerate(results[:MAX_FETCH_PAGES], start=1):
            title = " ".join(str(result.get("title") or "").split())[:300]
            url = str(result.get("url") or "").strip()
            if not title or not url:
                continue
            try:
                fetch_receipt = await asyncio.to_thread(
                    self.runtime.invoke, "web.fetch", {"url": url, "max_chars": 12000}
                )
                self.runtime.persist()
                fetch_payload = dict(fetch_receipt.payload)
                page = dict(fetch_payload.get("page") or {})
                markdown = " ".join(str(page.get("markdown") or "").split())
                final_url = str(page.get("url") or url).strip()
                if (not fetch_payload.get("ok") or
                        fetch_payload.get("canonical_capability_id") != "web.fetch" or
                        not page.get("success") or len(markdown) < 200):
                    continue
            except Exception:
                continue
            summary = markdown[:MAX_EVIDENCE_CHARS]
            content_hash = digest(markdown)
            revision = digest({
                "contract": "runtime-web-fetched-evidence:v1",
                "search_receipt_id": search_receipt.receipt_id,
                "fetch_receipt_id": fetch_receipt.receipt_id,
                "url": final_url,
                "content_sha256": content_hash,
                "rank": rank,
            })
            rows.append({
                "title": title, "summary": summary, "url": final_url,
                "source": "runtime:web.fetch:" + str(fetch_receipt.provider),
                "source_revision": revision,
                "evidence_refs": [
                    search_receipt.receipt_id, fetch_receipt.receipt_id,
                    "web-url-sha256:" + digest(final_url),
                    "web-content-sha256:" + content_hash,
                ],
                "search_query": normalized, "search_rank": rank,
                "published_at": (str(result.get("published_at") or "").strip() or None),
                "observed_at": now.isoformat(),
            })
        if not rows:
            raise RuntimeWebEvidenceError(
                "runtime:web.fetch",
                "web search discovered URLs but Runtime web.fetch produced no readable evidence",
            )
        return rows


__all__ = [
    "MAX_SEARCH_RESULTS", "MAX_FETCH_PAGES", "MAX_EVIDENCE_CHARS",
    "RuntimeWebEvidenceError", "RuntimeWebSearch",
]
