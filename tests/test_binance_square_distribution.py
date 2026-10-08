"""Offline integration checks for the Binance Square publication channel."""
from datetime import datetime, timezone
import json

import httpx
import pytest

from wechat_traffic_agent.platforms.binance_square import (
    BinanceSquareClient, SquarePublicationUnverified, publish_square, square_share_text,
)
from wechat_traffic_agent.publication.dispatch import DurablePublicationDispatch
from wechat_traffic_agent.publication.event_store import DistributionWorker, PublicationEventStore
from wechat_traffic_agent.publication.models import PublicationEvent, DistributionTask


def event():
    return PublicationEvent(
        "event1", "artifact1", "https://example.org/btc-study", "BTC funding research",
        "The source-based comparison suggests leverage increased, not proof of causation.",
        ["BTC", "AI", "BTC"], datetime.now(timezone.utc), "sha256:sample",
    )


def test_square_message_preserves_research_source_and_deduplicates_coin_tags():
    message = square_share_text(event())
    assert message.count("$BTC") == 1
    assert "$AI" not in message
    assert "https://example.org/btc-study" in message
    assert "not proof of causation" in message


def test_opt_in_channels_stay_off_without_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTENT_BINANCE_SQUARE_ENABLED", raising=False)
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event())
    assert len(list(store.queue.glob("*.json"))) == 1
    assert next(store.queue.glob("*.json")).read_text().count('"channel": "x"') == 1


def test_square_creates_verified_receipt_and_keeps_x_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    captured = []
    def responder(request):
        assert request.headers["X-Square-OpenAPI-Key"] == "test-key"
        assert request.headers["clienttype"] == "binanceSkill"
        captured.append(request.content.decode())
        return httpx.Response(200, json={"code": "000000", "data": {"id": "298177291743282"}})
    client = BinanceSquareClient(key="test-key", client=httpx.Client(transport=httpx.MockTransport(responder)))
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event())
    assert len(list(store.queue.glob("*.json"))) == 2
    done = DistributionWorker(store, {"binance_square": lambda e, t: publish_square(e, t, client=client)}).run_once()
    assert done.status == "SUCCESS"
    assert done.published_post_id == "298177291743282"
    assert done.published_url == "https://www.binance.com/square/post/298177291743282"
    assert len(captured) == 1 and "$BTC" in captured[0]
    assert '"contentType":1' in captured[0] or '"contentType": 1' in captured[0]
    assert len(list(store.queue.glob("*.json"))) == 1
    assert DistributionWorker(store, {"binance_square": lambda e, t: None}).run_once() is None


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"code": "000000", "data": {}}),
    httpx.Response(504, json={"code": "000000", "data": {}}),
    httpx.Response(502, text="<html>bad gateway</html>"),
])
def test_ambiguous_result_is_not_retried(tmp_path, monkeypatch, response):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    attempts = []
    def responder(request):
        attempts.append(1)
        return response
    client = BinanceSquareClient(key="key", client=httpx.Client(transport=httpx.MockTransport(responder)))
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store, channels=("binance_square",)).record(event())
    worker = DistributionWorker(store, {"binance_square": lambda e, t: publish_square(e, t, client=client)})
    result = worker.run_once()
    assert result.status == "FAILED_PERMANENT"
    assert result.attempts == 1
    assert worker.run_once() is None
    assert attempts == [1]


def test_api_key_is_required_and_no_request_is_sent(monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    client = BinanceSquareClient(key="")
    with pytest.raises(Exception, match="OPENAPI_KEY"):
        publish_square(event(), DistributionTask("d", "event1", "binance_square"), client=client)


def test_http_timeout_cannot_trigger_second_post():
    def timeout(_):
        raise httpx.ReadTimeout("ambiguous")
    client = BinanceSquareClient(key="test-key", client=httpx.Client(transport=httpx.MockTransport(timeout)))
    with pytest.raises(SquarePublicationUnverified):
        client.post_text("BTC research")


def test_square_notification_is_not_counted_as_x_post(tmp_path):
    from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store, channels=("binance_square",)).record(event())
    claimed = store.claim_next(channels={"binance_square"})
    done = store.finish(claimed, result_url="https://www.binance.com/square/post/123",
                        result_post_id="123")
    goal = DailyPostingGoal(tmp_path)
    goal.config_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    goal.config_path.write_text(json.dumps({
        "enabled": True, "minimum_original_posts": 20,
        "timezone": "Asia/Shanghai", "activated_at": now.isoformat(),
        "notifications_enabled": True, "operator_state_dir": str(tmp_path / "operator"),
    }))
    assert goal.status(now)["attempted"] == 0
    goal.notify_distribution_result(task=done, now=now)
    goal.notify_distribution_result(task=done, now=now)
    notifications = list((tmp_path / "operator/mailbox/notifications").glob("*.json"))
    assert len(notifications) == 1
    notice = json.loads(notifications[0].read_text())
    assert notice["kind"] == "FERRO_SQUARE_POSTING"
    assert "币安广场" in notice["title"]
    assert done.published_url in notice["message"]


def test_disabled_square_remains_queued_for_later(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTENT_BINANCE_SQUARE_ENABLED", raising=False)
    monkeypatch.delenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", raising=False)
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store, channels=("binance_square",)).record(event())
    from wechat_traffic_agent.publication.channel_adapters import active_distribution_adapters
    adapters = active_distribution_adapters(tmp_path)
    assert "binance_square" not in adapters
    assert DistributionWorker(store, adapters).run_once() is None
    assert len(list(store.queue.glob("*.json"))) == 1
