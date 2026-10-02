"""Independently reconcile X publication links and stalled production into WeCom."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(DailyPostingGoal(args.state_root).reconcile(
        datetime.now(timezone.utc), watch_heartbeat=True), ensure_ascii=False))
