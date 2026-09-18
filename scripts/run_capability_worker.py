#!/usr/bin/env python3
"""Run a minimal public Content capability worker on an opportunistic node."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.capability_worker import (  # noqa: E402
    PUBLIC_WORKER_CAPABILITIES,
    build_public_capability_worker,
)
from wechat_traffic_agent.network_service import (  # noqa: E402
    ContentNetworkService,
    content_network_seed_urls_from_environment,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="minimal public Content capability worker")
    parser.add_argument(
        "--state-root",
        default=os.environ.get("CONTENT_WORKER_STATE_ROOT", str(ROOT / "state" / "public_worker")),
    )
    parser.add_argument(
        "--agent-id",
        default=os.environ.get("CONTENT_WORKER_AGENT_ID", ""),
    )
    parser.add_argument(
        "--capabilities",
        default=os.environ.get(
            "CONTENT_WORKER_CAPABILITIES",
            ",".join(sorted(PUBLIC_WORKER_CAPABILITIES)),
        ),
    )
    parser.add_argument("--health", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    worker_id = str(args.agent_id).strip()
    if not worker_id:
        raise SystemExit("CONTENT_WORKER_AGENT_ID or --agent-id is required")
    capabilities = tuple(
        dict.fromkeys(item.strip() for item in str(args.capabilities).split(",") if item.strip())
    )
    unsupported = sorted(set(capabilities) - PUBLIC_WORKER_CAPABILITIES)
    if unsupported:
        raise SystemExit("public worker refuses unsupported capabilities: " + ",".join(unsupported))
    if not capabilities:
        raise SystemExit("public worker requires at least one capability")

    state_root = Path(args.state_root).expanduser().resolve()
    app = build_public_capability_worker(root=state_root, worker_id=worker_id)
    if args.health:
        print(app.status_observation())
        app.close()
        return 0

    service = ContentNetworkService(
        root=state_root,
        application=app,
        seed_urls=content_network_seed_urls_from_environment(),
        bind_host=os.environ.get("CONTENT_WORKER_HOST", "127.0.0.1"),
        advertise_host=os.environ.get("CONTENT_WORKER_ADVERTISE_HOST") or None,
        public_base_url=os.environ.get("CONTENT_WORKER_PUBLIC_BASE_URL") or None,
        port=int(os.environ.get("CONTENT_WORKER_PORT", "8113")),
        agent_id=worker_id,
        capabilities=capabilities,
        startup_timeout_seconds=float(os.environ.get("CONTENT_WORKER_STARTUP_TIMEOUT_SECONDS", "10")),
        join_retry_seconds=float(os.environ.get("CONTENT_WORKER_JOIN_RETRY_SECONDS", "30")),
        lease_seconds=int(os.environ.get("CONTENT_WORKER_NETWORK_LEASE_SECONDS", "300")),
        engagement_enabled=False,
    )
    stopping = False

    def _stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        service.start()
        print({
            "status": "JOINED" if service.joined else "ENDPOINT_READY_JOIN_PENDING",
            "agent_id": worker_id,
            "capabilities": list(capabilities),
            "endpoint": service.endpoint,
        })
        while not stopping:
            time.sleep(1.0)
    finally:
        service.stop()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
