"""Handlers for the PR5 chain subcommands: scan / analyze / create / run.

The chain keeps its human-in-the-loop stance: nothing here publishes; the
commands only produce scan reports, pattern files, and immutable local
experiment packages for manual WeCom publishing.

PR7: ``--source`` chooses the data plane explicitly. ``fixture`` stays
fully offline (unchanged behavior); ``live`` wires DailyHotApi + a
ReaderRouter whose bilibili route is platform-complete and whose default
is generic Jina reading. Live NEVER falls back to FixtureProvider — a
missing capability surfaces as UNAVAILABLE/PARTIAL/FAILED instead of
fixture data filling the gaps.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

import httpx
from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import LegacyProviderBackend
from agent_runtime.llm import load_llm_provider
from agent_runtime.runtime import AgentRuntime

from .agent_reach import AgentReachProvider
from .bilibili import BilibiliCliReader
from .dailyhot import DEFAULT_BASE_URL, DailyHotApiProvider
from .models import AudienceProfile, PerformanceObservation
from .planner import (
    VARIABLES,
    ExperimentVariable,
    analyze_report,
    load_patterns,
    load_scan,
    package_planned,
    plan_experiment,
    run_chain,
    save_patterns,
    save_scan,
    scan,
)
from .reader_router import ReaderRouter


def bootstrap_runtime(state_dir: Path) -> AgentRuntime:
    """Same bootstrap rules as the legacy flow in __main__."""
    package_root = Path(__file__).resolve().parents[2]
    manifest = load_agent_manifest(package_root / "agent.yaml")
    backend = LegacyProviderBackend(load_llm_provider())
    state_dir = state_dir.resolve()
    if (state_dir / "agent_state.json").exists():
        runtime = AgentRuntime.restore(state_dir, agent_backend=backend)
        if runtime.agent_manifest is None:
            runtime.agent_manifest = manifest
        return runtime
    return AgentRuntime.bootstrap(state_dir, agent_manifest=manifest, agent_backend=backend)


@dataclass(frozen=True)
class SourceStack:
    """Effective data plane for one CLI invocation."""

    mode: str  # "fixture" | "live"
    hot_provider: object | None
    reader: object | None
    closer: Callable[[], Awaitable[None]] | None


def _build_source_stack(args) -> SourceStack:
    """Resolve --source into concrete providers. fixture ⇒ (None, None):
    planner 层保持既有离线默认；live ⇒ 真实热榜 + ReaderRouter，任何通道
    都不再触碰 FixtureProvider，缺能力按 unavailable 暴露。"""
    dailyhot_url = getattr(args, "dailyhot_url", None)
    requested = getattr(args, "source", None)
    if dailyhot_url and requested == "fixture":
        raise SystemExit(
            "--dailyhot-url 与 --source fixture 冲突：要离线就删掉 url，"
            "要真实链路就用 --source live"
        )
    if requested == "live" or (requested is None and dailyhot_url):
        if not dailyhot_url:
            print(
                f"--source live 未提供 --dailyhot-url，使用默认部署 {DEFAULT_BASE_URL}",
                file=sys.stderr,
            )
        http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        reach = AgentReachProvider(http_client=http_client)
        router = ReaderRouter(
            default=reach, routes={"bilibili": BilibiliCliReader(inner=reach)}
        )
        return SourceStack(
            mode="live",
            hot_provider=DailyHotApiProvider(base_url=dailyhot_url or DEFAULT_BASE_URL),
            reader=router,
            closer=http_client.aclose,
        )
    return SourceStack(mode="fixture", hot_provider=None, reader=None, closer=None)


def _variable(args) -> ExperimentVariable | None:
    raw = getattr(args, "variable", None)
    if raw is None:
        return None
    return next(item for item in VARIABLES if item.value == raw)


def _read_history(path: Path | None) -> list[PerformanceObservation]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [PerformanceObservation.model_validate(item) for item in payload]


def dispatch_command(args) -> int:
    command = args.command
    handlers = {
        "scan": _cmd_scan,
        "analyze": _cmd_analyze,
        "create": _cmd_create,
        "run": _cmd_run,
    }
    handler = handlers.get(command)
    if handler is None:
        raise SystemExit(f"unknown command {command!r}")
    return handler(args)


def _cmd_scan(args) -> int:
    stack = _build_source_stack(args)

    async def run() -> int:
        try:
            report = await scan(
                platforms=args.platforms,
                hot_provider=stack.hot_provider,
                reader=stack.reader,
                top_n=args.top_n,
            )
        finally:
            if stack.closer is not None:
                await stack.closer()
        output = args.out or (
            Path("output") / "scans" / f"{date.today().isoformat()}-scan-{report.scan_id[:8]}.json"
        )
        save_scan(report, args.out.resolve() if args.out else output)
        summary = {
            "scan_id": report.scan_id,
            "source": stack.mode,
            "file": str(output),
            "platforms": [
                {"platform": p.platform, "status": p.status.value, "items": p.items_fetched}
                for p in report.platforms
            ],
            "top_candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "title": c.title,
                    "final_score": c.final_score,
                    "breakout_score": c.breakout_score,
                }
                for c in report.candidates[:10]
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    return asyncio.run(run())


def _cmd_analyze(args) -> int:
    runtime = bootstrap_runtime(args.state_dir)
    stack = _build_source_stack(args)

    async def run():
        try:
            report = load_scan(args.scan)
            patterns = await analyze_report(
                runtime, report, reader=stack.reader, top_k=args.top_k
            )
        finally:
            if stack.closer is not None:
                await stack.closer()
        output = args.out or args.scan.with_name(
            f"patterns-{report.scan_id[:8]}.json"
        )
        save_patterns(patterns, output)
        return report, output, patterns

    report, output, patterns = asyncio.run(run())
    for index, pattern in enumerate(patterns, start=1):
        print(
            json.dumps(
                {
                    "rank": index,
                    "candidate_id": pattern.candidate_id,
                    "target_audience": pattern.target_audience,
                    "confidence": pattern.confidence,
                    "evidence_refs": len(pattern.source_refs),
                },
                ensure_ascii=False,
            )
        )
    print(f"patterns 文件：{output}")
    return 0


def _cmd_create(args) -> int:
    runtime = bootstrap_runtime(args.state_dir)
    audience = AudienceProfile.model_validate(
        json.loads(args.audience.read_text(encoding="utf-8"))
    )
    history = _read_history(args.history)
    patterns = load_patterns(args.patterns)
    if not patterns:
        raise SystemExit("patterns 文件为空，无法创建实验")

    async def run():
        return await plan_experiment(
            runtime,
            patterns[0],
            audience,
            history=history,
            audience_key=getattr(args, "audience_key", None),
            observation_window=getattr(args, "observation_window", None),
            variable=_variable(args),
            rotation_offset=args.rotation_offset,
        )

    planned = asyncio.run(run())
    package = package_planned(
        planned, audience, args.output_dir.resolve(), pattern=patterns[0]
    )
    runtime.persist()
    print(planned.draft.description)
    print(
        "\n实验策略："
        + json.dumps(planned.strategy.model_dump(), ensure_ascii=False)
    )
    print(f"\n内容包：{package.markdown}")
    return 0


def _cmd_run(args) -> int:
    runtime = bootstrap_runtime(args.state_dir)
    audience = AudienceProfile.model_validate(
        json.loads(args.audience.read_text(encoding="utf-8"))
    )
    output_root = args.output_dir.resolve()
    scan_out = (
        output_root
        / "scans"
        / f"{date.today().isoformat()}-scan-{uuid.uuid4().hex[:8]}.json"
    )
    stack = _build_source_stack(args)

    async def chain():
        try:
            return await run_chain(
                runtime,
                audience,
                output_root=output_root,
                platforms=args.platforms,
                hot_provider=stack.hot_provider,
                reader=stack.reader,
                top_n=args.top_n,
                top_k=args.top_k,
                variable=_variable(args),
                rotation_offset=args.rotation_offset,
                history=_read_history(getattr(args, "history", None)),
                audience_key=getattr(args, "audience_key", None),
                observation_window=getattr(args, "observation_window", None),
                scan_out=scan_out,
            )
        finally:
            if stack.closer is not None:
                await stack.closer()

    package = asyncio.run(chain())
    runtime.persist()
    experiment = json.loads(package.experiment_file.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "experiment_id": experiment["experiment_id"],
                "source": stack.mode,
                "strategy": experiment["strategy"],
                "package": str(package.root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
