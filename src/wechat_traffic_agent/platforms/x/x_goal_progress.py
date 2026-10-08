"""Observed X execution context for autonomous owner-goal choices."""
from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any


def _snapshot(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected an X state object at {path.name}")
    return value


def owner_x_execution_context(state_root: Path | None, *, configured: bool) -> dict[str, Any]:
    """Describe current evidence; execution still enforces existing X authority."""
    context: dict[str, Any] = {
        "configured": configured,
        "transport": "JEV_AGENT_RUNTIME",
        "authority": "EXISTING_X_PUBLICATION_AND_REPLY_GATES",
        "selection_authority": "FERRO",
        "quotas_required": False,
        "available_actions": (
            ["CHECK_X", "ENGAGE_X", "X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"]
            if configured else []
        ),
    }
    if state_root is None:
        return context
    root = Path(state_root) / "content" / "x"
    status = _snapshot(root / "status.json")
    profile = _snapshot(root / "public_profile.json")
    context.update(
        status=status,
        profile={key: profile.get(key) for key in ("handle", "followers_count", "synced_at")},
        owner_strategy_advice=_snapshot(root / "strategy_advice.json"),
        previous_discovery=_snapshot(root / "discovery.json").get("decision"),
    )
    path = root / "history.jsonl"
    try:
        with path.open(encoding="utf-8") as handle:
            lines = deque(handle, maxlen=64)
    except FileNotFoundError:
        lines = []
    actions = []
    for line in lines:
        row = json.loads(line)
        if row.get("event_type") not in {"EXPRESSION", "REPLY", "PREDICTION_OUTCOME"}:
            continue
        if not row.get("x_post_id"):
            continue
        receipt_id = row.get("runtime_receipt_id")
        cycle = str(row.get("cycle") or "")
        if not receipt_id and cycle and Path(cycle).name == cycle:
            publication = _snapshot(
                Path(state_root) / "content" / "continuous" / "cycles" / cycle
                / "x_browser_publication.json"
            )
            if publication.get("status") == "POSTED" and str(publication.get("post_id")) == str(row["x_post_id"]):
                receipt_id = f"x-browser-publication:{cycle}:{row['x_post_id']}"
        if not receipt_id:
            continue
        actions.append({
            **{key: row.get(key) for key in
            ("event_type", "cycle", "x_post_id", "x_url", "target_post_id",
             "recorded_at")},
            "runtime_receipt_id": receipt_id,
        })
    context["recent_publication_receipts"] = actions[-8:]
    return context
