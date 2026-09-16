"""X evidence source backed by Agent Runtime receipts and the official X MCP provider."""
from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Mapping


def _walk_posts(value: Any) -> list[dict[str, Any]]:
    """Find post-like rows without depending on one X response envelope shape."""
    if isinstance(value, list):
        direct = [dict(row) for row in value if isinstance(row, dict) and row.get("id") and row.get("text")]
        if direct:
            return direct
        for row in value:
            found = _walk_posts(row)
            if found:
                return found
        return []
    if isinstance(value, dict):
        data = value.get("data")
        if data is not None:
            found = _walk_posts(data)
            if found:
                return found
        for row in value.values():
            found = _walk_posts(row)
            if found:
                return found
    return []
def _walk_users(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and value.get("id") and (value.get("username") or value.get("name")):
        return [dict(value)]
    if isinstance(value, list):
        direct = [dict(row) for row in value if isinstance(row, dict) and row.get("id") and (row.get("username") or row.get("name"))]
        if direct:
            return direct
        for row in value:
            found = _walk_users(row)
            if found:
                return found
        return []
    if isinstance(value, dict):
        data = value.get("data")
        if data is not None:
            found = _walk_users(data)
            if found:
                return found
        for row in value.values():
            found = _walk_users(row)
            if found:
                return found
    return []


def first_user(value: Any) -> dict[str, Any] | None:
    rows = _walk_users(value)
    return rows[0] if rows else None


def first_post_id(value: Any) -> str | None:
    rows = _walk_posts(value)
    if rows:
        return str(rows[0].get("id") or "") or None
    if isinstance(value, dict):
        for key in ("id", "post_id", "tweet_id"):
            if value.get(key):
                return str(value[key])
        for row in value.values():
            found = first_post_id(row)
            if found:
                return found
    return None
def normalize_posts(value: Any, *, observed_at: datetime) -> list[dict[str, Any]]:
    posts = _walk_posts(value)
    rows: list[dict[str, Any]] = []
    for post in posts:
        post_id = str(post.get("id") or "").strip()
        text = " ".join(str(post.get("text") or "").split())
        if not post_id or not text:
            continue
        username = str(post.get("username") or "").strip().lstrip("@")
        supplied_url = str(post.get("url") or "").strip()
        url = supplied_url if supplied_url.startswith("https://x.com/") else (
            f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/web/status/{post_id}"
        )
        revision = hashlib.sha256((post_id + "\n" + text).encode("utf-8")).hexdigest()
        rows.append({
            "evidence_id": "x-post:" + post_id,
            "source": "X" + ((" @" + username) if username else ""),
            "source_kind": "social-x",
            "title": text[:180],
            "summary": text[:1000],
            "url": url,
            "source_revision": revision,
            "published_at": post.get("created_at"),
            "observed_at": observed_at.isoformat(),
            "evidence_refs": ("x-post:" + post_id,),
            "x_author_id": post.get("author_id"),
            "x_conversation_id": post.get("conversation_id"),
        })
    return rows


class RuntimeXSearch:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def search(self, *, query: str, now: datetime, max_results: int = 10) -> list[dict[str, Any]]:
        receipt = self.runtime.invoke("x.search", {"query": query, "max_results": max(10, min(int(max_results), 100))})
        payload = receipt.payload
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("status") or "X_SEARCH_FAILED"))
        rows = normalize_posts(payload.get("data"), observed_at=now)
        for row in rows:
            row["evidence_refs"] = tuple(dict.fromkeys((*row.get("evidence_refs", ()), receipt.receipt_id)))
            row["x_runtime_receipt_id"] = receipt.receipt_id
        return rows


__all__ = ["RuntimeXSearch", "first_post_id", "first_user", "normalize_posts"]
