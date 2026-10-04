from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.state_events import FerroStateEventApplier, FerroStateEventBus
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)


def test_task_scheduler_claims_only_its_own_task(tmp_path):
    scheduler = IndependentTaskScheduler(tmp_path)
    scheduler.schedule("CHECK_X", NOW, reason="x")
    scheduler.schedule(
        "CONTENT_COGNITION",
        NOW + timedelta(seconds=1),
        reason="content",
    )

    x = scheduler.claim_due("CHECK_X", NOW)

    assert x is not None and x.task == "CHECK_X"
    pending = scheduler.pending()
    assert [row.task for row in pending] == ["CONTENT_COGNITION"]


class _FakeWakeful:
    def __init__(self):
        self.calls = []

    def set_resource_pressure(self, value):
        self.calls.append(("resource", value))

    def emit_x_mentions(self, *, count, observed_at):
        self.calls.append(("x", count, observed_at))

    def emit_network_evidence(self, *, evidence_ref, observed_at):
        self.calls.append(("evidence", evidence_ref, observed_at))

    def emit_network_error(self, *, code, observed_at):
        self.calls.append(("error", code, observed_at))

    def apply_prediction_outcome_feedback(self, payload):
        self.calls.append(("prediction", dict(payload)))
        return {"status": "APPLIED"}

    def settle_brain_cortex(self, **payload):
        self.calls.append(("cortex", dict(payload)))
        return {"status": "SETTLED"}

    def emit_prediction_due(self, *, prediction_ref, observed_at):
        self.calls.append(("prediction_due", prediction_ref, observed_at))

    def emit_action_commitment(self, *, prediction_id, observed_at):
        self.calls.append(("action", prediction_id, observed_at))
        return {"status": "OBSERVED"}

    def mark_cognition(self, now):
        self.calls.append(("cognition", now))


def test_state_event_bus_has_single_state_writer_boundary(tmp_path):
    bus = FerroStateEventBus(tmp_path)
    wakeful = _FakeWakeful()
    applier = FerroStateEventApplier(wakeful)

    receipt = bus.emit("X_MENTIONS", {"count": 3}, observed_at=NOW)
    [(path, event)] = bus.pending()

    assert receipt["status"] == "QUEUED"
    result = applier.apply(event)
    bus.acknowledge(path, result)

    assert wakeful.calls == [("x", 3, NOW)]
    assert bus.pending() == ()
    assert len(list(bus.applied_root.glob("*.json"))) == 1


class _FakeXLoop:
    async def observe(self, *, now, allow_remote_actions=True):
        return {
            "status": "OK",
            "new_mentions": 2,
            "discovery": {"source": "MENTIONS"},
        }


def test_check_x_runner_does_not_require_wakeful_runtime(tmp_path):
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        x_loop=_FakeXLoop(),
        wakeful=None,
    )

    result = runner._x(NOW)

    assert result["status"] == "OK"
    events = FerroStateEventBus(tmp_path).pending()
    assert len(events) == 1
    assert events[0][1]["kind"] == "X_MENTIONS"
    assert events[0][1]["payload"]["count"] == 2
