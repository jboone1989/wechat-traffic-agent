#!/usr/bin/env python3
"""Run raw/basic ChatGPT baselines for mature external benchmarks."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.external_eval_baseline import (
    ExternalEvalBaselineContestant,
    handle_external_baseline_request,
)
from wechat_traffic_agent.external_eval_contestant import build_chatgpt_only_provider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("raw", "basic"), required=True)
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args()

    os.environ["LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED"] = "1"
    os.environ["LIVINGRUNTIME_CHATGPT_AGENT_ID"] = args.agent_id
    os.environ["LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS"] = "900"
    os.environ["LIVINGRUNTIME_CHATGPT_DISPATCH_GRACE_SECONDS"] = "60"
    os.environ["LIVINGRUNTIME_CHATGPT_FOREGROUND_WAIT_SECONDS"] = "120"

    contestant = ExternalEvalBaselineContestant(
        state_root=args.state_root,
        provider=build_chatgpt_only_provider(state_root=args.state_root),
        mode=args.mode,
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            if request.get("type") == "close":
                return 0
            result = asyncio.run(handle_external_baseline_request(contestant, request))
            print(json.dumps({"ok": True, **result}, ensure_ascii=False, default=str), flush=True)
        except Exception as exc:
            print(json.dumps({
                "ok": False,
                "error": type(exc).__name__,
                "detail": str(exc)[:1000],
                "retryable": bool(getattr(exc, "retryable", False)),
            }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
