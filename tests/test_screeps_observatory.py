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
    assert result["display_mode"] == "LIVE"
    assert result["tick"] == 42
    assert result["frame"]["ferro"]["creeps"] == 5
    assert result["research_context"]["arm"] == "EXPLORATION"
    assert result["research"]["open_episode_count"] == 1
    assert len(result["frames"]) == 1
    assert result["function_success_is_reward"] is False


def test_screeps_observatory_keeps_latest_replay_when_training_is_deferred(tmp_path):
    researcher = ScreepsResearcher(tmp_path)
    trial = researcher.begin_episode(
        strategy_id="raider-v1",
        opponent_id="screeps-opponent:economy-v1",
        seed=9,
    )
    outcome = researcher.settle_episode(
        {
            "schema_version": "ferro-screeps-episode.v2",
            "episode_id": trial["episode_id"],
            "evidence_ref": f"screeps-match:{trial['episode_id']}",
            "metrics": {
                "survived": True,
                "match_outcome": 1,
                "owned_rooms_start": 1,
                "owned_rooms_end": 1,
                "energy_start": 300,
                "energy_end": 900,
                "controller_progress_start": 0,
                "controller_progress_end": 0,
                "hostile_creeps_destroyed": 2,
                "own_creeps_lost": 0,
                "structures_start": 1,
                "structures_end": 1,
            },
        }
    )
    app = tmp_path / "application"
    replay_dir = app / "screeps_replays"
    replay_dir.mkdir(parents=True, exist_ok=True)
    frame = {
        "tick": 60,
        "rooms": ["W0N1", "W1N1"],
        "ferro": {"creeps": 6, "soldiers": 3, "energy": 900, "spawn_alive": True},
        "opponent": {"creeps": 2, "soldiers": 0, "energy": 200, "spawn_alive": False},
        "tactical": {"mode": "WON", "momentum": "FERRO"},
        "objects": [],
    }
    safe = trial["episode_id"].replace(":", "-")
    (replay_dir / f"{safe}.json").write_text(
        json.dumps(
            {
                "episode_id": trial["episode_id"],
                "status": "FINISHED",
                "winner": "FERRO",
                "reward": outcome.reward,
                "frames": [frame],
            }
        ),
        encoding="utf-8",
    )
    (app / "screeps_live.json").write_text(
        json.dumps(
            {
                "status": "RESOURCE_DEFERRED",
                "observed_at": "2026-09-27T12:50:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = ScreepsObservatory(tmp_path).snapshot()

    assert result["playing"] is False
    assert result["status"] == "RESOURCE_DEFERRED"
    assert result["displaying_latest_replay"] is True
    assert result["display_mode"] == "LAST_REPLAY"
    assert result["episode_id"] == trial["episode_id"]
    assert result["frame"]["tick"] == 60
    assert result["frame"]["tactical"]["mode"] == "WON"
    assert result["research_context"]["strategy_id"] == "raider-v1"
    assert result["winner"] == "FERRO"
