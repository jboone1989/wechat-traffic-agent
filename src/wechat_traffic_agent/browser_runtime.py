"""Browser runtime telemetry for the single Agent Runtime browser path."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request
from typing import Any


class BrowserRuntimeUnavailable(RuntimeError):
    """The Agent Runtime browser profile is unavailable."""


@dataclass(frozen=True)
class BrowserTaskResult:
    provider: str
    status: str
    elapsed_ms: int
    action_count: int
    final_url: str | None = None
    failure_reason: str | None = None


class BrowserTelemetryLedger:
    """Persist browser-provider economics without page text or secrets."""

    def __init__(self, state_root: Path) -> None:
        self.path = Path(state_root) / "browser" / "runtime-events.jsonl"

    def append(
        self,
        *,
        task_type: str,
        site: str,
        result: BrowserTaskResult,
        account_id: str | None = None,
    ) -> None:
        row = {
            "schema_version": "content-browser-runtime-event.v2",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "task_type": str(task_type)[:80],
            "site": str(site)[:160],
            **({"account_id": str(account_id)[:80]} if account_id else {}),
            **asdict(result),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _profile_status(profile_id: str) -> dict[str, Any]:
    state_path = (
        Path.home()
        / ".local"
        / "state"
        / "agent-runtime"
        / "browser"
        / "profiles"
        / profile_id
        / "runtime.json"
    )
    try:
        body = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "profile_id": profile_id,
            "status": "UNAVAILABLE",
            "reason": type(exc).__name__,
        }
    port = body.get("cdp_port") if isinstance(body, dict) else None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return {
            "profile_id": profile_id,
            "status": "UNAVAILABLE",
            "reason": "INVALID_CDP_PORT",
        }
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version",
            timeout=1,
        ) as response:
            response.read(256)
    except OSError as exc:
        return {
            "profile_id": profile_id,
            "status": "UNAVAILABLE",
            "cdp_url": f"http://127.0.0.1:{port}",
            "reason": type(exc).__name__,
        }
    return {
        "profile_id": profile_id,
        "status": "READY",
        "cdp_url": f"http://127.0.0.1:{port}",
    }


def browser_runtime_status() -> dict[str, Any]:
    profiles = [_profile_status("chatgpt"), _profile_status("x")]
    return {
        "schema_version": "content-browser-runtime-status.v2",
        "routing_policy": "AGENT_RUNTIME_ONLY",
        "provider": "AGENT_RUNTIME_BROWSER",
        "backend": "browser-harness",
        "status": (
            "READY"
            if all(row["status"] == "READY" for row in profiles)
            else "DEGRADED"
        ),
        "profiles": profiles,
        "providers": [
            {
                "provider": "AGENT_RUNTIME_BROWSER",
                "status": (
                    "READY"
                    if all(row["status"] == "READY" for row in profiles)
                    else "DEGRADED"
                ),
                "profiles": profiles,
            }
        ],
    }


__all__ = [
    "BrowserRuntimeUnavailable",
    "BrowserTaskResult",
    "BrowserTelemetryLedger",
    "browser_runtime_status",
]
