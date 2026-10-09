#!/usr/bin/env python3
"""Independent native Square draft worker; no browser or cognition."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.platforms.binance_square import square_enabled, square_writes_enabled
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftWorker
from wechat_traffic_agent.platforms.binance_square_notifications import notify_square_distribution
from wechat_traffic_agent.publication.models import DistributionTask


def load_private_config(state_root: Path) -> None:
    """Read saved posting-only access, without commands, logs or hard-coded credentials."""
    flags = state_root / ".binance-square-settings.json"
    if flags.is_file():
        settings = json.loads(flags.read_text(encoding="utf-8"))
        if settings.get("enabled"):
            os.environ.setdefault("CONTENT_BINANCE_SQUARE_ENABLED", "1")
        if settings.get("writes_enabled"):
            os.environ.setdefault("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    credential = state_root / ".binance-square-posting.key"
    if credential.is_file():
        credential.chmod(0o600)
        value = credential.read_text(encoding="utf-8").strip()
        if value:
            os.environ.setdefault("BINANCE_SQUARE_OPENAPI_KEY", value)


def report_status(payload: dict, *, state_root: Path) -> None:
    path = state_root / "application" / "binance_square_worker.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {**payload, "observed_at": datetime.now(timezone.utc).isoformat()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT", "")
    root = Path("/home/ubuntu/content-agent-state")
    state_root = Path(raw).expanduser().resolve() if raw else (root if root.is_dir() else ROOT / "state/content_application").resolve()
    load_private_config(state_root)
    result = SquareDraftWorker(state_root).run_once()
    if result is not None and os.environ.get("CONTENT_SQUARE_OPERATOR_STATE_DIR", "").strip():
        receipt = DistributionTask(
            distribution_id=result["draft_id"], event_id="SquareDraft:" + result["draft_id"],
            channel="binance_square", status="SUCCESS" if result["status"] == "SUCCESS" else "FAILED_PERMANENT",
            attempts=result["attempts"], last_error=result.get("error"),
            published_url=result.get("url"), published_post_id=result.get("post_id"),
            attempt_times=[result["attempted_at"]],
        )
        notify_square_distribution(receipt,
            operator_root=Path(os.environ["CONTENT_SQUARE_OPERATOR_STATE_DIR"]).expanduser(),
            now=datetime.now(timezone.utc))
    if result is None:
        status = ("DISABLED" if not square_enabled() else "WAITING_FOR_WRITES"
                  if not square_writes_enabled() else "WAITING_FOR_KEY"
                  if not os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip() else "WAITING_FOR_EDITORIAL")
        report_status({"channel": "binance_square", "status": status}, state_root=state_root)
        return 0
    report_status({
        "channel": "binance_square", "status": result["status"],
        "draft_id": result["draft_id"],
        "attempts": result["attempts"], "url": result.get("url"),
        "post_id": result.get("post_id"), "error": result.get("error"),
    }, state_root=state_root)
    return 0 if result["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
