#!/usr/bin/env python3
"""Ferro process supervisor.

This process owns no business logic. It starts, monitors and terminates
independent workers and publishes their process-level health.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _worker_specs() -> list[tuple[str, str]]:
    specs = [
        ("ferro-state", "scripts/run_ferro_state_worker.py"),
        ("content-cognition", "scripts/run_content_cognition_worker.py"),
        ("resource-review", "scripts/run_resource_review_worker.py"),
        ("check-outcomes", "scripts/run_check_outcomes_worker.py"),
        ("reflect", "scripts/run_reflect_worker.py"),
        ("self-repair", "scripts/run_self_repair_worker.py"),
    ]
    if _enabled("CONTENT_X_ENABLED", default=False):
        specs.insert(2, ("check-x", "scripts/run_check_x_worker.py"))
    return specs


def _write_health(
    state_root: Path,
    workers: dict[str, dict],
    *,
    status: str,
) -> None:
    path = state_root / "application" / "supervisor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ferro-supervisor.v1",
        "status": status,
        "pid": os.getpid(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "workers": {
            name: {
                "pid": row.get("process").pid if row.get("process") is not None else None,
                "restart_count": int(row.get("restart_count") or 0),
                "last_exit_code": row.get("last_exit_code"),
                "script": row["script"],
            }
            for name, row in workers.items()
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _start_worker(
    *,
    name: str,
    script: str,
    state_root: Path,
    log_root: Path,
) -> tuple[subprocess.Popen, object]:
    log_root.mkdir(parents=True, exist_ok=True)
    log = (log_root / f"{name}.log").open("ab", buffering=0)
    process = subprocess.Popen(
        [sys.executable, str(ROOT / script)],
        cwd=str(ROOT),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=False,
    )
    return process, log


def _wait_for_state_writer(state_root: Path, process: subprocess.Popen) -> None:
    path = state_root / "application" / "workers" / "ferro-state.json"
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"ferro-state exited during startup with code {process.returncode}"
            )
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            row = {}
        if (
            row.get("status") == "RUNNING"
            and int(row.get("pid") or 0) == process.pid
        ):
            return
        time.sleep(0.1)
    raise RuntimeError("ferro-state did not become ready")


def main() -> int:
    state_root = _state_root()
    log_root = state_root / "application" / "workers" / "logs"
    stopping = False
    workers: dict[str, dict] = {}

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        specs = _worker_specs()
        state_name, state_script = specs[0]
        process, log = _start_worker(
            name=state_name,
            script=state_script,
            state_root=state_root,
            log_root=log_root,
        )
        workers[state_name] = {
            "script": state_script,
            "process": process,
            "log": log,
            "restart_count": 0,
            "last_exit_code": None,
        }
        _wait_for_state_writer(state_root, process)

        for name, script in specs[1:]:
            process, log = _start_worker(
                name=name,
                script=script,
                state_root=state_root,
                log_root=log_root,
            )
            workers[name] = {
                "script": script,
                "process": process,
                "log": log,
                "restart_count": 0,
                "last_exit_code": None,
            }

        _write_health(state_root, workers, status="RUNNING")

        while not stopping:
            for name, row in workers.items():
                process = row["process"]
                exit_code = process.poll()
                if exit_code is None:
                    continue
                row["last_exit_code"] = int(exit_code)
                row["restart_count"] = int(row["restart_count"]) + 1
                row["log"].close()
                if stopping:
                    continue
                time.sleep(0.5)
                new_process, new_log = _start_worker(
                    name=name,
                    script=row["script"],
                    state_root=state_root,
                    log_root=log_root,
                )
                row["process"] = new_process
                row["log"] = new_log
                if name == "ferro-state":
                    _wait_for_state_writer(state_root, new_process)
            _write_health(state_root, workers, status="RUNNING")
            time.sleep(1.0)

        return 0
    finally:
        for row in workers.values():
            process = row.get("process")
            if process is not None and process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 20.0
        for row in workers.values():
            process = row.get("process")
            if process is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
            log = row.get("log")
            if log is not None and not log.closed:
                log.close()
        _write_health(state_root, workers, status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
