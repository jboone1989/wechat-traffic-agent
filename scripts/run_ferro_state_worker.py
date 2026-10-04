#!/usr/bin/env python3
"""Single writer for Ferro's canonical internal state."""
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
from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def main() -> int:
    state_root = _state_root()
    heartbeat = WorkerHeartbeat(state_root, "ferro-state")
    bus = FerroStateEventBus(state_root)
    scheduler = IndependentTaskScheduler(state_root)
    wakeful = ContentWakefulRuntime(
        state_root=state_root,
        content_interval_hours=int(os.environ.get("CONTENT_CONTINUOUS_INTERVAL_HOURS", "6")),
        watchdog_seconds=1800,
    )
    applier = FerroStateEventApplier(wakeful)
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
                try:
                    result = applier.apply(event)
                except Exception as exc:
                    result = {
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                bus.acknowledge(path, result)

                snapshot = wakeful.core.snapshot()
                if snapshot.cognition_triggers:
                    scheduler.schedule(
                        "CONTENT_COGNITION",
                        datetime.now(timezone.utc),
                        reason="Ferro state crossed cognition threshold",
                        relevant_refs=tuple(
                            trigger.trigger_id for trigger in snapshot.cognition_triggers[-8:]
                        ),
                    )

            now = time.monotonic()
            if now >= next_heartbeat:
                heartbeat.write(
                    status="RUNNING",
                    detail="applied-state-events" if did_work else "idle",
                )
                next_heartbeat = now + 5.0
            if not did_work:
                time.sleep(0.2)
        return 0
    finally:
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
