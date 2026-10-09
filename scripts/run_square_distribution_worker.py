#!/usr/bin/env python3
"""Independent Binance Square PublicationEvent consumer; no browser or cognition."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.platforms.binance_square import square_enabled, square_writes_enabled
from wechat_traffic_agent.platforms.binance_square_consumer import SquareEventConsumer
from wechat_traffic_agent.platforms.binance_square_notifications import notify_square_distribution


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


def main() -> int:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT", "")
    root = Path("/home/ubuntu/content-agent-state")
    state_root = Path(raw).expanduser().resolve() if raw else (root if root.is_dir() else ROOT / "state/content_application").resolve()
    load_private_config(state_root)
    result = SquareEventConsumer(state_root).run_once()
    if result is not None and os.environ.get("CONTENT_SQUARE_OPERATOR_STATE_DIR", "").strip():
        notify_square_distribution(
            result, operator_root=Path(os.environ["CONTENT_SQUARE_OPERATOR_STATE_DIR"]).expanduser(),
            now=datetime.now(timezone.utc),
        )
    if result is None:
        status = ("DISABLED" if not square_enabled() else "WAITING_FOR_WRITES"
                  if not square_writes_enabled() else "WAITING_FOR_KEY"
                  if not os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip() else "NO_READY_TASK")
        print(json.dumps({"channel": "binance_square", "status": status}))
        return 0
    print(json.dumps({
        "channel": "binance_square", "status": result.status,
        "event_id": result.event_id, "distribution_id": result.distribution_id,
        "attempts": result.attempts, "url": result.published_url,
        "post_id": result.published_post_id, "error": result.last_error,
    }, ensure_ascii=False))
    return 0 if result.status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
