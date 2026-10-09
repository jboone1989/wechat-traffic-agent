"""One X adapter: syndicate an audited website URL through the existing Jev session."""
from __future__ import annotations

import asyncio
from pathlib import Path

from wechat_traffic_agent.publication.models import PublicationEvent, DistributionTask


def x_share_text(event: PublicationEvent) -> str:
    """Link to the canonical article, never synthesize another research claim."""
    suffix = "\n\n" + event.canonical_url
    budget = 280 - len(suffix)
    if budget < 20:
        raise ValueError("canonical URL is too long for an X post")
    lead = " ".join((event.title + " — " + event.summary).split())
    if len(lead) > budget:
        lead = lead[: budget - 1].rsplit(" ", 1)[0].rstrip(" ,.;") + "…"
    return lead + suffix


def publish_x(event: PublicationEvent, task: DistributionTask, *, state_root: Path) -> dict:
    from wechat_traffic_agent.platforms.x.x_browser import XBrowserTransportRouter, x_browser_writes_enabled
    if task.channel != "x":
        raise ValueError("X adapter cannot publish other channels")
    if not x_browser_writes_enabled():
        raise RuntimeError("X publishing is disabled")
    from wechat_traffic_agent.editorial.channel_records import ChannelRecords
    records = ChannelRecords(state_root)
    text = x_share_text(event)
    with records.transaction():
        existing = records.get("x", task.distribution_id)
        if existing:
            records.put(event.artifact_id, "x", {**existing, "body": text})
    publication = asyncio.run(
        XBrowserTransportRouter(state_root=Path(state_root)).publish(text=text)
    )
    post_id = str(publication.post_id or "").strip()
    url = str(publication.url or "").strip()
    if not post_id or not url.startswith("https://x.com/") or post_id not in url:
        raise RuntimeError("Jev did not return a verified X post ID and URL")
    return {"status": "POSTED", "url": url, "post_id": post_id}
