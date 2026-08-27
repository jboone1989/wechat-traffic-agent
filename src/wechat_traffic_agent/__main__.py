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
from .planner import VARIABLES
from .service import WeChatTrafficAgent


def _platforms_arg(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--platforms",
        type=_platforms_arg,
        default=("weibo", "zhihu", "bilibili"),
    )
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--dailyhot-url",
        type=str,
        default=None,
        help="启用 DailyHotApi 热榜（默认离线 fixture）。",
    )
    parser.add_argument(
        "--source",
        choices=("fixture", "live"),
        default=None,
        help=(
            "数据源模式：fixture 全链离线样本；live 接真实 DailyHot 热榜 + "
            "deep fetch（bilibili 平台完整：正文/作者/互动数据，其余平台走通用"
            " Jina 正文读取）。live 绝不回退 fixture，缺能力按 unavailable 暴露。"
            "不传时保持原行为（给了 --dailyhot-url 即视为 live）。"
        ),
    )


def _add_analyze_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=("fixture", "live"),
        default=None,
        help=(
            "证据重抓模式：live 会按扫描报告里的 URL 真实重取正文与评论"
            "（bilibili 走平台完整通道）；默认 fixture 离线。"
        ),
    )


def _add_feedback_args(sub) -> None:
    sub.add_argument("--audience-key", type=str, default=None, help="反馈学习的人群隔离键")
    sub.add_argument(
        "--observation-window",
        type=str,
        choices=("24h", "48h", "7d"),
        default=None,
        help=(
            "反馈学习的观察窗口（24h/48h/7d）。与 --audience-key 同时提供"
            "才启用历史推断，否则轮转选择被试变量。"
        ),
    )


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

    subparsers = parser.add_subparsers(dest="command")

    def _runtime_args(sub):
        sub.add_argument("--state-dir", type=Path, default=Path("state"))
        sub.add_argument("--output-dir", type=Path, default=Path("output"))

    scan_parser = subparsers.add_parser("scan", help="热榜扫描 → ViralCandidate 报告")
    _add_source_args(scan_parser)
    scan_parser.add_argument("--out", type=Path, default=None)

    analyze_parser = subparsers.add_parser("analyze", help="Top-K 候选 → ViralPattern")
    analyze_parser.add_argument("--scan", type=Path, required=True)
    analyze_parser.add_argument("--top-k", type=int, default=5)
    analyze_parser.add_argument("--out", type=Path, default=None)
    _add_analyze_source_args(analyze_parser)
    _runtime_args(analyze_parser)

    create_parser = subparsers.add_parser("create", help="ViralPattern → 实验内容包")
    create_parser.add_argument("--patterns", type=Path, required=True)
    create_parser.add_argument("--audience", type=Path, required=True)
    create_parser.add_argument("--history", type=Path)
    _add_feedback_args(create_parser)
    create_parser.add_argument("--variable", choices=[v.value for v in VARIABLES], default=None)
    create_parser.add_argument("--rotation-offset", type=int, default=0)
    _runtime_args(create_parser)

    run_parser = subparsers.add_parser("run", help="scan → analyze → create 串成一趟")
    _add_source_args(run_parser)
    run_parser.add_argument("--audience", type=Path, required=True)
    run_parser.add_argument("--history", type=Path)
    _add_feedback_args(run_parser)
    run_parser.add_argument("--top-k", type=int, default=5)
    run_parser.add_argument("--variable", choices=[v.value for v in VARIABLES], default=None)
    run_parser.add_argument("--rotation-offset", type=int, default=0)
    _runtime_args(run_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None):
        from .cli_workflows import dispatch_command

        return dispatch_command(args)
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
        from .wecom_router import build_wecom_router

        router = build_wecom_router(args.output_dir.resolve())
        return _run_serve(runtime, args, message_router=router)

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


def _run_serve(
    runtime: AgentRuntime,
    args: argparse.Namespace,
    *,
    message_router=None,
) -> int:
    """Keep the agent alive and answer the shared mailbox (WeCom entry)."""
    status = ContinuousRunner(runtime, message_router=message_router).run(
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
