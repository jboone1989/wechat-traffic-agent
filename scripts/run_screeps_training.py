#!/usr/bin/env python3
"""Run resource-bounded Screeps PvP episodes and feed them to Ferro learning."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.screeps_training import ScreepsResearcher  # noqa: E402


DEFAULT_OPPONENTS = ("balanced-v1", "fortress-v1", "raider-v1", "economy-v1")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="resource-bounded Ferro Screeps trainer")
    parser.add_argument("--state-root", type=Path, default=Path("/home/ubuntu/content-agent-state"))
    parser.add_argument("--lab-root", type=Path, default=Path("/home/ubuntu/screeps-lab"))
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--ticks", type=int, default=220)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--min-free-memory-mib", type=int, default=700)
    parser.add_argument("--min-free-disk-gib", type=float, default=8.0)
    parser.add_argument("--max-load-1m", type=float, default=1.75)
    parser.add_argument("--match-timeout-seconds", type=int, default=240)
    parser.add_argument("--public-live-path", type=Path, default=None)
    parser.add_argument("--opponents", default=",".join(DEFAULT_OPPONENTS))
    return parser


def _resource_snapshot(path: Path) -> dict[str, Any]:
    available_bytes = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                available_bytes = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        available_bytes = 0
    usage = shutil.disk_usage(path)
    load1 = float(os.getloadavg()[0]) if hasattr(os, "getloadavg") else 0.0
    return {
        "available_memory_bytes": available_bytes,
        "free_disk_bytes": int(usage.free),
        "load_1m": round(load1, 6),
    }


def _resource_admission(snapshot: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    memory_ok = int(snapshot["available_memory_bytes"]) >= int(args.min_free_memory_mib) * 1024 * 1024
    disk_ok = int(snapshot["free_disk_bytes"]) >= int(float(args.min_free_disk_gib) * 1024**3)
    load_ok = float(snapshot["load_1m"]) <= float(args.max_load_1m)
    return {
        "allowed": bool(memory_ok and disk_ok and load_ok),
        "memory_ok": memory_ok,
        "disk_ok": disk_ok,
        "load_ok": load_ok,
        "limits": {
            "min_free_memory_mib": int(args.min_free_memory_mib),
            "min_free_disk_gib": float(args.min_free_disk_gib),
            "max_load_1m": float(args.max_load_1m),
        },
        "snapshot": snapshot,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _run_match(
    *,
    node: str,
    harness: Path,
    input_path: Path,
    timeout_seconds: int,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        [node, str(harness), str(input_path)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(30, int(timeout_seconds)))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return 124, stdout, (stderr + "\nMATCH_TIMEOUT").strip()
    return int(process.returncode or 0), stdout, stderr


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            return row
    raise ValueError("Screeps harness produced no JSON result")


def main() -> int:
    args = _parser().parse_args()
    args.state_root = args.state_root.expanduser().resolve()
    args.lab_root = args.lab_root.expanduser().resolve()
    args.public_live_path = args.public_live_path.expanduser().resolve() if args.public_live_path else None
    args.lab_root.mkdir(parents=True, exist_ok=True)
    lock_handle = (args.lab_root / "trainer.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({
            "status": "ALREADY_RUNNING",
            "lock": str(args.lab_root / "trainer.lock"),
        }, sort_keys=True))
        return 0
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()) + "\n")
    lock_handle.flush()
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node executable is unavailable")
    harness = ROOT / "scripts" / "screeps_match.js"
    if not harness.exists():
        raise RuntimeError(f"Screeps match harness missing: {harness}")
    module = args.lab_root / "node_modules" / "@screepts" / "screeps-test-server"
    if not module.exists():
        raise RuntimeError(f"Screeps test server dependency missing: {module}")

    opponents = tuple(
        item.strip()
        for item in str(args.opponents).split(",")
        if item.strip()
    )
    if not opponents:
        raise ValueError("at least one opponent strategy is required")

    researcher = ScreepsResearcher(args.state_root)
    live_path = args.state_root / "application" / "screeps_live.json"
    replay_root = args.state_root / "application" / "screeps_replays"
    def write_live(payload: dict[str, Any]) -> None:
        _write_json(live_path, payload)
        if args.public_live_path is not None:
            _write_json(args.public_live_path, payload)
    base_seed = (
        int(args.seed) if args.seed is not None
        else int(researcher.projection().get("episode_count") or 0) + 1
    )
    results: list[dict[str, Any]] = []
    for index in range(max(1, min(64, int(args.episodes)))):
        snapshot = _resource_snapshot(args.lab_root)
        admission = _resource_admission(snapshot, args)
        if not admission["allowed"]:
            write_live({
                "schema_version": "ferro-screeps-live.v1",
                "status": "RESOURCE_DEFERRED",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "resource_admission": admission,
                "research": researcher.projection(),
            })
            print(json.dumps({
                "status": "RESOURCE_DEFERRED",
                "completed_episodes": len(results),
                "resource_admission": admission,
                "research": researcher.projection(),
            }, ensure_ascii=False, sort_keys=True))
            return 75

        seed = base_seed + index
        choice = researcher.choose_strategy(seed=seed)
        opponent_id = opponents[seed % len(opponents)]
        opponent = researcher.strategy(opponent_id)
        trial = researcher.begin_episode(
            strategy_id=str(choice["strategy_id"]),
            opponent_id=f"screeps-opponent:{opponent_id}",
            seed=seed,
            prediction=(
                f"{choice['strategy_id']} should preserve the base and outperform "
                f"{opponent_id} on measured Screeps utility"
            ),
            now=datetime.now(timezone.utc),
        )
        episode_id = str(trial["episode_id"])
        safe_id = episode_id.replace(":", "-")
        replay_path = replay_root / f"{safe_id}.json"
        match_input = {
            "episode_id": episode_id,
            "lab_root": str(args.lab_root),
            "live_path": str(live_path),
            "public_live_path": str(args.public_live_path) if args.public_live_path else None,
            "replay_path": str(replay_path),
            "frame_interval": 3,
            "ticks": max(20, min(1500, int(args.ticks))),
            "port": 22125 + (seed % 40),
            "ferro_room": "W0N1",
            "opponent_room": "W1N1",
            "strategy": {
                key: value
                for key, value in choice.items()
                if key in {
                    "worker_target", "builder_target", "upgrader_target",
                    "soldier_target", "aggression", "reserve_energy",
                }
            },
            "opponent_strategy": {
                key: value
                for key, value in opponent.items()
                if key in {
                    "worker_target", "builder_target", "upgrader_target",
                    "soldier_target", "aggression", "reserve_energy",
                }
            },
            "research": {
                "arm": choice.get("research_arm"),
                "trial_id": choice.get("trial_id"),
                "strategy_id": choice.get("strategy_id"),
                "opponent_id": opponent_id,
                "seed": seed,
            },
        }
        input_path = args.lab_root / "inputs" / f"{safe_id}.json"
        result_path = args.lab_root / "results" / f"{safe_id}.json"
        failure_path = args.lab_root / "failures" / f"{safe_id}.json"
        _write_json(input_path, match_input)

        returncode, stdout, stderr = _run_match(
            node=node,
            harness=harness,
            input_path=input_path,
            timeout_seconds=args.match_timeout_seconds,
        )
        if returncode != 0:
            failure = {
                "schema_version": "ferro-screeps-harness-failure.v1",
                "episode_id": episode_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "returncode": returncode,
                "stderr_tail": stderr[-4000:],
                "stdout_tail": stdout[-4000:],
            }
            _write_json(failure_path, failure)
            cancellation = researcher.abandon_episode(
                episode_id,
                reason=f"HARNESS_EXIT_{returncode}",
                evidence_ref=f"screeps-harness-failure:{safe_id}",
            )
            live = _read_json(live_path)
            live.update({
                "schema_version": "ferro-screeps-live.v1",
                "status": "HARNESS_FAILED",
                "episode_id": episode_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "error": f"HARNESS_EXIT_{returncode}",
                "research": researcher.projection(),
            })
            write_live(live)
            results.append({"status": "HARNESS_FAILED", **cancellation})
            continue

        try:
            row = _last_json_line(stdout)
            if str(row.get("episode_id") or "") != episode_id:
                raise ValueError("Screeps result episode identity mismatch")
            metrics = row.get("metrics")
            telemetry = row.get("telemetry")
            if not isinstance(metrics, dict) or not isinstance(telemetry, dict):
                raise ValueError("Screeps result lacks measured telemetry")
            ferro_telemetry = telemetry.get("ferro")
            if not isinstance(ferro_telemetry, dict) or not ferro_telemetry.get("memory"):
                raise ValueError("Ferro bot produced no execution telemetry")
        except Exception as exc:
            failure = {
                "schema_version": "ferro-screeps-harness-failure.v1",
                "episode_id": episode_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "returncode": 0,
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "stderr_tail": stderr[-4000:],
                "stdout_tail": stdout[-4000:],
            }
            _write_json(failure_path, failure)
            cancellation = researcher.abandon_episode(
                episode_id,
                reason=f"INVALID_RESULT:{type(exc).__name__}",
                evidence_ref=f"screeps-harness-failure:{safe_id}",
            )
            results.append({"status": "INVALID_RESULT", **cancellation})
            continue

        _write_json(result_path, row)
        settled = researcher.settle_episode(row)
        research = researcher.projection()
        replay = _read_json(replay_path)
        if replay:
            replay.update({
                "status": "FINISHED",
                "reward": settled.reward,
                "winner": row.get("winner"),
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "research": research,
            })
            _write_json(replay_path, replay)
        live = _read_json(live_path)
        live.update({
            "schema_version": "ferro-screeps-live.v1",
            "status": "FINISHED",
            "episode_id": settled.episode_id,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "winner": row.get("winner"),
            "reward": settled.reward,
            "research": research,
        })
        write_live(live)
        results.append({
            "status": "SETTLED",
            "episode_id": settled.episode_id,
            "strategy_id": settled.strategy_id,
            "opponent_id": settled.opponent_id,
            "reward": settled.reward,
            "winner": row.get("winner"),
            "evidence_ref": settled.evidence_ref,
        })
        # Successful matches have already emitted their durable compact result.
        # Remove the per-match Loki DB/logs so continuous training cannot grow
        # disk usage without bound.
        match_id = "".join(ch if ch.isalnum() or ch in "_.:-" else "_" for ch in episode_id)
        shutil.rmtree(args.lab_root / "matches" / match_id, ignore_errors=True)

    print(json.dumps({
        "status": "COMPLETED",
        "completed_episodes": len(results),
        "results": results,
        "research": researcher.projection(),
        "resource_snapshot": _resource_snapshot(args.lab_root),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
