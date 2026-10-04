#!/usr/bin/env python3
"""Independent SELF_REPAIR worker with isolated capability-runtime state."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.behavioral_state import build_behavioral_state, build_phenotype_prior
from wechat_traffic_agent.runtime_app import build_content_application
from wechat_traffic_agent.runtime_backend import load_independent_ferro_cognition_runtime
from wechat_traffic_agent.self_repair_executor import SelfRepairExecutor
from wechat_traffic_agent.state_events import FerroStateClient
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _write_result(state_root: Path, result: dict) -> None:
    path = state_root / "application" / "workers" / "self-repair-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    state_root = _state_root()
    heartbeat = WorkerHeartbeat(state_root, "self-repair")
    scheduler = IndependentTaskScheduler(state_root)
    state_client = FerroStateClient(state_root)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def behavioral_context():
        behavioral_state = build_behavioral_state(state_client.behavioral_projection())
        return {
            "phenotype_prior": build_phenotype_prior(behavioral_state),
            "behavioral_state": behavioral_state,
        }

    planner_runtime = load_independent_ferro_cognition_runtime(
        state_root=state_root,
        behavioral_context_provider=behavioral_context,
    )

    capability_root = state_root / "worker_runtime" / "self_repair"
    capability_app = build_content_application(
        root=capability_root,
        manifest_path=ROOT / "agent.yaml",
        allow_missing_required_capabilities=True,
    )

    workspace_raw = os.environ.get("CONTENT_SELF_REPAIR_WORKSPACE", "").strip()
    self_repair = SelfRepairExecutor(
        state_root=state_root,
        workspace_root=Path(workspace_raw) if workspace_raw else None,
        deployed_root=ROOT,
        python_executable=sys.executable,
        planner_runtime=planner_runtime,
        capability_runtime=capability_app.runtime,
        test_pythonpath=os.environ.get("CONTENT_SELF_REPAIR_TEST_PYTHONPATH", "").strip(),
    )
    runner = WakefulContentTaskRunner(
        state_root=state_root,
        self_repair=self_repair,
        wakeful=None,
        stop_requested=lambda: stopping,
    )

    startup = self_repair.startup_recovery_summary(now=datetime.now(timezone.utc))
    if int(startup.get("actionable_count") or 0) > 0 and not scheduler.pending("SELF_REPAIR"):
        scheduler.schedule(
            "SELF_REPAIR",
            datetime.now(timezone.utc),
            reason="resume actionable self-repair after worker start",
        )

    heartbeat.write(status="RUNNING")
    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            wake = scheduler.claim_due("SELF_REPAIR", now)
            if wake is None:
                heartbeat.write(status="RUNNING", detail="waiting")
                time.sleep(1.0)
                continue
            heartbeat.write(status="RUNNING", detail="repairing")
            try:
                result = runner.run(
                    "SELF_REPAIR",
                    now=now,
                    relevant_refs=tuple(wake.relevant_refs),
                )
            except Exception as exc:
                result = {
                    "task": "SELF_REPAIR",
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            _write_result(state_root, result)
            heartbeat.write(status="RUNNING", detail=str(result.get("status") or "UNKNOWN"))
        return 0
    finally:
        capability_app.close()
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
