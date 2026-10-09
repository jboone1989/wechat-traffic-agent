"""Legacy cycle identifiers resolve to fields on canonical article records."""
import json
from pathlib import Path
from wechat_traffic_agent.editorial.channel_records import ChannelRecords

NAMES = {"publication.json": "website_record", "x_social_expression.json": "x",
         "x_browser_publication.json": "x", "x_native_publication_verification.json": "x"}

def _coordinates(path):
    path = Path(path)
    folder = path.parent
    if folder.parent.name != "cycles":
        raise ValueError("publication record requires a canonical cycle")
    return folder.parents[3], folder.name, NAMES[path.name]

def read_cycle_record(path):
    root, cycle, channel = _coordinates(path)
    # Read-only compatibility for installations awaiting explicit migration.
    if Path(path).is_file():
        return json.loads(Path(path).read_text())
    if not (root / "content/automatic_website/articles.sqlite3").is_file():
        return {}
    row = ChannelRecords(root).get(channel, cycle)
    if row is None:
        return {}
    row = dict(row)
    row.pop("article_id", None)
    if channel == "x" and row.get("body") is not None:
        row["published_text"] = row["body"]
    return row

def write_cycle_record(path, value):
    root, cycle, channel = _coordinates(path)
    records = ChannelRecords(root)
    with records.transaction():
        previous = records.get(channel, cycle)
        website = records.get("website_record", cycle)
        article_id = (previous or {}).get("article_id") or value.get("artifact_id") or (website or {}).get("article_id") or "social-" + cycle
        fields = {**(previous or {}), **value, "cycle": cycle}
        if channel == "x" and (previous or {}).get("status") in {"POSTED", "ALREADY_EXPRESSED", "SUCCESS"} and value.get("status") == "PUBLICATION_UNVERIFIED":
            fields["status"] = previous["status"]
        records.put(article_id, channel, fields)
    Path(path).unlink(missing_ok=True)

def cycle_record_exists(path):
    if Path(path).is_file():
        return True
    root, cycle, channel = _coordinates(path)
    if not (root / "content/automatic_website/articles.sqlite3").is_file():
        return False
    return ChannelRecords(root).get(channel, cycle) is not None

def publication_paths(cycles_root):
    root = Path(cycles_root)
    paths = set(root.glob("*/publication.json"))
    if (root.parents[2] / "content/automatic_website/articles.sqlite3").is_file():
        paths.update(root / r["cycle"] / "publication.json"
                     for r in ChannelRecords(root.parents[2]).all("website_record") if r.get("cycle"))
    return sorted(paths)
