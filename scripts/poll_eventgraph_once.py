#!/usr/bin/env python3
"""One-shot EventGraph change poll. No model calls or publication side effects."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.eventgraph_context import poll_new_stories
from wechat_traffic_agent.state_events import FerroStateEventBus

state = Path(os.environ.get("CONTENT_APPLICATION_ROOT") or ROOT / "state/content_application")
print(json.dumps(poll_new_stories(state, FerroStateEventBus(state)), ensure_ascii=False))
