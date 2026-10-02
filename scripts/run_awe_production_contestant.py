#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.awe_production_contestant import (
    ProductionAWEContestant,
    handle_production_request,
)
from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProvider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args()

    os.environ["AGENT_RUNTIME_LLM_PROVIDER"] = "chatgpt-browser"

    provider = AgentRuntimeChatGPTProvider.from_environment(fallback=None)
    if not provider.available:
        raise SystemExit("ChatGPT provider unavailable")
    contestant = ProductionAWEContestant(
        state_root=args.state_root,
        provider=provider,
    )
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                if request.get("type") == "close":
                    return 0
                result = asyncio.run(
                    handle_production_request(contestant, request)
                )
                print(
                    json.dumps(
                        {"ok": True, **result},
                        ensure_ascii=False,
                        default=str,
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    json.dumps({
                        "ok": False,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:1000],
                    }, ensure_ascii=False),
                    flush=True,
                )
        return 0
    finally:
        contestant.close()


if __name__ == "__main__":
    raise SystemExit(main())
