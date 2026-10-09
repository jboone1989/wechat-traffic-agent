"""First-party publication outbox regression tests."""
import asyncio
import json
import pytest
from test_content_loop import NOW, setup
from wechat_traffic_agent.publication.models import PublicationEvent, DistributionTask
from wechat_traffic_agent.publication.event_store import PublicationEventStore, DistributionWorker


def test_audited_website_creates_one_canonical_event_and_x_task(tmp_path):
    _, loop, publisher = setup(tmp_path)
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert result["publication"]["audit"]["passed"] is True
    assert (publisher.public / "current" / "timeline" / "index.html").is_file()
    home = (publisher.public / "current" / "index.html").read_text()
    assert "Featured Research" in home and "What is being tested" in home
    store = PublicationEventStore(tmp_path / "state")
    events = store.list_events()
    pending = [t for t in store.list_tasks() if t["status"] in {"PENDING", "RETRY_PENDING"}]
    assert len(events) == len(pending) == 1
    event = events[0]
    task = {k: v for k, v in pending[0].items() if k != "article_id"}
    assert event["artifact_id"] == result["publication"]["artifact_id"]
    assert event["canonical_url"] == result["publication"]["site_url"]
    assert event["canonical_url"].startswith("https://")
    assert task["event_id"] == event["event_id"]
    assert task["status"] == "PENDING"
    store.enqueue_distribution(DistributionTask(**task))
    assert len([t for t in store.list_tasks() if t["status"] in {"PENDING", "RETRY_PENDING"}]) == 1
    assert asyncio.run(loop.run_once(now=NOW))["status"] == "ALREADY_PROCESSED"
    assert len(store.list_events()) == 1


def test_failed_website_check_keeps_outbox_empty(tmp_path):
    _, loop, publisher = setup(tmp_path, passes=False)
    before = (publisher.public / "current").resolve()
    assert asyncio.run(loop.run_once(now=NOW))["status"] == "HELD_BY_CHECKS"
    store = PublicationEventStore(tmp_path / "state")
    assert not store.list_events()
    assert not [t for t in store.list_tasks() if t["status"] in {"PENDING", "RETRY_PENDING"}]
    assert (publisher.public / "current").resolve() == before


def test_x_failure_retries_without_affecting_publication(tmp_path):
    _, loop, publisher = setup(tmp_path)
    assert asyncio.run(loop.run_once(now=NOW))["status"] == "PUBLISHED"
    before = (publisher.public / "current").resolve()
    store = PublicationEventStore(tmp_path / "state")
    def timeout(event, task):
        raise TimeoutError("X unavailable")
    failed = DistributionWorker(store, {"x": timeout}).run_once()
    assert failed.status == "RETRY_PENDING"
    assert failed.attempts == 1
    assert "TimeoutError" in failed.last_error
    assert len([t for t in store.list_tasks() if t["status"] in {"PENDING", "RETRY_PENDING"}]) == 1
    posted = DistributionWorker(store, {"x": lambda e, t: {"status": "POSTED", "url": "https://x.com/test/status/123"}}).run_once()
    assert posted.status == "SUCCESS"
    assert posted.attempts == 2
    assert not [t for t in store.list_tasks() if t["status"] in {"PENDING", "RETRY_PENDING"}]
    assert len([t for t in store.list_tasks() if t["status"] == "SUCCESS"]) == 1
    assert (publisher.public / "current").resolve() == before
    assert len(store.list_events()) == 1


def test_permanent_distribution_failure_does_not_delete_event(tmp_path):
    _, loop, _ = setup(tmp_path)
    assert asyncio.run(loop.run_once(now=NOW))["status"] == "PUBLISHED"
    store = PublicationEventStore(tmp_path / "state")
    fail = DistributionWorker(store, {"x": lambda e, t: {"status": "UNVERIFIED"}}, max_attempts=1).run_once()
    assert fail.status == "FAILED_PERMANENT"
    assert len([t for t in store.list_tasks() if t["status"] == "FAILED_PERMANENT"]) == len(store.list_events()) == 1


def test_private_and_relative_url_cannot_be_distributed(tmp_path):
    with pytest.raises(ValueError, match="absolute canonical"):
        PublicationEvent("id", "a", "/research/foo/", "title", "", [], NOW, "hash")
    store = PublicationEventStore(tmp_path)
    store.save_event(PublicationEvent("id", "a", "https://example.org/a/", "title", "", [], NOW, "hash", visibility="PRIVATE"))
    with pytest.raises(ValueError, match="private/unlisted"):
        store.enqueue_distribution(DistributionTask("id-x", "id", "x"))


def test_daily_distribution_attempts_replace_x_success_quota(tmp_path):
    from datetime import datetime, timezone
    from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
    _, loop, _ = setup(tmp_path)
    assert asyncio.run(loop.run_once(now=NOW))["status"] == "PUBLISHED"
    goal = DailyPostingGoal(tmp_path / "state")
    goal.config_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    goal.config_path.write_text(json.dumps({
        "enabled": True, "minimum_original_posts": 20,
        "timezone": "Asia/Shanghai", "activated_at": now.isoformat(),
        "notifications_enabled": False,
    }))
    assert goal.status(now)["metric"] == "DISTRIBUTION_ATTEMPTS"
    assert goal.status(now)["remaining"] == 20
    store = PublicationEventStore(tmp_path / "state")
    def timeout(event, task):
        raise TimeoutError("channel unavailable")
    assert DistributionWorker(store, {"x": timeout}).run_once().status == "RETRY_PENDING"
    status = goal.status(datetime.now(timezone.utc))
    assert status["attempted"] == 1
    assert status["published"] == 0
    assert status["remaining"] == 19


def test_x_adapter_uses_canonical_url_and_existing_browser_transport(tmp_path, monkeypatch):
    from wechat_traffic_agent.publication.x_distribution import x_share_text, publish_x
    from wechat_traffic_agent import x_browser
    event = PublicationEvent("id", "a", "https://example.org/research/example/", "Study " * 60,
                             "Only supported research findings.", ["AI"], NOW, "hash")
    message = x_share_text(event)
    assert event.canonical_url in message and len(message) <= 280
    called = []
    class Result:
        post_id = "321"
        url = "https://x.com/test/status/321"
    class Transport:
        def __init__(self, **kwargs):
            assert kwargs["state_root"] == tmp_path
        async def publish(self, *, text):
            called.append(text)
            return Result()
    monkeypatch.setattr(x_browser, "x_browser_writes_enabled", lambda: True)
    monkeypatch.setattr(x_browser, "XBrowserTransportRouter", Transport)
    row = publish_x(event, DistributionTask("x-id", "id", "x"), state_root=tmp_path)
    assert row["status"] == "POSTED"
    assert row["url"] == "https://x.com/test/status/321"
    assert called == [message]


def test_distribution_receipt_and_failure_notifications_are_deduplicated(tmp_path):
    from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
    goal = DailyPostingGoal(tmp_path / "state")
    goal.config_path.parent.mkdir(parents=True, exist_ok=True)
    goal.config_path.write_text(json.dumps({
        "enabled": True, "minimum_original_posts": 20,
        "timezone": "Asia/Shanghai", "activated_at": NOW.isoformat(),
        "notification_since": NOW.isoformat(),
        "notifications_enabled": True, "operator_state_dir": str(tmp_path / "operator"),
    }))
    task = DistributionTask("dist-1", "event-1", "x", "RETRY_PENDING", 1, "X timeout")
    first = goal.notify_distribution_result(task=task, now=NOW)
    second = goal.notify_distribution_result(task=task, now=NOW)
    assert first["key"] == second["key"]
    assert len(list((tmp_path / "operator" / "mailbox" / "notifications").glob("*.json"))) == 1
    done = DistributionTask("dist-1", "event-1", "x", "SUCCESS", 2, None, "https://x.com/ferro/status/123")
    goal.notify_distribution_result(task=done, now=NOW)
    assert len(list((tmp_path / "operator" / "mailbox" / "notifications").glob("*.json"))) == 2
