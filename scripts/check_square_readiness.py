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
    event_dir = ROOT / "publication" / "events"
    success_dir = ROOT / "distribution" / "success"
    posted = []
    for path in success_dir.glob("*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("channel") == "binance_square" and item.get("status") == "SUCCESS":
            posted.append({"at": (item.get("attempt_times") or [""])[-1], "post_id": item.get("published_post_id"),
                           "url": item.get("published_url")})
    research_note = ROOT / "distribution" / "binance_research_note_20261009.json"
    note = json.loads(research_note.read_text()) if research_note.exists() else {}
    if note.get("status") == "SUCCESS":
        posted.append({"at": note.get("published_at", ""), "post_id": note.get("post_id"), "url": note.get("url")})
    drafts = [json.loads(path.read_text()) for path in (ROOT / "square_editorial" / "drafts").glob("*.json")]
    for draft in drafts:
        if draft.get("status") == "SUCCESS":
            posted.append({"at": draft.get("published_at", ""), "post_id": draft.get("post_id"),
                           "url": draft.get("url")})
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
        "event_count": len(list(event_dir.glob("*.json"))),
        "success_count": len(posted),
        "latest_success": posted[-1] if posted else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
