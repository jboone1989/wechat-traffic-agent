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
from uuid import uuid4


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
        ("ferro-neural", "scripts/run_ferro_neural_worker.py"),
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
                "pid": row.get("worker_pid")
                if row.get("worker_pid") is not None
                else (
                    row.get("process").pid
                    if row.get("process") is not None
                    else None
                ),
                "restart_count": int(row.get("restart_count") or 0),
                "failure_streak": int(row.get("failure_streak") or 0),
                "restart_backoff_seconds": float(row.get("restart_backoff_seconds") or 0.0),
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
    argv = [sys.executable, str(ROOT / script)]
    environment = os.environ.copy()
    if name == "ferro-neural":
        user_runtime = "/run/user/" + str(os.getuid())
        environment.setdefault("XDG_RUNTIME_DIR", user_runtime)
        environment.setdefault(
            "DBUS_SESSION_BUS_ADDRESS",
            "unix:path=" + user_runtime + "/bus",
        )
        argv = [
            "systemd-run",
            "--user",
            "--quiet",
            "--scope",
            "--unit=ferro-neural-" + uuid4().hex,
            *argv,
        ]
    process = subprocess.Popen(
        argv,
        cwd=str(ROOT),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=False,
    )
    return process, log


def _wait_for_neural_writer(
    state_root: Path,
    process: subprocess.Popen,
) -> int:
    path = state_root / "application" / "workers" / "ferro-neural.json"
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"ferro-neural exited during startup with code {process.returncode}"
            )
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            row = {}
        if (
            row.get("status") == "RUNNING"
            and int(row.get("pid") or 0) > 1
        ):
            return int(row["pid"])
        time.sleep(0.1)
    raise RuntimeError("ferro-neural did not become ready")


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
            "failure_streak": 0,
            "restart_backoff_seconds": 0.0,
            "next_restart_monotonic": 0.0,
            "started_monotonic": time.monotonic(),
            "last_exit_code": None,
        }
        workers[state_name]["worker_pid"] = _wait_for_neural_writer(
            state_root, process
        )

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
                "failure_streak": 0,
                "restart_backoff_seconds": 0.0,
                "next_restart_monotonic": 0.0,
                "started_monotonic": time.monotonic(),
                "last_exit_code": None,
                "worker_pid": process.pid,
            }

        _write_health(state_root, workers, status="RUNNING")

        while not stopping:
            for name, row in workers.items():
                process = row["process"]
                now_mono = time.monotonic()
                if process is None:
                    if now_mono < float(row.get("next_restart_monotonic") or 0.0):
                        continue
                    new_process, new_log = _start_worker(
                        name=name,
                        script=row["script"],
                        state_root=state_root,
                        log_root=log_root,
                    )
                    row["process"] = new_process
                    row["log"] = new_log
                    row["started_monotonic"] = now_mono
                    if name == "ferro-neural":
                        row["worker_pid"] = _wait_for_neural_writer(
                            state_root, new_process
                        )
                    else:
                        row["worker_pid"] = new_process.pid
                    continue
                exit_code = process.poll()
                if exit_code is None:
                    if (
                        int(row.get("failure_streak") or 0) > 0
                        and now_mono - float(row.get("started_monotonic") or now_mono) >= 60.0
                    ):
                        row["failure_streak"] = 0
                        row["restart_backoff_seconds"] = 0.0
                    continue
                row["last_exit_code"] = int(exit_code)
                row["restart_count"] = int(row["restart_count"]) + 1
                row["failure_streak"] = int(row.get("failure_streak") or 0) + 1
                delay = min(
                    60.0,
                    float(2 ** min(int(row["failure_streak"]) - 1, 6)),
                )
                row["restart_backoff_seconds"] = delay
                row["next_restart_monotonic"] = now_mono + delay
                if row.get("log") is not None and not row["log"].closed:
                    row["log"].close()
                row["process"] = None
                row["worker_pid"] = None
                if stopping:
                    continue
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
