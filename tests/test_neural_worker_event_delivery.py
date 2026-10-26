"""Exercise the actual neural-worker event handler and durable event bus.

No VirtualBrain bootstrap or production state: deliberately inject boundary
collaborators and use real Ferro event and wake stores in tmp_path.
"""
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.state_events import FerroStateEventBus
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run_ferro_neural_worker.py"
spec = spec_from_file_location("_test_ferro_neural_worker", SOURCE)
worker = module_from_spec(spec)
spec.loader.exec_module(worker)


class Neural:
    def __init__(self, trigger_id="cog:source-event-1"):
        self.core = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                cognition_triggers=(SimpleNamespace(trigger_id=trigger_id),)
            )
        )


class Applier:
    def __init__(self):
        self.calls = []

    def apply(self, event):
        self.calls.append(event["event_id"])
        return {"status": "APPLIED"}


def test_wake_schedule_failure_keeps_input_for_restart_replay(tmp_path):
    bus = FerroStateEventBus(tmp_path)
    scheduler = IndependentTaskScheduler(tmp_path)
    applier = Applier()
    bus.emit("NETWORK_EVIDENCE", {"evidence_ref": "source:important"})
    path, event = bus.pending()[0]

    class FailingScheduler:
        def schedule(self, *args, **kwargs):
            raise OSError("simulate crash before plan is persisted")

    with pytest.raises(OSError, match="simulate crash"):
        worker._process_event(bus, applier, Neural(), FailingScheduler(), path, event)
    assert len(bus.pending()) == 1, "the event must remain durable until its wake is scheduled"

    path, event = bus.pending()[0]
    worker._process_event(bus, applier, Neural(), scheduler, path, event)
    assert bus.pending() == ()
    (wake,) = scheduler.pending("CONTENT_COGNITION")
    assert wake.relevant_refs == ("cog:source-event-1",)
    assert applier.calls == [event["event_id"], event["event_id"]]


@pytest.mark.parametrize("kind", [
    "PUBLIC_SNAPSHOT", "BEHAVIORAL_PROJECTION",
    "BRAIN_LAB_PROJECTION", "BRAIN_LAB_LIVE_PROJECTION", "DREAM_PROJECTION",
])
def test_read_only_projections_never_requeue_stale_trigger(tmp_path, kind):
    bus = FerroStateEventBus(tmp_path)
    scheduler = IndependentTaskScheduler(tmp_path)
    applier = Applier()
    # A stale trigger is still in persisted neural state; queries must not
    # convert that preexisting trigger back into fresh work.
    for _ in range(2):
        bus.emit(kind)
        path, event = bus.pending()[0]
        worker._process_event(bus, applier, Neural(), scheduler, path, event)
    assert bus.pending() == ()
    assert scheduler.pending("CONTENT_COGNITION") == ()


def test_failed_event_does_not_schedule_stale_cognition(tmp_path):
    bus = FerroStateEventBus(tmp_path)
    scheduler = IndependentTaskScheduler(tmp_path)
    bus.emit("NETWORK_EVIDENCE", {"evidence_ref": "bad"})
    path, event = bus.pending()[0]

    class FailingApplier:
        def apply(self, event):
            raise ValueError("invalid signal")

    worker._process_event(bus, FailingApplier(), Neural(), scheduler, path, event)
    assert not scheduler.pending("CONTENT_COGNITION")
    assert bus.pending() == ()
    receipt = bus.result(event["event_id"])
    assert receipt["status"] == "ERROR"

def test_eventgraph_ref_survives_neural_wake_to_content_plan(tmp_path):
    from datetime import datetime, timezone
    bus = FerroStateEventBus(tmp_path)
    scheduler = IndependentTaskScheduler(tmp_path)
    story_ref = "eventgraph:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    bus.emit("NETWORK_EVIDENCE", {"evidence_ref": story_ref})
    path, event = bus.pending()[0]
    worker._process_event(bus, Applier(), Neural(), scheduler, path, event)
    (wake,) = scheduler.pending("CONTENT_COGNITION")
    assert story_ref in wake.relevant_refs

def test_ranked_eventgraph_story_wakes_without_neural_threshold(tmp_path):
    from types import SimpleNamespace
    bus = FerroStateEventBus(tmp_path)
    scheduler = IndependentTaskScheduler(tmp_path)
    story_ref = "eventgraph:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    bus.emit("NETWORK_EVIDENCE", {"evidence_ref": story_ref})
    path, event = bus.pending()[0]
    quiet_brain = SimpleNamespace(core=SimpleNamespace(
        snapshot=lambda: SimpleNamespace(cognition_triggers=()),
    ))
    worker._process_event(bus, Applier(), quiet_brain, scheduler, path, event)
    (wake,) = scheduler.pending("CONTENT_COGNITION")
    assert wake.relevant_refs == (story_ref,)
    assert wake.reason == "EventGraph story changed"
