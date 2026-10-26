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


_PROJECTION_EVENTS = frozenset({
    "BEHAVIORAL_PROJECTION", "PUBLIC_SNAPSHOT", "BRAIN_LAB_PROJECTION",
    "BRAIN_LAB_LIVE_PROJECTION", "DREAM_PROJECTION",
})


def _process_event(bus, applier, neural, scheduler, path, event) -> None:
    try:
        result = applier.apply(event)
    except Exception as exc:
        result = {
            "status": "ERROR", "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        }

    # Do not turn read-only projections or failed event applications into wakes.
    if result.get("status") != "ERROR" and event.get("kind") not in _PROJECTION_EVENTS:
        triggers = neural.core.snapshot().cognition_triggers
        if triggers:
            scheduler.schedule(
                "CONTENT_COGNITION", datetime.now(timezone.utc),
                reason="Ferro neural state crossed cognition threshold",
                relevant_refs=tuple(row.trigger_id for row in triggers[-8:]),
            )
    # Ack AFTER the wake is durable. A failed schedule leaves the input to replay.
    bus.acknowledge(path, result)


def main() -> int:
    from wechat_traffic_agent.ferro_neural_runtime import FerroNeuralRuntime

    state_root = _state_root()
    heartbeat = WorkerHeartbeat(state_root, "ferro-neural")
    bus = FerroStateEventBus(state_root)
    scheduler = IndependentTaskScheduler(state_root)
    neural = FerroNeuralRuntime(state_root=state_root)
    applier = FerroStateEventApplier(neural)
    stopping = False

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
            for path, event in bus.pending():
                did_work = True
                _process_event(bus, applier, neural, scheduler, path, event)

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
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
