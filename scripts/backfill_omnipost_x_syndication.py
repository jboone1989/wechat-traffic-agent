#!/usr/bin/env python3
"""Attach omnipost's X cross-post receipts to Square drafts published before x_syndication existed.

omnipost publishes one job to binance_square and x together. Its ledger pairs the two
results by job_id; the Square draft only stored the binance_square ref. This script finds
the Square draft by that ref and records the X receipt so XOutcomeCollector can see it.
Dry run by default; pass --apply to write. The omnipost ledger is opened read-only.
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys
from datetime import timezone, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.editorial.channel_records import ChannelRecords  # noqa: E402

DEFAULT_LEDGER = "/opt/omnipost/state/ledger.db"


def ledger_pairs(ledger: Path) -> list[dict]:
    """(square_ref, x_ref, x_url, x_published_at) for jobs where both platforms succeeded."""
    db = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT job_id, platform, ref, url, updated_at FROM publishes WHERE status='ok'"
        ).fetchall()
    finally:
        db.close()
    jobs: dict[str, dict] = {}
    for job_id, platform, ref, url, updated_at in rows:
        jobs.setdefault(job_id, {})[platform] = {"ref": str(ref or ""), "url": str(url or ""),
                                                 "updated_at": str(updated_at or "")}
    pairs = []
    for job_id, platforms in sorted(jobs.items()):
        square, x = platforms.get("binance_square"), platforms.get("x")
        if square and x and square["ref"] and x["ref"] and x["url"]:
            published = datetime.fromisoformat(x["updated_at"]).replace(tzinfo=timezone.utc)
            pairs.append({"job_id": job_id, "square_ref": square["ref"], "x_ref": x["ref"],
                          "x_url": x["url"], "x_published_at": published.isoformat()})
    return pairs


def backfill(state_root: Path, pairs: list[dict], *, apply: bool) -> dict:
    records = ChannelRecords(state_root)
    attached, already, unmatched = [], 0, []
    with records.transaction():
        by_ref = {str(doc.get("post_id")): doc for doc in records.all("binance_square")
                  if doc.get("status") == "SUCCESS" and doc.get("post_id")}
        for pair in pairs:
            doc = by_ref.get(pair["square_ref"])
            if doc is None:
                unmatched.append(pair["job_id"])
                continue
            if doc.get("x_syndication"):
                already += 1
                continue
            doc["x_syndication"] = {"status": "SUCCESS", "post_id": pair["x_ref"], "url": pair["x_url"],
                                    "published_at": pair["x_published_at"],
                                    "backfilled_from": f"omnipost-ledger:{pair['job_id']}"}
            if apply:
                records.put(doc.get("article_id") or "square-" + doc["draft_id"], "binance_square", doc)
            attached.append({"draft_id": doc["draft_id"], "x_url": pair["x_url"]})
    return {"mode": "APPLY" if apply else "DRY_RUN", "ledger_pairs": len(pairs),
            "attached": len(attached), "already_attached": already,
            "unmatched_jobs": unmatched, "examples": attached[:5]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=os.environ.get("OMNIPOST_LEDGER", DEFAULT_LEDGER))
    parser.add_argument("--state-root", default=os.environ.get(
        "CONTENT_APPLICATION_ROOT", "/home/ubuntu/content-agent-state"))
    parser.add_argument("--apply", action="store_true", help="write receipts (default is a dry run)")
    args = parser.parse_args()
    result = backfill(Path(args.state_root), ledger_pairs(Path(args.ledger)), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
