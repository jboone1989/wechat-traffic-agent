"""Isolated deterministic X publisher for one account-scoped JEV profile."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _emit({"status": "INVALID_DRAFT", "message": "invalid request"})
        return 2
    text = str(request.get("text") or "")
    expected_handle = str(request.get("expected_handle") or "")
    state_root = Path(str(request.get("state_root") or "."))
    started = time.perf_counter()
    action_state = {"attempted": False}
    try:
        from .browser_runtime import BrowserRuntimeUnavailable
        from .x_browser import XBrowserPublicationUnverified
        from .x_local_browser import LocalHarnessXTransport

        transport = LocalHarnessXTransport(
            state_root=state_root,
            expected_handle=expected_handle,
        )
        result = transport._publish_sync(text, action_state=action_state)
    except ValueError as exc:
        _emit({
            "status": "INVALID_DRAFT",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 0,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        })
        return 2
    except BrowserRuntimeUnavailable as exc:
        status = (
            "PUBLICATION_UNVERIFIED"
            if action_state["attempted"]
            else "SESSION_UNAVAILABLE"
        )
        _emit({
            "status": status,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 1 if action_state["attempted"] else 0,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        })
        return 1
    except XBrowserPublicationUnverified as exc:
        _emit({
            "status": "PUBLICATION_UNVERIFIED",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 1 if action_state["attempted"] else 0,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        })
        return 1
    except Exception as exc:
        _emit({
            "status": "PUBLICATION_UNVERIFIED",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 1 if action_state["attempted"] else 0,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        })
        return 1

    _emit({
        "status": "POSTED",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "action_count": 0 if result.duplicate_found else 1,
        "post_id": result.post_id,
        "url": result.url,
        "handle": result.handle,
        "duplicate_found": result.duplicate_found,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
