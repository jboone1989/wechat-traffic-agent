"""Bounded public projection for Ferro's Screeps training world."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .screeps_training import ScreepsResearcher


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _age_seconds(value: object) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


class ScreepsObservatory:
    """Read-only, privacy-bounded live/replay view of Screeps episodes."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.application_root = self.state_root / "application"
        self.live_path = self.application_root / "screeps_live.json"
        self.replay_root = self.application_root / "screeps_replays"

    def snapshot(self, *, include_frames: bool = False) -> dict[str, Any]:
        live = _read(self.live_path)
        researcher = ScreepsResearcher(self.state_root)
        research = researcher.projection()
        episode_id = str(live.get("episode_id") or "")
        displaying_latest_replay = False
        if not episode_id:
            episodes = [
                row
                for row in (researcher.state.get("episodes") or ())
                if isinstance(row, Mapping) and row.get("episode_id")
            ]
            if episodes:
                episode_id = str(episodes[-1]["episode_id"])
                displaying_latest_replay = True
        replay: dict[str, Any] = {}
        if episode_id:
            safe_id = episode_id.replace(":", "-")
            replay = _read(self.replay_root / f"{safe_id}.json")
        frames = [
            dict(row)
            for row in (replay.get("frames") or ())
            if isinstance(row, Mapping)
        ][-120:]
        frame = live.get("frame") if isinstance(live.get("frame"), Mapping) else None
        if frame is None and frames:
            frame = frames[-1]

        recent = []
        for row in list(researcher.state.get("episodes") or ())[-8:]:
            if not isinstance(row, Mapping):
                continue
            recent.append({
                "episode_id": row.get("episode_id"),
                "strategy_id": row.get("strategy_id"),
                "opponent_id": row.get("opponent_id"),
                "reward": row.get("reward"),
                "observed_at": row.get("observed_at"),
                "match_outcome": (row.get("causal_factors") or {}).get("match_outcome")
                if isinstance(row.get("causal_factors"), Mapping)
                else None,
            })

        status = str(live.get("status") or "IDLE").upper()
        payload = {
            "schema_version": "ferro-screeps-observatory.v1",
            "status": status,
            "playing": status == "RUNNING",
            "episode_id": episode_id or None,
            "displaying_latest_replay": displaying_latest_replay,
            "observed_at": live.get("observed_at"),
            "age_seconds": _age_seconds(live.get("observed_at")),
            "tick": live.get("tick") or (frame or {}).get("tick"),
            "ticks_requested": live.get("ticks_requested"),
            "winner": live.get("winner") or replay.get("winner"),
            "reward": live.get("reward") if isinstance(live.get("reward"), (int, float)) else replay.get("reward"),
            "strategy": live.get("strategy") if isinstance(live.get("strategy"), Mapping) else replay.get("strategy"),
            "opponent_strategy": live.get("opponent_strategy")
            if isinstance(live.get("opponent_strategy"), Mapping)
            else replay.get("opponent_strategy"),
            "research_context": live.get("research")
            if isinstance(live.get("research"), Mapping)
            else replay.get("research"),
            "frame": dict(frame) if isinstance(frame, Mapping) else None,
            "frames_recorded": int(live.get("frames_recorded") or len(frames)),
            "research": research,
            "recent_episodes": recent,
            "function_success_is_reward": False,
            "public_projection": True,
        }
        if include_frames:
            payload["frames"] = frames
        return payload


__all__ = ["ScreepsObservatory"]
