#!/usr/bin/env python3
"""Independent host/resource observation worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.state_events import FerroStateEventBus
from wechat_traffic_agent.survival_resources import SurvivalResourceManager
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat


INTERVAL_SECONDS = 3600


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _write_result(state_root: Path, result: dict) -> None:
    path = state_root / "application" / "workers" / "resource-review-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    state_root = _state_root()
    scheduler = IndependentTaskScheduler(state_root)
    events = FerroStateEventBus(state_root)
    heartbeat = WorkerHeartbeat(state_root, "resource-review")
    survival = SurvivalResourceManager(state_root)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    if not scheduler.pending("RESOURCE_REVIEW"):
        scheduler.schedule("RESOURCE_REVIEW", datetime.now(timezone.utc), reason="initial resource review")

    heartbeat.write(status="RUNNING")
    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            wake = scheduler.claim_due("RESOURCE_REVIEW", now)
            if wake is None:
                heartbeat.write(status="RUNNING", detail="waiting")
                time.sleep(1.0)
                continue
            heartbeat.write(status="RUNNING", detail="observing")
            try:
                before = survival.observe(now=now)
                actions = survival.execute_approved(now=now)
                after = survival.observe(now=now) if actions.get("results") else before
                pressure = str(after.get("pressure") or "UNKNOWN").upper()
                state_event = events.emit(
                    "SET_RESOURCE_PRESSURE",
                    {"pressure": pressure},
                    observed_at=now,
                )
                result = {
                    "task": "RESOURCE_REVIEW",
                    "status": "OK",
                    "before": before,
                    "actions": actions,
                    "after": after,
                    "state_event": state_event,
                }
            except Exception as exc:
                result = {
                    "task": "RESOURCE_REVIEW",
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            _write_result(state_root, result)
            scheduler.schedule(
                "RESOURCE_REVIEW",
                datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS),
                reason="periodic independent resource review",
            )
            heartbeat.write(status="RUNNING", detail=str(result.get("status") or "UNKNOWN"))
        return 0
    finally:
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
