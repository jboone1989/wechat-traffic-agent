#!/usr/bin/env python3
"""Independent, neural-triggered EventGraph research worker (not a publisher)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.eventgraph_research import EventGraphResearch
from wechat_traffic_agent.state_events import FerroStateClient, FerroStateEventBus
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat


def _root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (
        ROOT / "state" / "content_application"
    ).resolve()


def main() -> int:
    site = os.environ.get("CONTENT_NEWS_AGGREGATOR_URL", "").strip()
    if not site:
        return 0
    state_root = _root()
    scheduler = IndependentTaskScheduler(state_root)
    heartbeat = WorkerHeartbeat(state_root, "eventgraph-research")
    events = FerroStateEventBus(state_root)
    state = FerroStateClient(state_root)
    research = EventGraphResearch(state_root=state_root, site_url=site)
    stopping = False
    runtime = None

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    def behavioral_context():
        from wechat_traffic_agent.behavioral_state import (
            build_behavioral_state, build_phenotype_prior,
        )
        behavior = build_behavioral_state(state.behavioral_projection())
        return {
            "phenotype_prior": build_phenotype_prior(behavior),
            "behavioral_state": behavior,
        }

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    heartbeat.write(status="RUNNING")
    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            wake = scheduler.claim_due("EVENTGRAPH_RESEARCH", now)
            if wake is None:
                time.sleep(0.5)
                continue
            ref = next(
                (ref for ref in wake.relevant_refs
                 if ref.startswith("eventgraph-event:")), "",
            )
            if not ref:
                continue
            heartbeat.write(status="RUNNING", detail="researching")
            try:
                if runtime is None:
                    from wechat_traffic_agent.runtime_backend import (
                        load_independent_ferro_cognition_runtime,
                    )
                    runtime = load_independent_ferro_cognition_runtime(
                        state_root=state_root,
                        behavioral_context_provider=behavioral_context,
                        session_id="ferro-eventgraph-research",
                    )
                result = asyncio.run(research.run(
                    evidence_ref=ref, runtime=runtime,
                ))
                if result["status"] in {"RESEARCHED", "ALREADY_RESEARCHED"}:
                    events.emit("MARK_COGNITION", observed_at=now)
                heartbeat.write(status="RUNNING", detail=result["status"])
            except Exception as exc:
                # Failed research is not a lost event. Preserve its original
                # identity and retry without waking all other Ferro tasks.
                scheduler.schedule(
                    "EVENTGRAPH_RESEARCH", now + timedelta(minutes=5),
                    wake_id=wake.wake_id,
                    reason="retry neural-origin EventGraph research",
                    relevant_refs=wake.relevant_refs,
                )
                heartbeat.write(status="RUNNING",
                                detail="research error: " + type(exc).__name__)
        return 0
    finally:
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
