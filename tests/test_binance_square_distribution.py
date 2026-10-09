"""Binance Square is an independent PublicationEvent consumer, not an X feature."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest

from wechat_traffic_agent.platforms.binance_square import (
    BinanceSquareClient, SquarePublicationUnverified, publish_square, square_share_text,
)
from wechat_traffic_agent.platforms.binance_square_consumer import SquareEventConsumer
from wechat_traffic_agent.platforms.binance_square_notifications import notify_square_distribution
from wechat_traffic_agent.publication.dispatch import DurablePublicationDispatch
from wechat_traffic_agent.publication.event_store import DistributionWorker, PublicationEventStore
from wechat_traffic_agent.publication.models import PublicationEvent, DistributionTask


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def event(when=NOW, event_id="event1"):
    return PublicationEvent(
        event_id, "artifact-" + event_id, "https://example.org/btc-study", "BTC funding research",
        "The source-based comparison suggests leverage increased, not proof of causation.",
        ["BTC", "AI", "BTC"], when, "sha256:sample",
    )


def mock_client(responder):
    return BinanceSquareClient(key="test-key", client=httpx.Client(
        transport=httpx.MockTransport(responder),
    ))


def test_square_text_preserves_source_without_inventing_tags():
    text = square_share_text(event())
    assert text.count("$BTC") == 1 and "$AI" not in text
    assert "https://example.org/btc-study" in text
    assert "not proof of causation" in text


def test_producer_has_no_binance_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event())
    assert len(list(store.queue.glob("*.json"))) == 1
    assert json.loads(next(store.queue.glob("*.json")).read_text())["channel"] == "x"


def test_subscriber_consumes_new_event_without_x_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    monkeypatch.setenv("BINANCE_SQUARE_OPENAPI_KEY", "test-key")
    sent = []
    def responder(request):
        assert request.headers["X-Square-OpenAPI-Key"] == "test-key"
        body = json.loads(request.content)
        assert body["contentType"] == 1
        assert "$BTC" in body["bodyTextOnly"]
        sent.append(body)
        return httpx.Response(200, json={"code": "000000", "data": {"id": "298177291743282"}})
    client = mock_client(responder)
    consumer = SquareEventConsumer(tmp_path, publisher=lambda e, t: publish_square(e, t, client=client))
    consumer.enqueue_events(now=NOW - timedelta(minutes=1))  # subscribe first
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event())
    done = consumer.run_once()
    assert done.status == "SUCCESS"
    assert done.channel == "binance_square"
    assert done.published_post_id == "298177291743282"
    assert done.published_url == "https://www.binance.com/square/post/298177291743282"
    assert len(sent) == 1
    assert consumer.run_once() is None
    assert len(sent) == 1
    assert len(list(store.queue.glob("*.json"))) == 1  # X queue untouched


def test_activating_consumer_never_backfills_old_articles(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.delenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", raising=False)
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event(NOW - timedelta(days=1)))
    consumer = SquareEventConsumer(tmp_path)
    assert consumer.run_once(now=NOW) is None
    assert not any("binance_square" in path.read_text() for path in store.queue.glob("*.json"))
    DurablePublicationDispatch(store).record(event(NOW + timedelta(seconds=1), "fresh"))
    assert consumer.enqueue_events(now=NOW + timedelta(days=1)) == 1
    assert consumer.enqueue_events(now=NOW + timedelta(days=1)) == 0


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"code": "000000", "data": {}}),
    httpx.Response(504, json={"code": "000000", "data": {}}),
    httpx.Response(502, text="<html>bad gateway</html>"),
])
def test_ambiguous_receipt_is_not_resubmitted(tmp_path, monkeypatch, response):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    monkeypatch.setenv("BINANCE_SQUARE_OPENAPI_KEY", "test-key")
    attempts = []
    def responder(_):
        attempts.append(1)
        return response
    client = mock_client(responder)
    consumer = SquareEventConsumer(tmp_path, publisher=lambda e, t: publish_square(e, t, client=client))
    consumer.enqueue_events(now=NOW - timedelta(minutes=1))
    PublicationEventStore(tmp_path).save_event(event())
    result = consumer.run_once()
    assert result.status == "FAILED_PERMANENT"
    assert result.attempts == 1
    assert consumer.run_once() is None
    assert attempts == [1]


def test_square_failure_does_not_modify_x_goal(tmp_path):
    from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
    store = PublicationEventStore(tmp_path)
    store.save_event(event())
    store.enqueue_distribution(DistributionTask("s1", "event1", "binance_square"))
    claimed = store.claim_next(channels={"binance_square"})
    done = store.finish(claimed, result_url="https://www.binance.com/square/post/123",
                        result_post_id="123")
    goal = DailyPostingGoal(tmp_path)
    goal.config_path.parent.mkdir(parents=True, exist_ok=True)
    goal.config_path.write_text(json.dumps({
        "enabled": True, "minimum_original_posts": 20, "timezone": "Asia/Shanghai",
        "activated_at": NOW.isoformat(), "notifications_enabled": False,
    }))
    assert goal.status(NOW)["attempted"] == 0
    notify_square_distribution(done, operator_root=tmp_path / "operator", now=NOW)
    notify_square_distribution(done, operator_root=tmp_path / "operator", now=NOW + timedelta(minutes=2))
    paths = list((tmp_path / "operator/mailbox/notifications").glob("*.json"))
    assert len(paths) == 1
    notice = json.loads(paths[0].read_text())
    assert notice["kind"] == "FERRO_SQUARE_POSTING"
    assert "币安广场" in notice["title"]
    assert done.published_url in notice["message"]


def test_square_disabled_does_not_claim_events_or_x_tasks(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTENT_BINANCE_SQUARE_ENABLED", raising=False)
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event())
    assert SquareEventConsumer(tmp_path).run_once() is None
    assert not (store.distribution / "subscriptions/binance_square.json").exists()
    assert len(list(store.queue.glob("*.json"))) == 1


def test_missing_key_never_calls_square_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    monkeypatch.delenv("BINANCE_SQUARE_OPENAPI_KEY", raising=False)
    consumer = SquareEventConsumer(tmp_path, publisher=lambda e, t: pytest.fail("called without key"))
    consumer.enqueue_events(now=NOW - timedelta(minutes=1))
    PublicationEventStore(tmp_path).save_event(event())
    assert consumer.run_once() is None
    assert len(list(PublicationEventStore(tmp_path).queue.glob("*.json"))) == 1


def test_http_timeout_is_explicitly_unverified():
    def timeout(_):
        raise httpx.ReadTimeout("ambiguous")
    with pytest.raises(SquarePublicationUnverified):
        mock_client(timeout).post_text("BTC research")


def test_subscriber_modules_do_not_import_x_or_cognition():
    root = Path(__file__).resolve().parents[1]
    for name in ("binance_square.py", "binance_square_consumer.py",
                 "binance_square_notifications.py"):
        source = (root / "src/wechat_traffic_agent/platforms" / name).read_text()
        assert "x_browser" not in source
        assert "daily_posting_goal" not in source
        assert "wakeful_runtime" not in source
