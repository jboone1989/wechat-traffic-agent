#!/usr/bin/env python3
"""Read-only evidence collector for native Square editorial drafts."""
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.sources.binance_market_structure import collect_market_structure, save_market_structure


async def main():
    root = Path(os.environ.get("CONTENT_APPLICATION_ROOT", "/home/ubuntu/content-agent-state"))
    report = await collect_market_structure()
    path = save_market_structure(root, report)
    print(json.dumps({"status": report["status"], "observed_at": report["observed_at"],
                      "metrics": report["metrics"], "error": report.get("error"),
                      "snapshot": str(path), "publication_authority": False}, ensure_ascii=False))
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
