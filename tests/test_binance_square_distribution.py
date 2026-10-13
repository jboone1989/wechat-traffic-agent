"""Binance Square is an independent PublicationEvent consumer, not an X feature."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

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





def test_producer_has_no_binance_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event())
    assert len(store.list_tasks()) == 1
    assert store.list_tasks()[0]["channel"] == "x_article"








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
    assert not any(t["channel"] == "binance_square" for t in store.list_tasks())
    assert len(store.list_tasks()) == 1





def test_subscriber_modules_do_not_import_x_or_cognition():
    root = Path(__file__).resolve().parents[1]
    for name in ("binance_square.py", "binance_square_consumer.py",
                 "binance_square_notifications.py"):
        source = (root / "src/wechat_traffic_agent/platforms" / name).read_text()
        assert "x_browser" not in source
        assert "daily_posting_goal" not in source
        assert "wakeful_runtime" not in source



def test_retired_consumer_never_queues_new_or_old_website_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    monkeypatch.setenv("BINANCE_SQUARE_OPENAPI_KEY", "test-key")
    store = PublicationEventStore(tmp_path)
    DurablePublicationDispatch(store).record(event(NOW - timedelta(days=1), "old"))
    DurablePublicationDispatch(store).record(event(NOW, "new"))
    consumer = SquareEventConsumer(tmp_path, publisher=lambda *a: pytest.fail("summary posted"))
    assert consumer.enqueue_events(now=NOW) == 0
    assert consumer.run_once(now=NOW) is None
    assert len(store.list_tasks()) == 2
    assert all(t["channel"] == "x_article" for t in store.list_tasks())
