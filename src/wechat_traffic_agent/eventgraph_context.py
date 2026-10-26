"""Evidence-bound context from EventGraph's existing public story API.

Never treats a neural trigger id alone as a topic or a verified citation.
Only explicit eventgraph:<public-id> references select a story.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

_STORY_ID = re.compile(
    r"^eventgraph:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def is_eventgraph_ref(value: str) -> bool:
    return _STORY_ID.fullmatch(str(value).strip()) is not None


def load_eventgraph_context(
    relevant_refs: tuple[str, ...],
    *,
    base_url: str | None = None,
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    ids = []
    for ref in relevant_refs:
        matched = _STORY_ID.fullmatch(str(ref).strip())
        if matched and matched.group(1).lower() not in ids:
            ids.append(matched.group(1).lower())
    if not ids:
        return None

    if len(ids) > 1:
        raise ValueError("multiple EventGraph stories must have separate cognition wakes")
    story_id = ids[0]
    if fetch is None:
        url = (base_url or os.environ.get("EVENTGRAPH_API_BASE_URL") or "").strip()
        if not url:
            raise RuntimeError("EVENTGRAPH_API_BASE_URL is required for EventGraph-triggered cognition")
        import httpx
        def fetch(identifier: str) -> dict[str, Any]:
            response = httpx.get(
                url.rstrip("/") + "/api/v1/stories/" + identifier,
                timeout=12.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.json()

    document = fetch(story_id)
    story = document.get("story") if isinstance(document, dict) else None
    if not isinstance(story, dict) or str(story.get("publicId") or "").lower() != story_id:
        raise ValueError("EventGraph response does not match the requested public story id")
    topic = str(story.get("title") or "").strip()
    if not topic:
        raise ValueError("EventGraph story has no title")

    evidence = []
    for report in list(story.get("reports") or [])[:20]:
        if not isinstance(report, dict):
            continue
        links = report.get("links") or {}
        original = str(links.get("original") or "").strip()
        title = str(report.get("title") or "").strip()
        report_id = str(report.get("id") or "").strip()
        summary = str(report.get("summary") or story.get("digest") or "").strip()
        if (not title or not report_id or not summary
                or not original.startswith(("https://", "http://"))):
            continue
        evidence_id = "eventgraph:" + story_id + ":report:" + report_id
        evidence.append({
            "evidence_id": evidence_id,
            "source_kind": "EVENTGRAPH_STORY",
            "source": str((report.get("source") or {}).get("name") or "EventGraph"),
            "title": title,
            "summary": summary,
            "url": original,
            "published_at": report.get("publishedAt"),
            "source_revision": str(story.get("latestAt") or evidence_id),
            "evidence_refs": ["eventgraph:" + story_id, original],
        })
    if not evidence:
        raise ValueError("EventGraph story has no usable source-linked report")
    return {
        "story_ref": "eventgraph:" + story_id,
        "topic": topic,
        "evidence": evidence,
        "source_count": len(evidence),
    }


def poll_new_stories(
    state_root: Path,
    bus: Any,
    *,
    base_url: str | None = None,
    fetch: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Queue changed EventGraph hot stories without spending a model call.

    One-shot by design: the host can invoke this on its existing schedule.
    A local checkpoint avoids re-queueing unchanged stories on every poll.
    """
    if fetch is None:
        url = (base_url or os.environ.get("EVENTGRAPH_API_BASE_URL") or "").strip()
        if not url:
            raise RuntimeError("EVENTGRAPH_API_BASE_URL is required to poll EventGraph")
        import httpx
        def fetch() -> dict[str, Any]:
            response = httpx.get(url.rstrip("/") + "/api/v1/hot-topics", timeout=12.0)
            response.raise_for_status()
            return response.json()

    feed = fetch()
    if not isinstance(feed, dict) or not isinstance(feed.get("items"), list):
        raise ValueError("EventGraph hot topics response has no items list")
    path = Path(state_root) / "application" / "eventgraph_seen.json"
    seen = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(seen, dict):
        raise ValueError("EventGraph checkpoint must be a JSON object")
    queued = []
    for row in feed["items"]:
        if not isinstance(row, dict):
            continue
        story_link = str((row.get("links") or {}).get("story") or "")
        public_id = story_link.rstrip("/").split("/")[-1].lower()
        ref = "eventgraph:" + public_id
        latest = str(row.get("latestAt") or "").strip()
        if not is_eventgraph_ref(ref) or not latest or seen.get(public_id) == latest:
            continue
        bus.emit(
            "NETWORK_EVIDENCE",
            {"evidence_ref": ref, "eventgraph_title": str(row.get("title") or "")[:500]},
        )
        seen[public_id] = latest
        queued.append(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(seen, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    return {"status": "QUEUED" if queued else "UNCHANGED", "queued": queued}
