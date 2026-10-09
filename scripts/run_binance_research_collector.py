#!/usr/bin/env python3
"""One-shot read-only Binance research source. Does not create content or trades."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.sources.binance_web3_public import (
    collect_binance_public, save_binance_snapshot,
)


async def main() -> int:
    default = Path("/home/ubuntu/content-agent-state")
    root = Path(os.environ.get("CONTENT_APPLICATION_ROOT") or (
        default if default.exists() else ROOT / "state/content_application"
    ))
    report = await collect_binance_public()
    path = save_binance_snapshot(root, report)
    print(json.dumps({
        "status": "OK" if report["observations"] else "NO_DATA",
        "as_of": report["observed_at"],
        "sources": [{"source": x["source"], "rows": x["row_count"],
                     "evidence_revision": x["revision"][:24]}
                    for x in report["observations"]],
        "failures": report["failures"], "snapshot_path": str(path),
        "publication_authority": False,
    }, ensure_ascii=False))
    return 0 if report["observations"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
