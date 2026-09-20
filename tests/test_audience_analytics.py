from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.audience_analytics import AudienceAnalytics
from wechat_traffic_agent.website_engagement import WebsiteEngagementStore, ingest_engagement_body


T = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def payload(event_id, visitor, session, route, *, acquisition="direct", device_class="desktop", event_type="page_view", target_route=None):
    row = {
        "schema_version": "website-engagement-event.v1",
        "event_id": event_id,
        "visitor_token": visitor,
        "session_token": session,
        "event_type": event_type,
        "route": route,
        "acquisition": acquisition,
        "device_class": device_class,
    }
    if target_route is not None:
        row["target_route"] = target_route
    return row


def record(store, row, at, *, audience_class="public"):
    return ingest_engagement_body(
        store,
        json.dumps(row).encode(),
        observed_at=at,
        audience_class=audience_class,
    )


def test_audience_snapshot_is_aggregate_and_tracks_real_funnel(tmp_path):
    root = tmp_path / "state"
    store = WebsiteEngagementStore(root / "content" / "website_engagement")
    store.mark_started(observed_at=T - timedelta(days=2))

    v1, v2 = "1" * 32, "2" * 32
    s1, s2, s3 = "a" * 32, "b" * 32, "c" * 32

    record(store, payload("0" * 32, v1, s1, "/", acquisition="x", device_class="mobile"), T - timedelta(hours=10))
    record(store, payload("1" * 32, v1, s1, "/agents/ferro/", acquisition="x", device_class="mobile"), T - timedelta(hours=9, minutes=50))
    record(store, payload("2" * 32, v1, s1, "/neural/", acquisition="x", device_class="mobile"), T - timedelta(hours=9, minutes=40))
    record(store, payload("3" * 32, v1, s1, "/talk/", acquisition="x", device_class="mobile"), T - timedelta(hours=9, minutes=30))
    record(store, payload("4" * 32, v1, s2, "/", acquisition="direct", device_class="desktop"), T - timedelta(hours=2))
    record(store, payload("5" * 32, v2, s3, "/", acquisition="google", device_class="desktop"), T - timedelta(hours=1))
    record(store, payload("6" * 32, v2, s3, "/articles/a/", acquisition="google", device_class="desktop", event_type="engaged_30s"), T - timedelta(minutes=30))
    record(
        store,
        payload("7" * 32, v2, s3, "/", acquisition="google", device_class="desktop", event_type="internal_link_click", target_route="/agents/ferro/"),
        T - timedelta(minutes=20),
    )

    record(
        store,
        payload("8" * 32, "3" * 32, "d" * 32, "/", acquisition="direct", device_class="desktop"),
        T - timedelta(minutes=10),
        audience_class="operator",
    )

    data = AudienceAnalytics(root).snapshot(window="24h", now=T)
    assert data["status"] == "OK"
    assert data["summary"]["visitors"] == 2
    assert data["summary"]["pageviews"] == 6
    assert data["summary"]["pageview_source"] == "FIRST_PARTY"
    assert data["summary"]["http_pageviews"] is None
    assert data["summary"]["first_party_pageviews"] == 6
    assert data["summary"]["sessions"] == 3
    assert data["summary"]["returning_visitors"] == 1
    assert data["summary"]["engaged_readers_30s"] == 1
    assert data["summary"]["internal_link_clicks"] == 1
    assert data["summary"]["excluded_operator_events"] == 1

    pages = {row["route"]: row["pageviews"] for row in data["top_pages"]}
    assert pages["/"] == 3
    assert pages["/agents/ferro/"] == 1
    assert {row["name"]: row["count"] for row in data["acquisition"]}["x"] == 3
    assert {row["name"]: row["count"] for row in data["devices"]}["mobile"] == 3
    assert data["funnel"] == [
        {"stage": "Visit", "visitors": 2},
        {"stage": "View Ferro", "visitors": 1},
        {"stage": "Open Neural Life", "visitors": 1},
        {"stage": "Talk", "visitors": 1},
    ]

    serialized = json.dumps(data)
    assert "1" * 64 not in serialized
    assert "2" * 64 not in serialized
    assert data["privacy"]["raw_ip_persisted"] is False
    assert data["privacy"]["raw_user_agent_persisted"] is False
    assert data["privacy"]["raw_referrer_persisted"] is False
    assert data["privacy"]["visitor_level_rows_exposed"] is False
    assert data["privacy"]["known_operator_browser_events_excluded"] is True
    assert data["privacy"]["historical_unclassified_events_may_include_operator"] is True


def test_audience_snapshot_uses_existing_nginx_history_before_first_party_coverage(tmp_path):
    root = tmp_path / "state"
    log = tmp_path / "access.log"
    log.write_text(
        '\n'.join([
            '203.0.113.10 - - [20/Sep/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 100 "-" "Mozilla/5.0"',
            '203.0.113.11 - - [20/Sep/2026:10:05:00 +0000] "GET / HTTP/1.1" 200 100 "-" "Mozilla/5.0"',
            '203.0.113.12 - - [20/Sep/2026:10:10:00 +0000] "GET /agents/ferro/ HTTP/1.1" 200 100 "-" "Mozilla/5.0"',
            '203.0.113.13 - - [20/Sep/2026:10:15:00 +0000] "GET / HTTP/1.1" 200 100 "-" "Googlebot/2.1"',
            '203.0.113.14 - - [20/Sep/2026:10:20:00 +0000] "GET /missing HTTP/1.1" 404 10 "-" "Mozilla/5.0"',
        ]) + '\n',
        encoding="utf-8",
    )

    data = AudienceAnalytics(root, access_log=log).snapshot(window="24h", now=T)
    assert data["status"] == "HTTP_ONLY"
    assert data["summary"]["pageviews"] == 3
    assert data["summary"]["pageview_source"] == "NGINX_HTTP"
    assert data["summary"]["http_pageviews"] == 3
    assert data["summary"]["first_party_pageviews"] == 0
    assert data["summary"]["visitors"] == 0
    assert data["top_pages"][:2] == [
        {"route": "/", "pageviews": 2},
        {"route": "/agents/ferro/", "pageviews": 1},
    ]
    assert data["http"]["status"] == "OK"
    assert data["first_party"]["status"] == "NO_COVERAGE"
    serialized = json.dumps(data)
    assert "203.0.113." not in serialized
    assert "Mozilla/5.0" not in serialized
    assert "Googlebot" not in serialized


def test_audience_snapshot_reports_missing_coverage_truthfully(tmp_path):
    data = AudienceAnalytics(tmp_path / "state").snapshot(window="7d", now=T)
    assert data["status"] == "NO_COVERAGE"
    assert data["coverage_partial"] is True
    assert data["summary"]["visitors"] == 0
    assert data["top_pages"] == []


def test_audience_window_is_validated(tmp_path):
    try:
        AudienceAnalytics(tmp_path / "state").snapshot(window="all-time", now=T)
    except ValueError as exc:
        assert "window must be one of" in str(exc)
    else:
        raise AssertionError("unsupported analytics windows must fail closed")
