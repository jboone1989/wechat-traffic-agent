#!/usr/bin/env python3
"""Independent, one-shot Binance Square outbox consumer.

Run from a lightweight timer; it never starts the X browser or an LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
from wechat_traffic_agent.platforms.binance_square import (
    publish_square, square_enabled, square_writes_enabled,
)
from wechat_traffic_agent.publication.event_store import DistributionWorker, PublicationEventStore


def main() -> int:
    if not square_enabled() or not square_writes_enabled():
        return 0
    if not os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip():
        return 0  # Leave tasks pending until the posting-only key is configured.
    raw = os.environ.get("CONTENT_APPLICATION_ROOT", "")
    state_root = Path(raw).expanduser().resolve() if raw else (ROOT / "state/content_application").resolve()
    result = DistributionWorker(
        PublicationEventStore(state_root), {"binance_square": publish_square},
    ).run_once()
    if result is not None:
        DailyPostingGoal(state_root).notify_distribution_result(
            task=result, now=datetime.now(timezone.utc),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
