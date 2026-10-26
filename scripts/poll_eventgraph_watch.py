#!/usr/bin/env python3
"""Dev-only bounded periodic EventGraph check. Reuses poll_new_stories and the
scripts/poll_eventgraph_once.py wiring (real FerroStateEventBus, state root as
CONTENT_APPLICATION_ROOT) but always against an isolated state directory:
never the real Ferro state, no cognition/publish worker, no publication.
Stdlib fetch is injected because httpx is absent on cn-dev, for the same
reason as scripts/smoke_eventgraph_evidence.py. Not a daemon: bounded by
--max-checks, stops on SIGINT/SIGTERM or end of checks, then prints a summary.
"""
import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.eventgraph_context import poll_new_stories
from wechat_traffic_agent.state_events import FerroStateEventBus

DEFAULT_BASE_URL = "http://127.0.0.1:3000"


def stdlib_fetch(base_url):
    def fetch():
        request = urllib.request.Request(
            base_url.rstrip("/") + "/api/v1/hot-topics",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=12.0) as response:
            return json.loads(response.read().decode("utf-8"))
    return fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=30.0,
                        help="seconds between checks (default 30)")
    parser.add_argument("--max-checks", type=int, default=2,
                        help="stop after this many checks (default 2)")
    parser.add_argument("--base-url", default=None,
                        help="EventGraph API base URL (default $EVENTGRAPH_API_BASE_URL or "
                             + DEFAULT_BASE_URL + ")")
    parser.add_argument("--state-root", default=None,
                        help="isolated state directory (default: fresh temp dir, removed at exit)")
    args = parser.parse_args()
    if args.max_checks < 1 or args.interval < 0:
        parser.error("--max-checks must be >= 1 and --interval >= 0")

    base_url = (args.base_url or os.environ.get("EVENTGRAPH_API_BASE_URL") or DEFAULT_BASE_URL).strip()
    owns_state = args.state_root is None
    state_root = Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp(prefix="eventgraph-poll-watch-"))
    fetch = stdlib_fetch(base_url)

    def stop_now(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, stop_now)
    signal.signal(signal.SIGTERM, stop_now)

    results, stop_reason, done = [], "completed", 0
    try:
        bus = FerroStateEventBus(state_root)
        for done in range(1, args.max_checks + 1):
            row = poll_new_stories(state_root, bus, base_url=base_url, fetch=fetch)
            results.append(row)
            print(json.dumps({"check": done, **row}, ensure_ascii=False), flush=True)
            if done < args.max_checks:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        stop_reason = "interrupted"
    finally:
        pending = sorted((state_root / "application" / "state_events" / "pending").glob("*.json"))
        unique = sorted({ref for row in results for ref in row["queued"]})
        summary = {
            "summary": True,
            "base_url": base_url,
            "state_root": str(state_root),
            "checks_completed": len(results),
            "max_checks": args.max_checks,
            "interval": args.interval,
            "stop_reason": stop_reason,
            "results": results,
            "unique_queued": unique,
            "events_enqueued": len(pending),
        }
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if owns_state:
            shutil.rmtree(state_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
