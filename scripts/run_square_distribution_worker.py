#!/usr/bin/env python3
"""Independent one-shot Binance Square PublicationEvent consumer.

No X browser, cognition, agent loop or X-specific posting state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.platforms.binance_square_consumer import SquareEventConsumer
from wechat_traffic_agent.platforms.binance_square_notifications import notify_square_distribution


def main() -> int:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT", "")
    state_root = Path(raw).expanduser().resolve() if raw else (ROOT / "state/content_application").resolve()
    result = SquareEventConsumer(state_root).run_once()
    if result is not None and os.environ.get("CONTENT_SQUARE_OPERATOR_STATE_DIR", "").strip():
        notify_square_distribution(
            result, operator_root=Path(os.environ["CONTENT_SQUARE_OPERATOR_STATE_DIR"]).expanduser(),
            now=datetime.now(timezone.utc),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
