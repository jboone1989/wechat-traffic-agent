#!/usr/bin/env python3
"""Inspect Binance Square readiness without exposing posting credentials."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path("/home/ubuntu/content-agent-state")


def main() -> int:
    flags = ROOT / ".binance-square-settings.json"
    data = json.loads(flags.read_text(encoding="utf-8")) if flags.exists() else {}
    credential = ROOT / ".binance-square-posting.key"
    from wechat_traffic_agent.publication.event_store import PublicationEventStore
    from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
    store = PublicationEventStore(ROOT)
    drafts = SquareDraftStore(ROOT).list()
    posted = [{"at": d.get("published_at") or (d.get("attempt_times") or [""])[-1],
               "post_id": d.get("post_id") or d.get("published_post_id"),
               "url": d.get("url") or d.get("published_url")}
              for d in drafts if d.get("status") == "SUCCESS"]
    posted.sort(key=lambda row: row.pop("at"))
    print(json.dumps({
        "key_saved": credential.is_file() and credential.stat().st_size > 0,
        "key_owner_only_permissions": (
            credential.is_file() and (credential.stat().st_mode & 0o077) == 0
        ),
        "consumer_enabled": bool(data.get("enabled")),
        "writes_enabled": bool(data.get("writes_enabled")),
        "publishing_mode": "NATIVE_REVIEWED_DRAFTS",
        "editorial_counts": {status: sum(d.get("status") == status for d in drafts)
                             for status in ("DRAFT", "REVIEWED", "NEEDS_REVIEW", "SUCCESS", "UNVERIFIED")},
        "event_count": len(store.list_events()),
        "success_count": len(posted),
        "latest_success": posted[-1] if posted else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
