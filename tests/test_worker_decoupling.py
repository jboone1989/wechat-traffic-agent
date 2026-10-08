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


def test_async_neural_receipt_does_not_duplicate_large_input_state(tmp_path):
    bus = FerroStateEventBus(tmp_path)
    projection = {"big_live_snapshot": "x" * 100_000}
    bus.emit("INTEROCEPTIVE_FAULT", {"projection": projection}, observed_at=NOW)
    [(path, event)] = bus.pending()
    assert len(path.read_bytes()) > 100_000
    bus.acknowledge(path, {"status": "APPLIED", "episode_id": "signal-1"})
    [(receipt,)] = [tuple(bus.applied_root.glob("*.json"))]
    row = receipt.read_text()
    assert "big_live_snapshot" not in row
    assert len(row) < 600
    assert "signal-1" in row


def test_request_reply_is_consumed_not_accumulated_as_memory(tmp_path):
    bus = FerroStateEventBus(tmp_path)
    row = bus.emit("PUBLIC_SNAPSHOT", {}, observed_at=NOW, expects_reply=True)
    [(path, _event)] = bus.pending()
    bus.acknowledge(path, {"status": "OK", "snapshot": {"public": "x" * 120000}})
    assert len(list(bus.applied_root.glob("*.json"))) == 1
    assert bus.result(row["event_id"])["snapshot"]["public"] == "x" * 120000
    assert not list(bus.applied_root.glob("*.json"))


def test_old_neural_receipts_compact_without_losing_result(tmp_path):
    import json
    import os
    from datetime import timedelta
    from pathlib import Path
    import runpy
    script = Path(__file__).resolve().parents[1] / "scripts" / "compact_state_event_receipts.py"
    compact = runpy.run_path(str(script))["compact"]
    bus = FerroStateEventBus(tmp_path)
    event = bus.emit("INTEROCEPTIVE_FAULT", {"projection": {"deep_copy": "x" * 90_000}}, observed_at=NOW)
    [(path, row)] = bus.pending()
    bus.acknowledge(path, {"status": "APPLIED", "episode_id": "memory-already-ingested"})
    receipt = next(bus.applied_root.glob("*.json"))
    legacy = {**row, "applied_at": NOW.isoformat(), "result": {"status": "APPLIED", "episode_id": "memory-already-ingested"}}
    receipt.write_text(json.dumps(legacy))
    old = (NOW - timedelta(days=2)).timestamp()
    os.utime(receipt, (old, old))
    request = bus.emit("PUBLIC_SNAPSHOT", {}, observed_at=NOW)
    [(qpath, _)] = bus.pending()
    bus.acknowledge(qpath, {"status": "OK", "snapshot": {"huge": "y" * 100_000}})
    snapshot_receipt = next(x for x in bus.applied_root.glob("*.json") if x != receipt)
    os.utime(snapshot_receipt, (old, old))
    previous_bytes = receipt.stat().st_size
    dry = compact(bus.applied_root, now=NOW, age_hours=24, limit=10, apply=False)
    assert dry["counts"] == {"compact_fault": 1, "expire_query": 1}
    assert receipt.stat().st_size == previous_bytes
    done = compact(bus.applied_root, now=NOW, age_hours=24, limit=10, apply=True)
    assert done["counts"] == dry["counts"]
    assert receipt.stat().st_size < 900
    compacted = json.loads(receipt.read_text())
    assert compacted["result"]["episode_id"] == "memory-already-ingested"
    assert len(compacted["input_sha256"]) == 64
    assert not snapshot_receipt.exists()
