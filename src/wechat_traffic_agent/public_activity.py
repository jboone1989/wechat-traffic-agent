"""Bounded public activity events for the Live Agent surface."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

MAX_EVENTS = 400
MAX_DETAIL_CHARS = 500


def _log_path(state_root: Path) -> Path:
    return Path(state_root) / "content" / "live" / "public-activity.jsonl"


def emit_public_activity(
    state_root: Path,
    *,
    cycle: str,
    phase: str,
    title: str,
    detail: str = "",
    status: str = "working",
    kind: str = "progress",
) -> dict[str, Any]:
    """Append one safe, user-visible progress event. Never store private reasoning."""
    now = datetime.now(timezone.utc)
    event = {
        "schema_version": "public-agent-activity:v1",
        "event_id": f"{time.time_ns()}",
        "at": now.isoformat(),
        "cycle": str(cycle)[:120],
        "phase": str(phase)[:40].upper(),
        "title": str(title)[:180],
        "detail": str(detail)[:MAX_DETAIL_CHARS],
        "status": str(status)[:40],
        "kind": str(kind)[:40],
    }
    path = _log_path(Path(state_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    try:
        if path.stat().st_size > 512_000:
            rows = path.read_text(encoding="utf-8").splitlines()[-MAX_EVENTS:]
            path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    except OSError:
        pass
    return event


def read_public_activity(state_root: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    path = _log_path(Path(state_root))
    try:
        rows = path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), MAX_EVENTS)):]
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema_version") == "public-agent-activity:v1":
            events.append(payload)
    return events


__all__ = ["emit_public_activity", "read_public_activity"]
