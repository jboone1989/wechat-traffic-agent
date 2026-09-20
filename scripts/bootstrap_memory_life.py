#!/usr/bin/env python3
"""Idempotently project historical real content-cycle outcomes into Memory System.

This reads existing canonical cycle artifacts only. It does not invent audience outcomes
or rewrite trajectory; it forms episodes/procedures/knowledge with explicit artifact refs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wechat_traffic_agent.memory_life import MemoryLifeRuntime


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    state_root = args.state_root
    cycles_root = state_root / "content" / "continuous" / "cycles"
    life = MemoryLifeRuntime(state_root)
    rows = sorted(cycles_root.glob("*/experience.json"))[-max(1, args.limit):]
    recorded = already = skipped = 0
    last: dict[str, Any] = {}
    for experience_path in rows:
        folder = experience_path.parent
        experience = _read(experience_path)
        cycle = str(experience.get("cycle") or folder.name).strip()
        draft_sha = str(experience.get("draft_sha256") or "").strip()
        fingerprint = str(experience.get("source_fingerprint") or "").strip()
        if not cycle or not draft_sha or not fingerprint:
            skipped += 1
            continue
        result = _read(folder / "result.json")
        inputs = _read(folder / "inputs.json")
        passed = bool(experience.get("automated_checks_passed"))
        status = str(result.get("status") or ("DRAFT_CREATED" if passed else "HELD_BY_CHECKS"))
        floor = experience.get("evidence_floor_check") or {}
        floor_status = str(floor.get("status") or "") if isinstance(floor, dict) else ""
        refs = [f"cycle:{cycle}", "draft-sha256:" + draft_sha, "source-fingerprint:" + fingerprint]
        observed_at = str(inputs.get("now") or "")
        last = life.observe_process_outcome(
            process_id="content-cycle:" + cycle,
            topic="content-cycle-history",
            action="content-create-verify-publish",
            success=passed,
            outcome=status,
            evidence_refs=refs,
            capabilities=["content.generate", "content.verify", "content.publish"],
            steps=["select evidence", "draft content", "run evidence and safety checks", "publish only after checks pass"],
            verification=["automated_checks_passed=true", "persist draft and source fingerprints"],
            failure_mode="" if passed else (floor_status or "content_checks_failed"),
            recovery_step="" if passed else "repair missing evidence or failed checks before publishing",
            expected_cost={"word_count": int(experience.get("word_count") or 0)},
            observed_at=observed_at,
        )
        if last.get("status") == "ALREADY_RECORDED":
            already += 1
        else:
            recorded += 1
    print(json.dumps({
        "status": "BOOTSTRAPPED",
        "cycles_scanned": len(rows),
        "recorded": recorded,
        "already_recorded": already,
        "skipped": skipped,
        "last": last,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
