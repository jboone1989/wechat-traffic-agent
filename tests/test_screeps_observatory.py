from __future__ import annotations

import json

from wechat_traffic_agent.screeps_observatory import ScreepsObservatory
from wechat_traffic_agent.screeps_training import ScreepsResearcher


def test_screeps_observatory_projects_live_frame_and_replay(tmp_path):
    researcher = ScreepsResearcher(tmp_path)
    trial = researcher.begin_episode(
        strategy_id="balanced-v1",
        opponent_id="screeps-opponent:raider-v1",
        seed=7,
    )
    app = tmp_path / "application"
    replay_dir = app / "screeps_replays"
    replay_dir.mkdir(parents=True, exist_ok=True)
    frame = {
        "tick": 42,
        "rooms": ["W0N1", "W1N1"],
        "ferro": {"creeps": 5, "soldiers": 1, "energy": 1200, "spawn_alive": True},
        "opponent": {"creeps": 4, "soldiers": 2, "energy": 900, "spawn_alive": True},
        "objects": [{"type": "spawn", "side": "ferro", "room": "W0N1", "x": 15, "y": 15}],
    }
    live = {
        "schema_version": "ferro-screeps-live.v1",
        "status": "RUNNING",
        "episode_id": trial["episode_id"],
        "observed_at": "2026-09-27T12:00:00+00:00",
        "tick": 42,
        "ticks_requested": 180,
        "research": {"arm": "EXPLORATION", "strategy_id": "balanced-v1"},
        "frame": frame,
        "frames_recorded": 3,
    }
    (app / "screeps_live.json").write_text(json.dumps(live), encoding="utf-8")
    safe = trial["episode_id"].replace(":", "-")
    (replay_dir / f"{safe}.json").write_text(
        json.dumps({"frames": [frame], "status": "RUNNING"}),
        encoding="utf-8",
    )

    result = ScreepsObservatory(tmp_path).snapshot(include_frames=True)

    assert result["playing"] is True
    assert result["tick"] == 42
    assert result["frame"]["ferro"]["creeps"] == 5
    assert result["research_context"]["arm"] == "EXPLORATION"
    assert result["research"]["open_episode_count"] == 1
    assert len(result["frames"]) == 1
    assert result["function_success_is_reward"] is False
