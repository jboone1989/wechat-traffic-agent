from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.website_engagement import (
    WebsiteEngagementOutcomeCollector,
    WebsiteEngagementStore,
    ingest_engagement_body,
)
from wechat_traffic_agent.website_engagement_client import first_party_engagement_script

T = datetime(2026, 9, 15, tzinfo=timezone.utc)


def _payload(event_id, visitor, session, event_type="page_view", route="/articles/a/"):
    return {
        "schema_version": "website-engagement-event.v1",
        "event_id": event_id,
        "visitor_token": visitor,
        "session_token": session,
        "event_type": event_type,
        "route": route,
    }


def test_raw_browser_tokens_are_never_persisted(tmp_path):
    store = WebsiteEngagementStore(tmp_path / "engagement")
    visitor, session = "1" * 32, "2" * 32
    result = ingest_engagement_body(
        store, json.dumps(_payload("a" * 32, visitor, session)).encode(), observed_at=T
    )
    assert result["status"] == "RECORDED"
    text = next((tmp_path / "engagement/events").glob("*/*.json")).read_text()
    assert visitor not in text and session not in text
    assert "visitor_hash" in text and "session_hash" in text
    assert (store.key_path.stat().st_mode & 0o777) == 0o600


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


def test_browser_beacon_is_same_origin_and_cookie_free():
    script = first_party_engagement_script()
    assert "sendBeacon('/events'" in script
    assert "localStorage" in script and "sessionStorage" in script
    assert "document.cookie" not in script
    assert "u.origin!==location.origin" in script
    assert "engaged_30s" in script and "internal_link_click" in script
