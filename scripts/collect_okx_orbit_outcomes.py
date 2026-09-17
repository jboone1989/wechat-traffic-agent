#!/usr/bin/env python3
"""Collect one bounded batch of public OKX Orbit outcome evidence."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.okx_orbit_outcomes import OKXOrbitOutcomeCollector  # noqa: E402


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def main() -> int:
    collector = OKXOrbitOutcomeCollector(
        state_root=_state_root(),
        account=os.environ.get("CONTENT_OKX_ORBIT_ACCOUNT", "https://www.okx.com/orbit"),
    )
    result = asyncio.run(collector.collect(now=datetime.now(timezone.utc).replace(microsecond=0)))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
