"""Isolated Jev worker for one account-scoped Chromium profile."""
from __future__ import annotations

import json
import sys
import time


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"status": "FAILED", "failure_reason": "INVALID_REQUEST"}))
        return 2
    url = str(request.get("url") or "")
    goal = str(request.get("goal") or "")
    if not url.startswith(("https://", "http://")) or not goal.strip():
        print(json.dumps({"status": "FAILED", "failure_reason": "INVALID_REQUEST"}))
        return 2

    started = time.perf_counter()
    final = {}
    try:
        from jev_ultrafast import Agent
        with Agent(url, goal) as agent:
            for state in agent.run():
                final = dict(state)
            if not final and hasattr(agent, "snapshot"):
                final = dict(agent.snapshot())
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 0,
            "failure_reason": type(exc).__name__,
        }))
        return 1

    raw_status = str(final.get("status") or "blocked").upper()
    page = final.get("page") if isinstance(final.get("page"), dict) else {}
    history = final.get("history") if isinstance(final.get("history"), list) else []
    print(json.dumps({
        "status": "COMPLETED" if raw_status == "DONE" else "BLOCKED",
        "elapsed_ms": int(
            final.get("elapsed_ms")
            or round((time.perf_counter() - started) * 1000)
        ),
        "action_count": len(history),
        "final_url": str(page.get("url") or "") or None,
        "failure_reason": None if raw_status == "DONE" else raw_status,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
