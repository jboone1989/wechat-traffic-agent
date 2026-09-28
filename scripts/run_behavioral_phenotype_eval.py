#!/usr/bin/env python3
"""Run Ferro's blind Behavioral Phenotype Test against persisted production state."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.behavioral_phenotype_eval import (  # noqa: E402
    SCENARIOS,
    concise_report,
    load_persisted_wakeful_state,
    run_behavioral_phenotype_eval,
)
from wechat_traffic_agent.openai_public_chat import OpenAIResponsesProvider  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--state-root", default=os.environ.get("CONTENT_APPLICATION_ROOT") or "/home/ubuntu/content-agent-state")
    p.add_argument("--service", default="content-agent.service")
    p.add_argument("--output", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--model", default="gpt-5.6-sol")
    return p


def _service_main_pid(service: str) -> int:
    raw = subprocess.check_output(
        ["systemctl", "show", service, "--property=MainPID", "--value"],
        text=True,
    ).strip()
    pid = int(raw)
    if pid <= 1:
        raise RuntimeError(f"{service} has no live MainPID")
    return pid


def _service_environment(service: str) -> dict[str, str]:
    pid = _service_main_pid(service)
    rows = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    result: dict[str, str] = {}
    allowed = {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "FERRO_PUBLIC_CHAT_OPENAI_MODEL",
    }
    for raw in rows:
        if b"=" not in raw:
            continue
        key_raw, value_raw = raw.split(b"=", 1)
        key = key_raw.decode("utf-8", "ignore")
        if key in allowed:
            result[key] = value_raw.decode("utf-8", "ignore")
    return result


async def _run(args: argparse.Namespace) -> dict:
    service_env = _service_environment(args.service)
    api_key = str(service_env.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("production service has no OPENAI_API_KEY")
    model = str(
        args.model
        or "gpt-5.6-sol"
    ).strip()
    base_url = str(
        service_env.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).strip().rstrip("/")

    subject = OpenAIResponsesProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=24.0,
        reasoning_effort="none",
    )
    judge = OpenAIResponsesProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=24.0,
        reasoning_effort="none",
    )
    state = load_persisted_wakeful_state(Path(args.state_root))
    scenarios = SCENARIOS[: args.limit] if args.limit > 0 else SCENARIOS
    report = await run_behavioral_phenotype_eval(
        subject_provider=subject,
        judge_provider=judge,
        wakeful_state=state,
        scenarios=scenarios,
    )
    report["runtime"] = {
        "model": model,
        "state_root": str(Path(args.state_root).resolve()),
        "scenario_ids": [row.scenario_id for row in scenarios],
        "subject_route": "OpenAI Responses public-chat-equivalent",
        "judge_route": "same model, blind A/B evaluator",
    }
    return report


def main() -> int:
    args = _parser().parse_args()
    report = asyncio.run(_run(args))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.output:
        output = Path(args.output).expanduser().resolve()
    else:
        output = (
            Path(args.state_root)
            / "evaluations"
            / "behavioral_phenotype"
            / f"{stamp}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "report_path": str(output),
        "summary": concise_report(report),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
