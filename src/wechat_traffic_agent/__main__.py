from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Sequence

from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import LegacyProviderBackend
from agent_runtime.llm import load_llm_provider
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import ContinuousRunner

from .models import AudienceProfile, PerformanceObservation, TopicSignal
from .service import WeChatTrafficAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wechat-tietu-agent",
        description="Use Agent Runtime to generate a copy-ready WeChat 题图 package.",
    )
    parser.add_argument("--audience", type=Path)
    parser.add_argument("--topics", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument(
        "--serve",
        action="store_true",
        help=(
            "Run a continuous runner that consumes the shared mailbox, so an "
            "agent-runtime-operator process on the same state-dir bridges WeCom."
        ),
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=2.0,
        help="Delay between life-loop cycles in serve mode (default: 2).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.serve and (args.audience is None or args.topics is None):
        build_parser().error("--audience and --topics are required (or use --serve)")
    package_root = Path(__file__).resolve().parents[2]
    manifest = load_agent_manifest(package_root / "agent.yaml")
    backend = LegacyProviderBackend(load_llm_provider())
    state_dir = args.state_dir.resolve()
    if (state_dir / "agent_state.json").exists():
        runtime = AgentRuntime.restore(state_dir, agent_backend=backend)
        if runtime.agent_manifest is None:
            runtime.agent_manifest = manifest
    else:
        runtime = AgentRuntime.bootstrap(
            state_dir,
            agent_manifest=manifest,
            agent_backend=backend,
        )

    if args.serve:
        return _run_serve(runtime, args)

    audience = AudienceProfile.model_validate(_read_json(args.audience))
    topics = [TopicSignal.model_validate(item) for item in _read_json(args.topics)]
    history = (
        [PerformanceObservation.model_validate(item) for item in _read_json(args.history)]
        if args.history
        else []
    )
    draft, package = asyncio.run(
        WeChatTrafficAgent(runtime).create_content_package(
            topics, history, audience, args.output_dir.resolve()
        )
    )
    runtime.persist()
    print(draft.description)
    print(f"\n内容包：{package.markdown}")
    return 0


def _run_serve(runtime: AgentRuntime, args: argparse.Namespace) -> int:
    """Keep the agent alive and answer the shared mailbox (WeCom entry)."""
    status = ContinuousRunner(runtime).run(
        max_ticks=None,
        idle_seconds=args.idle_seconds,
    )
    print(
        json.dumps(
            {
                "mode": "serve",
                "agent_id": runtime.identity.agent_id,
                "status": status.status,
                "stop_reason": status.stop_reason,
                "state_dir": str(runtime.state_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
