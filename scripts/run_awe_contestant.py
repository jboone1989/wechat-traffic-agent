#!/usr/bin/env python3
"""Run Ferro as an AWE JSONL contestant against an isolated state snapshot."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.awe_contestant import FerroAWEContestant, handle_request
from wechat_traffic_agent.runtime_backend import load_optional_advisory_backend


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args()

    backend = load_optional_advisory_backend(state_root=args.state_root)
    if backend is None:
        raise SystemExit("Ferro advisory provider is not configured for this evaluation snapshot")

    contestant = FerroAWEContestant(
        state_root=args.state_root,
        provider=backend.provider,
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
            result = asyncio.run(handle_request(contestant, request))
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:500]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
