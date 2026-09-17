#!/usr/bin/env python3
"""Collect one bounded batch of X public outcome evidence."""
from __future__ import annotations
import asyncio,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from wechat_traffic_agent.x_outcomes import XOutcomeCollector
def state_root():
    raw=os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT/"state/content_application").resolve()
def main():
    result=asyncio.run(XOutcomeCollector(state_root=state_root()).collect(now=datetime.now(timezone.utc).replace(microsecond=0)))
    print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
