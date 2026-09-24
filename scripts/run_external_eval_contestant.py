#!/usr/bin/env python3
"""Run Ferro as a JSONL contestant for mature external benchmarks."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.awe_provider import build_fixed_openai_provider
from wechat_traffic_agent.external_eval_contestant import (
    FerroExternalEvalContestant,
    build_chatgpt_only_provider,
    handle_external_request,
)
from wechat_traffic_agent.llm_audit import audited_provider
from wechat_traffic_agent.runtime_backend import load_optional_advisory_backend


def _provider(args):
    fixed_requested = bool(
        args.fixed_model or args.fixed_base_url or args.fixed_credential_env
    )
    if args.chatgpt_only and fixed_requested:
        raise SystemExit("--chatgpt-only cannot be combined with fixed provider options")
    if args.chatgpt_only:
        return build_chatgpt_only_provider(state_root=args.state_root)
    if fixed_requested:
        if not (args.fixed_model and args.fixed_base_url and args.fixed_credential_env):
            raise SystemExit("fixed provider requires model, base URL, and credential env")
        provider = build_fixed_openai_provider(
            credential_env=args.fixed_credential_env,
            base_url=args.fixed_base_url,
            model=args.fixed_model,
            max_retries=args.infra_retries,
            timeout_seconds=args.provider_timeout_seconds,
        )
        return audited_provider(
            provider,
            args.state_root,
            scope="external-benchmark-evaluation",
        )
    backend = load_optional_advisory_backend(state_root=args.state_root)
    if backend is None:
        raise SystemExit("Ferro advisory provider is not configured for this evaluation snapshot")
    return backend.provider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument(
        "--chatgpt-only",
        action="store_true",
        help="Require LivingRuntime ChatGPT cognition and forbid API fallback.",
    )
    parser.add_argument("--fixed-model")
    parser.add_argument("--fixed-base-url")
    parser.add_argument("--fixed-credential-env")
    parser.add_argument("--infra-retries", type=int, default=2)
    parser.add_argument("--provider-timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    contestant = FerroExternalEvalContestant(
        state_root=args.state_root,
        provider=_provider(args),
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
            result = asyncio.run(handle_external_request(contestant, request))
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
