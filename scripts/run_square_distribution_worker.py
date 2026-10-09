#!/usr/bin/env python3
"""Independent one-shot Binance Square PublicationEvent consumer.

No X browser, cognition, agent loop or X-specific posting state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.platforms.binance_square import (
    square_enabled, square_writes_enabled,
)
from wechat_traffic_agent.platforms.binance_square_consumer import SquareEventConsumer
from wechat_traffic_agent.platforms.binance_square_notifications import notify_square_distribution


def main() -> int:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT", "")
    state_root = Path(raw).expanduser().resolve() if raw else (ROOT / "state/content_application").resolve()
    consumer = SquareEventConsumer(state_root)
    result = consumer.run_once()
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
        "post_id": result.published_post_id,
        "error": result.last_error,
    }, ensure_ascii=False))
    return 0 if result.status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
