"""Publication outbox is a swappable boundary; website knows no X transport."""
from __future__ import annotations

import asyncio

from test_content_loop import NOW, setup
from wechat_traffic_agent.publication.dispatch import DurablePublicationDispatch
from wechat_traffic_agent.publication.event_store import PublicationEventStore
from wechat_traffic_agent.publication.models import PublicationEvent


def test_outbound_channels_are_chosen_by_distribution_not_website(tmp_path):
    store = PublicationEventStore(tmp_path)
    event = PublicationEvent(
        "new", "a", "https://example.com/articles/a/", "A study", "Summary",
        ["AI"], NOW, "sha256:example",
    )
    dispatch = DurablePublicationDispatch(store, channels=("x", "bilibili"))
    dispatch.record(event)
    dispatch.record(event)
    tasks = store.list_tasks()
    assert len(tasks) == 2
    assert {store.load_event("new").event_id} == {"new"}


def test_website_publisher_can_emit_to_external_event_sink(tmp_path):
    _, loop, publisher = setup(tmp_path)
    received = []
    publisher.publication_sink = received.append
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert len(received) == 1
    event = received[0]
    assert isinstance(event, PublicationEvent)
    assert event.artifact_id == result["publication"]["artifact_id"]
    # No local outbox is created when the feature is wired to an external sink.
    store = PublicationEventStore(tmp_path / "state")
    assert not store.list_events()
    assert not store.list_tasks()
