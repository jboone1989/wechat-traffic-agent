#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from wechat_traffic_agent.channels.screeps_live_page import screeps_live_document
def main():
 p=argparse.ArgumentParser()
 p.add_argument("--web-root",type=Path,default=Path("/var/www/content-agent-live/current/screeps"))
 p.add_argument("--public-live-file",type=Path,default=Path("/var/www/content-agent-live/screeps-live.json"))
 a=p.parse_args(); root=a.web_root.expanduser().resolve(); root.mkdir(parents=True,exist_ok=True)
 out=root/"index.html"; tmp=root/"index.html.tmp"
 tmp.write_text(screeps_live_document(data_url="/screeps/live.json"),encoding="utf-8"); tmp.replace(out)
 link=root/"live.json"
 if link.exists() or link.is_symlink(): link.unlink()
 link.symlink_to(a.public_live_file.expanduser().resolve())
 return 0
if __name__=="__main__": raise SystemExit(main())
