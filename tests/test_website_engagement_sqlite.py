"""Website engagement event storage keeps dedup and time filtering in SQLite."""
from datetime import datetime, timedelta, timezone
import json

from test_website_engagement import T, _payload
from wechat_traffic_agent.website_engagement import (
    WebsiteEngagementEvent, WebsiteEngagementStore, ingest_engagement_body,
)


def test_new_events_have_no_per_event_json_files(tmp_path):
    store = WebsiteEngagementStore(tmp_path / "engagement")
    body = json.dumps(_payload("a"*32, "1"*32, "2"*32)).encode()
    assert ingest_engagement_body(store, body, observed_at=T)["status"] == "RECORDED"
    assert (store.root / "events.sqlite3").is_file()
    assert not list(store.events_root.glob("*/*.json"))
    assert ingest_engagement_body(store, body, observed_at=T + timedelta(days=2))["status"] == "DUPLICATE"
    assert len(store.events(start=T, end=T + timedelta(days=1))) == 1
    assert store.events(start=T + timedelta(days=1), end=T + timedelta(days=3)) == []


def test_legacy_event_import_is_queryable_after_original_removed(tmp_path):
    root = tmp_path / "state/content/website_engagement"
    date = T.strftime("%Y-%m-%d")
    legacy = root / "events" / date / ("a"*32 + ".json")
    legacy.parent.mkdir(parents=True)
    event = WebsiteEngagementEvent(
        event_id="a"*32, visitor_hash="b"*64, session_hash="c"*64,
        route="/articles/history/", event_type="page_view", observed_at=T,
    )
    legacy.write_text(event.model_dump_json())
    store = WebsiteEngagementStore(root)
    assert store.migrate_legacy_events() == {"events": 1, "legacy_files_seen": 1}
    assert store.events(start=T, end=T + timedelta(seconds=1)) == [event]
    legacy.unlink()
    assert store.migrate_legacy_events() == {"events": 1, "legacy_files_seen": 0}
    assert store.events(start=T, end=T + timedelta(seconds=1)) == [event]


def test_indexed_event_conflict_never_overwrites_record(tmp_path):
    store = WebsiteEngagementStore(tmp_path / "engagement")
    body = json.dumps(_payload("a"*32, "1"*32, "2"*32)).encode()
    assert ingest_engagement_body(store, body, observed_at=T)["status"] == "RECORDED"
    conflict = json.dumps(_payload("a"*32, "3"*32, "2"*32)).encode()
    try:
        ingest_engagement_body(store, conflict, observed_at=T)
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("same event identity must not overwrite data")
