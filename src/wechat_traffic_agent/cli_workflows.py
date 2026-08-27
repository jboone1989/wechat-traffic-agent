"""Handlers for the PR5 chain subcommands: scan / analyze / create / run.

The chain keeps its human-in-the-loop stance: nothing here publishes; the
commands only produce scan reports, pattern files, and immutable local
experiment packages for manual WeCom publishing.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date
from pathlib import Path

from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import LegacyProviderBackend
from agent_runtime.llm import load_llm_provider
from agent_runtime.runtime import AgentRuntime

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


def _hot_provider(args) -> DailyHotApiProvider | None:
    url = getattr(args, "dailyhot_url", None)
    return DailyHotApiProvider(base_url=url or DEFAULT_BASE_URL) if url else None


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
    async def run() -> int:
        report = await scan(
            platforms=args.platforms, hot_provider=_hot_provider(args), top_n=args.top_n
        )
        output = args.out or (
            Path("output") / "scans" / f"{date.today().isoformat()}-scan-{report.scan_id[:8]}.json"
        )
        save_scan(report, args.out.resolve() if args.out else output)
        summary = {
            "scan_id": report.scan_id,
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

    async def run():
        report = load_scan(args.scan)
        patterns = await analyze_report(runtime, report, top_k=args.top_k)
        output = args.out or args.scan.with_name(
            f"patterns-{report.scan_id[:8]}.json"
        )
        save_patterns(patterns, output)
        return report, output

    report, output = asyncio.run(run())
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
    package = asyncio.run(
        run_chain(
            runtime,
            audience,
            output_root=output_root,
            platforms=args.platforms,
            hot_provider=_hot_provider(args),
            top_n=args.top_n,
            top_k=args.top_k,
            variable=_variable(args),
            rotation_offset=args.rotation_offset,
            history=_read_history(getattr(args, "history", None)),
            audience_key=getattr(args, "audience_key", None),
            scan_out=scan_out,
        )
    )
    runtime.persist()
    experiment = json.loads(package.experiment_file.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "experiment_id": experiment["experiment_id"],
                "strategy": experiment["strategy"],
                "package": str(package.root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
