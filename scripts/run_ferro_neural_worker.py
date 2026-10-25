#!/usr/bin/env python3
"""Single owner of Ferro's live neural state."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.ferro_neural_runtime import FerroNeuralRuntime
from wechat_traffic_agent.eventgraph_neural_feed import EventGraphChangeSensor
from wechat_traffic_agent.state_events import FerroStateEventApplier, FerroStateEventBus
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return (
        Path(raw).expanduser().resolve()
        if raw
        else (ROOT / "state" / "content_application").resolve()
    )


def main() -> int:
    state_root = _state_root()
    heartbeat = WorkerHeartbeat(state_root, "ferro-neural")
    bus = FerroStateEventBus(state_root)
    scheduler = IndependentTaskScheduler(state_root)
    neural = FerroNeuralRuntime(state_root=state_root)
    applier = FerroStateEventApplier(neural)
    site = os.environ.get("CONTENT_NEWS_AGGREGATOR_URL", "").strip()
    sensor = EventGraphChangeSensor(state_root=state_root, site_url=site) if site else None
    next_eventgraph_poll = 0.0
    stopping = False

    def perceive_event(item, changed_at, evidence_ref):
        response = neural.emit_eventgraph_item(
            item=item, changed_at=changed_at, evidence_ref=evidence_ref,
        )
        if not response["cognition_triggered"]:
            return
        # Each event gets its own durable task. The neural state, not the
        # poll cadence or source score, is the authority to spend LLM tokens.
        scheduler.schedule(
            "EVENTGRAPH_RESEARCH", datetime.now(timezone.utc),
            wake_id="eventgraph-research:" + evidence_ref.split(":")[-1],
            reason="Ferro neural cognition elected EventGraph research",
            relevant_refs=(evidence_ref, response["event_id"]),
        )

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heartbeat.write(status="RUNNING")

    next_heartbeat = time.monotonic()
    try:
        while not stopping:
            did_work = False
            now = time.monotonic()
            if sensor is not None and now >= next_eventgraph_poll:
                next_eventgraph_poll = now + 15.0
                try:
                    result = sensor.poll(perceive_event)
                    did_work = did_work or result["delivered"] > 0
                except Exception as exc:
                    # Source failure never kills Ferro's live nervous system.
                    heartbeat.write(status="RUNNING",
                                    detail="EventGraph offline: " + type(exc).__name__)
            for path, event in bus.pending():
                did_work = True
                try:
                    result = applier.apply(event)
                except Exception as exc:
                    result = {
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                bus.acknowledge(path, result)

                snapshot = neural.core.snapshot()
                if snapshot.cognition_triggers:
                    scheduler.schedule(
                        "CONTENT_COGNITION",
                        datetime.now(timezone.utc),
                        reason="Ferro neural state crossed cognition threshold",
                        relevant_refs=tuple(
                            trigger.trigger_id
                            for trigger in snapshot.cognition_triggers[-8:]
                        ),
                    )

            now = time.monotonic()
            if now >= next_heartbeat:
                heartbeat.write(
                    status="RUNNING",
                    detail="applied-neural-events" if did_work else "idle",
                )
                next_heartbeat = now + 5.0
            if not did_work:
                time.sleep(0.2)
        return 0
    finally:
        if sensor is not None:
            sensor.close()
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
