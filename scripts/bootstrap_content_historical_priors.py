#!/usr/bin/env python3
from __future__ import annotations
from datetime import datetime, timezone
import json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from wechat_traffic_agent.content_historical_replay import build_historical_content_priors, import_historical_content_priors
from wechat_traffic_agent.content_mind import ContentMind
state=Path(os.environ.get('CONTENT_APPLICATION_ROOT', ROOT/'state/content_application')).resolve()
account=os.environ.get('CONTENT_BASE_URL','https://livingruntime.com').rstrip('/')
replay=build_historical_content_priors(state_root=state,account=account,as_of=datetime.now(timezone.utc))
mind=ContentMind(root=state,subject_id='content-agent')
result=import_historical_content_priors(mind=mind,replay=replay)
print(json.dumps({'replay':replay,'import':result},ensure_ascii=False,sort_keys=True))
