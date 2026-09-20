from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.website_engagement import (
    WebsiteEngagementOutcomeCollector,
    WebsiteEngagementStore,
    ingest_engagement_body,
)
from wechat_traffic_agent.website_engagement_client import first_party_engagement_script

T = datetime(2026, 9, 15, tzinfo=timezone.utc)


def _payload(event_id, visitor, session, event_type="page_view", route="/articles/a/", *, acquisition="unknown", device_class="unknown"):
    return {
        "schema_version": "website-engagement-event.v1",
        "event_id": event_id,
        "visitor_token": visitor,
        "session_token": session,
        "event_type": event_type,
        "route": route,
        "acquisition": acquisition,
        "device_class": device_class,
    }


def test_raw_browser_tokens_and_raw_audience_dimensions_are_never_persisted(tmp_path):
    store = WebsiteEngagementStore(tmp_path / "engagement")
    visitor, session = "1" * 32, "2" * 32
    result = ingest_engagement_body(
        store,
        json.dumps(_payload("a" * 32, visitor, session, acquisition="x", device_class="mobile")).encode(),
        observed_at=T,
    )
    assert result["status"] == "RECORDED"
    text = next((tmp_path / "engagement/events").glob("*/*.json")).read_text()
    assert visitor not in text and session not in text
    assert "visitor_hash" in text and "session_hash" in text
    assert '"acquisition": "x"' in text
    assert '"device_class": "mobile"' in text
    assert "https://x.com/some/private/path" not in text
    assert "Mozilla/5.0" not in text
    assert (store.key_path.stat().st_mode & 0o777) == 0o600


def test_legacy_payload_defaults_to_unknown_dimensions(tmp_path):
    store = WebsiteEngagementStore(tmp_path / "engagement")
    payload = {
        "schema_version": "website-engagement-event.v1",
        "event_id": "a" * 32,
        "visitor_token": "1" * 32,
        "session_token": "2" * 32,
        "event_type": "page_view",
        "route": "/",
    }
    assert ingest_engagement_body(store, json.dumps(payload).encode(), observed_at=T)["status"] == "RECORDED"
    row = store.events(start=T - timedelta(seconds=1), end=T + timedelta(seconds=1))[0]
    assert row.acquisition == "unknown"
    assert row.device_class == "unknown"


def test_duplicate_event_is_idempotent_and_conflict_fails(tmp_path):
    store = WebsiteEngagementStore(tmp_path / "engagement")
    body = json.dumps(_payload("a" * 32, "1" * 32, "2" * 32)).encode()
    assert ingest_engagement_body(store, body, observed_at=T)["status"] == "RECORDED"
    assert ingest_engagement_body(store, body, observed_at=T + timedelta(seconds=1))["status"] == "DUPLICATE"
    changed = json.dumps(_payload("a" * 32, "3" * 32, "2" * 32)).encode()
    try:
        ingest_engagement_body(store, changed, observed_at=T + timedelta(seconds=2))
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("conflicting event id must fail closed")


def test_precoverage_publication_is_unknown_not_zero(tmp_path):
    root = tmp_path / "state"
    store = WebsiteEngagementStore(root / "content/website_engagement")
    store.mark_started(observed_at=T + timedelta(days=2))
    cycle = root / "content/continuous/cycles/c1"
    cycle.mkdir(parents=True)
    publication = {
        "revision": "pub-1",
        "artifact_id": "a",
        "outcome_attribution": {
            "published_at": T.isoformat(),
            "public_route": "/articles/a/",
            "published_content_sha256": "a" * 64,
            "agent_candidate_sha256": "a" * 64,
            "topic": "agents",
            "title_version": "title:v1",
            "structure_version": "structure:v1",
            "strategy_version": "strategy:v1",
        },
    }
    (cycle / "publication.json").write_text(json.dumps(publication))
    report = WebsiteEngagementOutcomeCollector(state_root=root, account="https://livingruntime.com").collect(
        now=T + timedelta(days=8)
    )
    assert report["status"] == "INSUFFICIENT_ENGAGEMENT_COVERAGE"
    assert report["recorded"] == []


def test_browser_beacon_is_same_origin_cookie_free_and_only_sends_coarse_dimensions():
    script = first_party_engagement_script()
    assert "sendBeacon('/events'" in script
    assert "localStorage" in script and "sessionStorage" in script
    assert "document.cookie" not in script
    assert "u.origin!==location.origin" in script
    assert "engaged_30s" in script and "internal_link_click" in script
    assert "document.referrer" in script
    assert "navigator.userAgent" in script
    assert "acquisition,device_class" in script
    assert "referrer:" not in script
    assert "user_agent:" not in script
