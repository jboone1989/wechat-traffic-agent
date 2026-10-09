#!/usr/bin/env python3
"""Import publication metadata into existing article rows, then remove verified duplicates."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from wechat_traffic_agent.publication.cycle_records import NAMES

def migrate(root, *, apply=False):
    root = Path(root)
    planned = []
    cycles = root / "content/continuous/cycles"
    # First bind each cycle to its canonical website article.
    publications = [(p, json.loads(p.read_text())) for p in sorted(cycles.glob("*/publication.json"))]
    cycle_ids = {p.parent.name: v.get("artifact_id") or "social-" + p.parent.name for p, v in publications}
    events = [(p, json.loads(p.read_text())) for p in sorted((root / "publication/events").glob("*.json"))]
    event_ids = {v["event_id"]: v["artifact_id"] for _, v in events}
    for p, v in publications:
        planned.append((p, cycle_ids[p.parent.name], "website_record", {**v, "cycle": p.parent.name}))
    for p, v in events:
        planned.append((p, v["artifact_id"], "website", v))
    for status in ("queue", "processing", "success", "failed"):
        for p in sorted((root / "distribution" / status).glob("*.json")):
            v = json.loads(p.read_text())
            if not v.get("distribution_id"):
                continue
            article_id = event_ids.get(v["event_id"])
            if not article_id:
                raise ValueError("distribution has no canonical article: " + str(p))
            if v.get("status") == "SUCCESS":
                v.setdefault("published_at", (v.get("attempt_times") or [datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()])[-1])
            planned.append((p, article_id, v["channel"], v))
    for name, channel in NAMES.items():
        if name == "publication.json":
            continue
        for p in sorted(cycles.glob("*/" + name)):
            v = json.loads(p.read_text())
            planned.append((p, cycle_ids.get(p.parent.name, "social-" + p.parent.name), channel, {**v, "cycle": p.parent.name}))
    for p in sorted((root / "square_editorial/drafts").glob("*.json")):
        v = json.loads(p.read_text())
        cycle = (v.get("research_provenance") or {}).get("learning_cycle")
        article_id = v.get("article_id") or cycle_ids.get(cycle) or "square-" + v["draft_id"]
        planned.append((p, article_id, "binance_square", v))
    note = root / "distribution/binance_research_note_20261009.json"
    if note.is_file():
        v = json.loads(note.read_text())
        planned.append((note, "square-legacy-" + str(v.get("post_id") or "web3-note"), "binance_square", v))
    for p in sorted((root / "square_editorial/pending_website").glob("*.json")):
        v = json.loads(p.read_text())
        article_id = (v.get("publication") or {}).get("artifact_id") or cycle_ids.get(v.get("cycle"))
        if not article_id:
            raise ValueError("Square preparation has no article: " + str(p))
        planned.append((p, article_id, "square_preparation", v))
    for p in sorted(cycles.glob("*/square_website_syndication.json")):
        v = json.loads(p.read_text())
        article_id = (v.get("website_publication") or {}).get("artifact_id") or cycle_ids.get(p.parent.name)
        if article_id:
            compact = {k: v.get(k) for k in ("status", "cycle", "draft_id", "draft_status", "rationale")}
            planned.append((p, article_id, "square_preparation", compact))
    if not apply:
        return {"planned_records": len(planned), "applied": False}
    records = ChannelRecords(root)
    verified = []
    with records.transaction():
        db = records.connection
        site_before = db.execute("SELECT count(*) FROM articles WHERE record_type='website'").fetchone()[0]
        for p, article_id, channel, value in planned:
            previous = records.get(channel, article_id) or {}
            fields = {**previous, **value}
            if channel == "website":
                row = db.execute("SELECT record_type FROM articles WHERE artifact_id=?", (article_id,)).fetchone()
                if row and row[0] == "website":
                    for key in ("title", "summary", "topics"):
                        fields.pop(key, None)
            if channel == "x" and previous.get("status") in {"POSTED", "ALREADY_EXPRESSED", "SUCCESS"} and value.get("status") == "PUBLICATION_UNVERIFIED":
                fields["status"] = previous["status"]
            records.put(article_id, channel, fields)
            stored = records.get(channel, article_id)
            for key in ("event_id", "draft_id", "post_id", "published_post_id"):
                if value.get(key) is not None and stored.get(key) != value[key]:
                    raise ValueError("migration identity mismatch: " + str(p))
            verified.append(p)
        if db.execute("SELECT count(*) FROM articles WHERE record_type='website'").fetchone()[0] != site_before:
            raise ValueError("migration changed website article inventory")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("article database integrity check failed")
    history = root / "content/x/history.jsonl"
    if history.is_file():
        lines = history.read_text().splitlines()
        compact = []
        for line in lines:
            value = json.loads(line)
            if value.get("event_type") == "EXPRESSION" and value.get("x_post_id") and value.get("cycle"):
                prior = records.get("x", value["cycle"])
                if prior and str(prior.get("post_id") or prior.get("published_post_id")) == str(value["x_post_id"]):
                    if value.get("text") and not prior.get("body"):
                        records.put(prior["article_id"], "x", {**prior, "body": value["text"]})
                    value.pop("text", None)
                    value["article_id"] = prior["article_id"]
            compact.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        temp = history.with_suffix(".migration.tmp")
        temp.write_text("\n".join(compact) + "\n")
        temp.replace(history)
    # Delete only after the complete import transaction has committed.
    for p in verified:
        p.unlink()
    return {"applied": True, "imported_records": len(verified), "website_articles": site_before,
            "square_success": sum(r.get("status") == "SUCCESS" for r in records.all("binance_square"))}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.state_root, apply=args.apply)))
