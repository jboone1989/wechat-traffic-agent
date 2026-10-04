#!/usr/bin/env python3
"""Independent CHECK_X worker. No WakefulRuntime ownership."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.behavioral_state import (
    build_behavioral_state,
    build_phenotype_prior,
)
from wechat_traffic_agent.runtime_backend import (
    load_independent_ferro_cognition_runtime,
)
from wechat_traffic_agent.state_events import FerroStateClient
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat
from wechat_traffic_agent.x_social import XAutonomyLoop


INTERVAL_SECONDS = 1800


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def main() -> int:
    state_root = _state_root()
    heartbeat = WorkerHeartbeat(state_root, "check-x")
    scheduler = IndependentTaskScheduler(state_root)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    state_client = FerroStateClient(state_root)

    def behavioral_context():
        behavioral_state = build_behavioral_state(
            state_client.behavioral_projection()
        )
        return {
            "phenotype_prior": build_phenotype_prior(behavioral_state),
            "behavioral_state": behavioral_state,
        }

    cognition_runtime = load_independent_ferro_cognition_runtime(
        state_root=state_root,
        behavioral_context_provider=behavioral_context,
    )
    x_loop = XAutonomyLoop(
        runtime=None,
        model_runtime=cognition_runtime,
        state_root=state_root,
        base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
        growth_mission=None,
        social_observer=state_client.observe_social_interaction,
        social_context_provider=state_client.relationship,
    )
    runner = WakefulContentTaskRunner(
        state_root=state_root,
        x_loop=x_loop,
        wakeful=None,
        stop_requested=lambda: stopping,
    )

    if not scheduler.pending("CHECK_X"):
        scheduler.schedule(
            "CHECK_X",
            datetime.now(timezone.utc),
            reason="initial independent X observation",
        )

    heartbeat.write(status="RUNNING")
    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            wake = scheduler.claim_due("CHECK_X", now)
            if wake is None:
                heartbeat.write(status="RUNNING", detail="waiting")
                time.sleep(1.0)
                continue
            heartbeat.write(status="RUNNING", detail="observing-x")
            result = runner.run(
                "CHECK_X",
                now=now,
                relevant_refs=tuple(wake.relevant_refs),
            )
            scheduler.schedule(
                "CHECK_X",
                datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS),
                reason="periodic independent X observation",
            )
            heartbeat.write(
                status="RUNNING",
                detail=str(result.get("status") or "UNKNOWN"),
            )
        return 0
    finally:
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
