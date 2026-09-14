#!/usr/bin/env python3
"""Run the persistent Content Agent with Runtime, VirtualBrain and Agent Network."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.network_service import ContentNetworkService, network_enabled  # noqa: E402
from wechat_traffic_agent.runtime_app import (  # noqa: E402
    APPLICATION_VERSION,
    build_content_application,
)


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _health_path() -> Path:
    return _state_root() / "application" / "health.json"


def _show_health() -> int:
    path = _health_path()
    if not path.exists():
        print(json.dumps({"ok": False, "reason": "health_not_recorded"}))
        return 1
    print(path.read_text(encoding="utf-8").strip())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="persistent Content Agent")
    parser.add_argument("--once", action="store_true", help="Run one application tick and exit")
    parser.add_argument("--health", action="store_true", help="Print persisted application health")
    parser.add_argument("--poll-seconds", type=int, default=300)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.health:
        return _show_health()
    if args.poll_seconds <= 0:
        parser.error("poll-seconds must be positive")

    state_root = _state_root()
    app = build_content_application(root=state_root, manifest_path=ROOT / "agent.yaml")
    network_service: ContentNetworkService | None = None
    if network_enabled():
        network_service = ContentNetworkService.from_environment(root=state_root, application=app)
        network_service.start()
        print(f"agent-network joined seed={network_service.seed_url} endpoint={network_service.endpoint}")

    stopping = False

    def _stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(f"{APPLICATION_VERSION} publish_mode=manual")
    try:
        while not stopping:
            as_of = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            try:
                guard = network_service.execution_lock if network_service is not None else nullcontext()
                with guard:
                    result = app.run_tick(as_of_time=as_of)
                print(json.dumps(result.to_payload(), ensure_ascii=False, sort_keys=True))
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({
                    "application_version": APPLICATION_VERSION,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }, sort_keys=True), file=sys.stderr)
                if args.once:
                    return 1
            if args.once:
                return 0
            deadline = time.monotonic() + args.poll_seconds
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return 0
    finally:
        if network_service is not None:
            network_service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
