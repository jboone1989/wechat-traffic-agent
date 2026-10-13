import importlib.util
import sqlite3
from pathlib import Path

from wechat_traffic_agent.editorial.channel_records import ChannelRecords

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backfill", ROOT / "scripts/backfill_omnipost_x_syndication.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def _ledger(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE publishes(job_id, platform, content_hash, status, url, ref, message, updated_at)")
    db.executemany("INSERT INTO publishes VALUES (?,?,?,?,?,?,?,?)", [
        ("j1", "binance_square", "h", "ok", "https://www.binance.com/square/post/111", "111", "", "2026-10-12 03:00:00"),
        ("j1", "x", "h", "ok", "https://x.com/a/status/901", "901", "", "2026-10-12 03:00:01"),
        ("j2", "binance_square", "h", "ok", "https://www.binance.com/square/post/222", "222", "", "2026-10-12 04:00:00"),
        ("j2", "x", "h", "failed", "", "", "too long", "2026-10-12 04:00:01"),
        ("j3", "binance_square", "h", "ok", "u", "333", "", "2026-10-12 05:00:00"),
        ("j3", "x", "h", "ok", "https://x.com/a/status/903", "903", "", "2026-10-12 05:00:01"),
    ])
    db.commit(); db.close()
    return path


def _square(root: Path, draft_id: str, post_id: str) -> None:
    records = ChannelRecords(root)
    with records.transaction():
        records.put("square-" + draft_id, "binance_square",
                    {"draft_id": draft_id, "status": "SUCCESS", "post_id": post_id, "body": "b"})


def test_dry_run_reports_without_writing_then_apply_attaches_once(tmp_path):
    pairs = backfill.ledger_pairs(_ledger(tmp_path / "ledger.db"))
    assert [p["job_id"] for p in pairs] == ["j1", "j3"]  # j2's X failed
    _square(tmp_path, "d1", "111")  # j3's Square draft is absent → unmatched

    dry = backfill.backfill(tmp_path, pairs, apply=False)
    assert dry["attached"] == 1 and dry["unmatched_jobs"] == ["j3"]
    assert "x_syndication" not in ChannelRecords(tmp_path).get("binance_square", "d1")

    applied = backfill.backfill(tmp_path, pairs, apply=True)
    receipt = ChannelRecords(tmp_path).get("binance_square", "d1")["x_syndication"]
    assert applied["attached"] == 1
    assert receipt["post_id"] == "901" and receipt["status"] == "SUCCESS"
    assert receipt["published_at"].endswith("+00:00")

    again = backfill.backfill(tmp_path, pairs, apply=True)
    assert again["attached"] == 0 and again["already_attached"] == 1
