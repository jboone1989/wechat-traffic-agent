#!/usr/bin/env python3
"""One-shot live data observation. Does not start a browser or publish."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.sources.market_pipeline import MarketSignalCollector


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    if not enabled("CONTENT_MARKET_INGESTION_ENABLED"):
        print(json.dumps({"status": "DISABLED"}))
        return 0
    raw = os.getenv("CONTENT_APPLICATION_ROOT", "")
    state_root = Path(raw).expanduser().resolve() if raw else (ROOT / "state/content_application").resolve()
    report = asyncio.run(MarketSignalCollector(state_root).run_once())
    if report["new_research_events"] and enabled("CONTENT_MARKET_WAKE_ENABLED"):
        from wechat_traffic_agent.sources.market_pipeline import MarketSignalStore
        from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
        now = datetime.now(timezone.utc)
        refs = tuple(row["id"] for row in MarketSignalStore(state_root).recent(now=now, limit=3))
        IndependentTaskScheduler(state_root).schedule(
            "CONTENT_COGNITION", now, reason="new market evidence available for research",
            relevant_refs=refs,
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
