"""T8: EventGraph events change research input, not just wake scheduling."""
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from wechat_traffic_agent.eventgraph_context import load_eventgraph_context, poll_new_stories
from wechat_traffic_agent.orchestration.content import ContentTaskMethods

STORY_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REF = "eventgraph:" + STORY_ID


def story(title="Shipping route closed"):
    return {
        "story": {
            "publicId": STORY_ID,
            "title": title,
            "digest": "Shipping volumes slowed after a route closure.",
            "latestAt": "2026-10-26T03:00:00Z",
            "reports": [
                {"id": "report-1", "title": title,
                 "source": {"name": "Port Authority", "firstParty": True},
                 "links": {"original": "https://example.org/port/statement"},
                 "publishedAt": "2026-10-26T03:00:00Z"},
            ],
        }
    }


class ContentHarness(ContentTaskMethods):
    def __init__(self):
        self.continuous_loop = SimpleNamespace(run_once=lambda **kw: {"status": "ALREADY_PROCESSED"})
        self.captured = None
        self.state_root = "/tmp/isolated-test"
        self.web_worker = None
        self.gsc_collector = None
        self.state_events = SimpleNamespace(emit=lambda *args, **kwargs: None)

    def _runtime_tick(self, now):
        return SimpleNamespace(generation_ready=True), {}
    def _maintain_content_site(self, rows, now): pass
    def _recover_social_stage_backlog(self, **kwargs): pass
    def _start_owner_content(self, rows, now, focus_topic, relevant_refs):
        return (focus_topic, {"goal_id": "daily-1"}, None, False, False, [], {})
    def _observe_site_editorial(self, rows, now): pass
    def _observe_search_console(self, rows, now): pass
    def _prepare_content_cycle(self, rows, now, *args):
        return ({}, "directed", "cycle", None, None)
    def _admit_content_resources(self, rows, now, *args): return {"approved": True}
    def _content_execution_options(self, rows, now, focus_topic, owner_goal, seed, *args):
        self.captured = {"topic": focus_topic, "seed": seed}
        return {}
    def _run_async(self, result): return result
    def _finish_content_recovery(self, *args): pass
    def _finish_content_mission(self, *args): pass
    def _emit_content_prediction(self, *args): pass
    def _settle_content_resources(self, *args): pass
    def _handle_content_nonpublication(self, *args): pass
    def _complete_content_owner(self, *args): pass


class EventGraphCausalContextTests(TestCase):
    def test_story_context_uses_original_source_and_topic(self):
        result = load_eventgraph_context((REF,), fetch=lambda _: story())
        self.assertEqual(result["topic"], "Shipping route closed")
        self.assertEqual(result["evidence"][0]["url"], "https://example.org/port/statement")
        self.assertEqual(result["evidence"][0]["evidence_refs"][0], REF)

    def test_context_rejects_bad_and_uncited_stories(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            load_eventgraph_context((REF,), fetch=lambda _: {"story": {"publicId": "bad"}})
        with self.assertRaisesRegex(ValueError, "no usable"):
            load_eventgraph_context((REF,), fetch=lambda _: {
                "story": {**story()["story"], "reports": []}
            })
        self.assertIsNone(load_eventgraph_context(("cog:unrelated",), fetch=lambda _: story()))

    def test_event_changes_research_input_under_daily_goal(self):
        with patch("wechat_traffic_agent.eventgraph_context.load_eventgraph_context",
                   side_effect=lambda refs: load_eventgraph_context(refs, fetch=lambda _: story())):
            harness = ContentHarness()
            now = datetime(2026, 10, 26, tzinfo=timezone.utc)
            harness._content(now, focus_topic=None, relevant_refs=("daily-goal:1", REF))
            self.assertEqual(harness.captured["topic"], "Shipping route closed")
            self.assertEqual(harness.captured["seed"][0]["evidence_refs"][0], REF)

            harness = ContentHarness()
            harness._content(now, focus_topic=None, relevant_refs=("daily-goal:1",))
            self.assertIsNone(harness.captured["topic"])
            self.assertEqual(harness.captured["seed"], [])

    def test_operator_focus_has_priority(self):
        with patch("wechat_traffic_agent.eventgraph_context.load_eventgraph_context",
                   side_effect=lambda refs: load_eventgraph_context(refs, fetch=lambda _: story())):
            harness = ContentHarness()
            harness._content(
                datetime(2026, 10, 26, tzinfo=timezone.utc),
                focus_topic="User-directed climate topic",
                relevant_refs=(REF,),
            )
            self.assertEqual(harness.captured["topic"], "User-directed climate topic")
            self.assertEqual(harness.captured["seed"], [])

    def test_new_hot_story_queues_once_and_requeues_only_on_actual_change(self):
        events = []
        bus = SimpleNamespace(emit=lambda kind, payload: events.append((kind, payload)))
        feed = {"items": [
            {"title": "Shipping route closed",
             "latestAt": "2026-10-26T03:00:00Z",
             "links": {"story": "https://example.org/stories/" + STORY_ID}}
        ]}
        with TemporaryDirectory() as folder:
            root = Path(folder)
            a = poll_new_stories(root, bus, fetch=lambda: feed)
            b = poll_new_stories(root, bus, fetch=lambda: feed)
            self.assertEqual(a["queued"], [REF])
            self.assertEqual(b["queued"], [])
            self.assertEqual(events[0][1]["evidence_ref"], REF)
            feed["items"][0]["latestAt"] = "2026-10-26T04:00:00Z"
            c = poll_new_stories(root, bus, fetch=lambda: feed)
            self.assertEqual(c["queued"], [REF])
            self.assertEqual(len(events), 2)

    def test_bad_feed_is_not_interpreted_as_no_new_events(self):
        with TemporaryDirectory() as folder:
            bus = SimpleNamespace(emit=lambda *args: None)
            with self.assertRaisesRegex(ValueError, "no items"):
                poll_new_stories(Path(folder), bus, fetch=lambda: {"error": "busy"})
