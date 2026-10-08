#!/usr/bin/env python3
"""Paired memory-off/on Ferro learning evaluation using existing JEV transport.

Uses the isolated AWE snapshot. No API fallback and no additional daemon.
The benchmark owner must independently approve gold cases before scores may
be reported as evidence of learning benefit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.external_eval_contestant import build_chatgpt_only_provider
from wechat_traffic_agent.learning_evaluation import (
    evaluate_learning, load_benchmark, snapshot_digest, compare_runs,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--model-id", default="jev-chatgpt")
    parser.add_argument("--retrieval-mode", choices=("lexical", "full"), default="lexical")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--compare-before")
    parser.add_argument("--compare-after")
    opts = parser.parse_args()
    if opts.compare_before or opts.compare_after:
        if not opts.compare_before or not opts.compare_after:
            parser.error("comparison requires both run IDs")
        print(json.dumps(compare_runs(
            opts.ledger, opts.compare_before, opts.compare_after,
        ), ensure_ascii=False, indent=2))
        return 0
    root = opts.snapshot_root.resolve()
    if not (root / AWE_SENTINEL).is_file():
        parser.error("isolated AWE snapshot required; refusing production state")
    spec, cases, digest = load_benchmark(opts.benchmark)
    if opts.inspect_only:
        # An actual read-only retrieval check on the frozen memory snapshot,
        # not a fabricated model score or evidence of improved reasoning.
        from wechat_traffic_agent.memory_retrieval import MemoryRouter
        router = MemoryRouter(root, enable_vectors=(opts.retrieval_mode == "full"))
        retrieval = {
            case.case_id: len(router.recall(case.observation, limit=8, access_scope=None))
            for case in cases
        }
        print(json.dumps({
            "status": "READY_FOR_MODEL_EVALUATION",
            "cases": len(cases),
            "benchmark_id": spec["benchmark_id"],
            "benchmark_sha256": digest,
            "snapshot_sha256": snapshot_digest(root),
            "review_status": spec.get("review_status", "PROVISIONAL"),
            "retrieval_mode": opts.retrieval_mode,
            "model_calls": len(cases) * 2,
            "retrieval_candidates_by_case": retrieval,
            "results_claimed": False,
        }, ensure_ascii=False))
        return 0
    provider = build_chatgpt_only_provider(state_root=root)
    report = asyncio.run(evaluate_learning(
        provider=provider, snapshot_root=root, benchmark_path=opts.benchmark,
        ledger_path=opts.ledger, model_id=opts.model_id,
        retrieval_mode=opts.retrieval_mode,
    ))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["scorecard"]["cases_scored"] == len(cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
