from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.game_intelligence import (
    GameIntelligence,
    MIN_CURRICULUM_EPISODES,
)
import wechat_traffic_agent.game_intelligence as game_intelligence_module


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def test_world_model_skill_library_and_macro_discovery(tmp_path):
    engine = GameIntelligence(tmp_path)
    actions = ("RIGHT", "UP", "RIGHT")
    for episode in range(4):
        for index, action in enumerate(actions):
            engine.record_transition(
                environment="MAZE",
                episode_id=f"maze:{episode}",
                state_key=f"s{index}",
                action=action,
                reward=0.2,
                next_state_key=f"s{index + 1}",
                observed_at=NOW + timedelta(seconds=episode * 10 + index),
            )

    prediction = engine.predict(
        environment="MAZE",
        state_key="s0",
        skill_id="MAZE:RIGHT",
    )
    assert prediction["status"] == "EMPIRICAL_STATE_SKILL_MODEL"
    assert prediction["expected_reward"] == 0.2
    assert prediction["likely_next_state"] == "s1"

    macros = engine.discover_macro_skills("MAZE", min_support=3)
    assert macros
    assert any(row["actions"][:2] == ["RIGHT", "UP"] for row in macros)
    assert engine.projection()["macro_skill_count"] >= 1


def test_episode_dream_and_curriculum_require_real_episode_outcomes(tmp_path):
    engine = GameIntelligence(tmp_path)
    for index in range(MIN_CURRICULUM_EPISODES):
        result = engine.record_episode(
            environment="FORAGING",
            episode_id=f"forage:{index}",
            reward=0.8,
            success=True,
            metrics={"foods": 2},
            observed_at=NOW + timedelta(minutes=index),
        )

    assert result["curriculum"]["level"] == 2
    dream = engine.dream_replay(environment="FORAGING")
    assert dream["reward_authority"] == "NONE_OFFLINE_REPLAY_DOES_NOT_REWARD"
    assert all(row["requires_prospective_world_validation"] for row in dream["challengers"])


def test_self_play_population_uses_measured_matches(tmp_path):
    engine = GameIntelligence(tmp_path)
    strategies = {
        "balanced-v1": {"aggression": 0.35, "reserve_energy": 0.25},
        "raider-v1": {"aggression": 0.80, "reserve_energy": 0.15},
        "fortress-v1": {"aggression": 0.20, "reserve_energy": 0.45},
    }
    summary = engine.register_population("SCREEPS", strategies)
    assert summary["member_count"] == 3

    pair = engine.select_self_play_pair("SCREEPS", seed=3)
    assert pair is not None and pair[0] != pair[1]
    summary = engine.record_self_play_match(
        environment="SCREEPS",
        left=pair[0],
        right=pair[1],
        left_reward=0.7,
        episode_id="screeps:selfplay:1",
        observed_at=NOW,
    )
    assert summary["match_count"] == 1
    assert summary["members"][pair[0]]["wins"] == 1
    assert summary["members"][pair[1]]["losses"] == 1


def test_neural_temperament_changes_strategy_fit_without_faking_reward(tmp_path):
    engine = GameIntelligence(tmp_path)
    strategies = {
        "economy": {
            "aggression": 0.05,
            "reserve_energy": 0.55,
            "soldier_target": 0,
            "worker_target": 7,
        },
        "raider": {
            "aggression": 0.9,
            "reserve_energy": 0.1,
            "soldier_target": 6,
            "worker_target": 3,
        },
    }
    high_aggression = {
        "exploration": 0.2,
        "aggression": 0.95,
        "resource_pressure": 0.1,
        "caution": 0.1,
    }
    high_caution = {
        "exploration": 0.1,
        "aggression": 0.05,
        "resource_pressure": 0.9,
        "caution": 0.9,
    }
    assert engine.choose_strategy_for_temperament(
        strategies, environment="SCREEPS", temperament=high_aggression
    ) == "raider"
    assert engine.choose_strategy_for_temperament(
        strategies, environment="SCREEPS", temperament=high_caution
    ) == "economy"
    assert engine.projection()["episode_count"] == 0


def test_cross_game_transfer_uses_shared_skill_families(tmp_path):
    engine = GameIntelligence(tmp_path)
    engine.record_transition(
        environment="MAZE",
        episode_id="maze:1",
        state_key="0,0",
        action="RIGHT",
        reward=0.7,
        next_state_key="1,0",
        observed_at=NOW,
    )
    engine.record_transition(
        environment="FORAGING",
        episode_id="forage:1",
        state_key="a",
        action="RIGHT",
        reward=0.6,
        next_state_key="b",
        observed_at=NOW,
    )
    result = engine.evaluate_transfer(
        source_environment="MAZE",
        target_environment="FORAGING",
        now=NOW,
    )
    assert result["shared_family_count"] >= 1
    assert "NAVIGATE" in result["shared_skill_families"]
    assert result["interpretation"] == "MEASURED_CROSS_GAME_REUSE"


def test_macro_skill_becomes_executable_then_requires_real_validation(tmp_path):
    engine = GameIntelligence(tmp_path)
    for episode in range(4):
        engine.record_transition(
            environment="MAZE",
            episode_id=f"macro:{episode}",
            state_key="0,0",
            action="RIGHT",
            reward=0.4,
            next_state_key="1,0",
            observed_at=NOW + timedelta(seconds=episode * 10),
        )
        engine.record_transition(
            environment="MAZE",
            episode_id=f"macro:{episode}",
            state_key="1,0",
            action="UP",
            reward=0.4,
            next_state_key="1,1",
            observed_at=NOW + timedelta(seconds=episode * 10 + 1),
        )
    engine.discover_macro_skills("MAZE", min_support=3)
    macro = engine.choose_macro_skill(
        "MAZE",
        valid_actions=("RIGHT", "UP", "LEFT", "DOWN"),
        seed=1,
    )
    assert macro is not None
    assert macro["actions"] == ["RIGHT", "UP"]
    assert macro["requires_prospective_world_validation"] is True

    for index in range(3):
        result = engine.record_macro_execution(
            environment="MAZE",
            skill_id=macro["skill_id"],
            execution_id=f"macro-run:{index}",
            actions=macro["actions"],
            reward=0.8,
            observed_at=NOW + timedelta(minutes=index + 1),
        )
    assert result["prospectively_validated"] is True
    skill = engine.state["skills"][macro["skill_id"]]
    assert skill["requires_prospective_world_validation"] is False


def test_macro_skill_is_quarantined_after_repeated_negative_real_execution(tmp_path):
    engine = GameIntelligence(tmp_path)
    for episode in range(3):
        engine.record_transition(
            environment="PONG",
            episode_id=f"bad-macro:{episode}",
            state_key="s0",
            action="UP",
            reward=0.3,
            next_state_key="s1",
            observed_at=NOW + timedelta(seconds=episode * 10),
        )
        engine.record_transition(
            environment="PONG",
            episode_id=f"bad-macro:{episode}",
            state_key="s1",
            action="DOWN",
            reward=0.3,
            next_state_key="s2",
            observed_at=NOW + timedelta(seconds=episode * 10 + 1),
        )
    engine.discover_macro_skills("PONG", min_support=3)
    macro = engine.choose_macro_skill(
        "PONG",
        valid_actions=("UP", "DOWN", "STAY"),
        seed=1,
    )
    assert macro is not None

    for index in range(3):
        result = engine.record_macro_execution(
            environment="PONG",
            skill_id=macro["skill_id"],
            execution_id=f"bad-run:{index}",
            actions=macro["actions"],
            reward=-0.5,
            observed_at=NOW + timedelta(minutes=index + 1),
        )

    assert result["prospectively_rejected"] is True
    skill = engine.state["skills"][macro["skill_id"]]
    assert skill["prospective_status"] == "REJECTED"
    assert engine.choose_macro_skill(
        "PONG",
        valid_actions=("UP", "DOWN", "STAY"),
        seed=2,
    ) is None


def test_population_opponent_prefers_least_tested_pair(tmp_path):
    engine = GameIntelligence(tmp_path)
    engine.register_population(
        "SCREEPS",
        {
            "a": {"aggression": 0.2},
            "b": {"aggression": 0.5},
            "c": {"aggression": 0.8},
        },
    )
    engine.record_self_play_match(
        environment="SCREEPS",
        left="a",
        right="b",
        left_reward=0.5,
        episode_id="ab:1",
        observed_at=NOW,
    )
    assert engine.select_population_opponent(
        "SCREEPS", strategy_id="a", seed=4
    ) == "c"


def test_concurrent_instances_merge_experience_instead_of_overwriting(tmp_path):
    first = GameIntelligence(tmp_path)
    second = GameIntelligence(tmp_path)

    first.record_episode(
        environment="MAZE",
        episode_id="concurrent:maze",
        reward=0.4,
        success=True,
        observed_at=NOW,
    )
    second.record_episode(
        environment="FORAGING",
        episode_id="concurrent:foraging",
        reward=0.5,
        success=True,
        observed_at=NOW + timedelta(seconds=1),
    )
    first.record_transition(
        environment="MAZE",
        episode_id="concurrent:maze",
        state_key="0,0",
        action="RIGHT",
        reward=0.2,
        next_state_key="1,0",
        observed_at=NOW + timedelta(seconds=2),
    )
    second.record_transition(
        environment="FORAGING",
        episode_id="concurrent:foraging",
        state_key="a",
        action="UP",
        reward=0.3,
        next_state_key="b",
        observed_at=NOW + timedelta(seconds=3),
    )

    restored = GameIntelligence(tmp_path)
    ids = {row["episode_id"] for row in restored.state["episodes"]}
    assert ids == {"concurrent:maze", "concurrent:foraging"}
    assert restored.projection()["transition_count"] == 2


def test_batch_updates_keep_in_session_learning_and_commit_once(monkeypatch, tmp_path):
    engine = GameIntelligence(tmp_path)
    writes = []
    real_write = game_intelligence_module._write

    def counted_write(path, payload):
        writes.append(path)
        real_write(path, payload)

    monkeypatch.setattr(game_intelligence_module, "_write", counted_write)
    with engine.batch_updates():
        for index in range(12):
            engine.record_transition(
                environment="PONG",
                episode_id="batch:pong:1",
                state_key=f"s{index}",
                action="UP",
                reward=0.2,
                next_state_key=f"s{index + 1}",
                observed_at=NOW + timedelta(seconds=index),
            )
        prediction = engine.predict(
            environment="PONG",
            state_key="s11",
            skill_id="PONG:UP",
        )
        assert prediction["samples"] == 1
        assert writes == []

    assert len(writes) == 1
    restored = GameIntelligence(tmp_path)
    assert restored.projection()["transition_count"] == 12


def test_graduation_requires_broad_real_game_evidence(tmp_path):
    engine = GameIntelligence(tmp_path)
    base = ("PONG", "MAZE", "FORAGING", "ROVER")
    engine.state["episodes"] = [
        {"environment": env, "episode_id": f"{env}:{index}", "reward": 0.1}
        for env in base
        for index in range(40)
    ]
    engine.state["transitions"] = [
        {
            "environment": env,
            "episode_id": f"{env}:{index // 20}",
            "state_key": str(index),
            "action": "RIGHT",
            "reward": 0.01,
            "next_state_key": str(index + 1),
        }
        for env in base
        for index in range(750)
    ]
    engine.state["skills"] = {
        "macro:maze:a": {"kind": "MACRO", "prospectively_validated": True},
        "macro:foraging:b": {"kind": "MACRO", "prospectively_validated": True},
    }
    engine.state["transfer_evaluations"] = [
        {"interpretation": "MEASURED_CROSS_GAME_REUSE"} for _ in range(3)
    ]
    engine.state["population"] = {
        "SCREEPS": {
            "matches": [{"episode_id": f"s:{i}"} for i in range(50)],
            "members": {
                name: {
                    "games": 5,
                    "wins": 2,
                    "losses": 3,
                    "reward_sum": 0.0,
                    "params": {},
                }
                for name in ("a", "b", "c", "d")
            },
        }
    }

    status = engine.graduation_status()
    assert status["graduated"] is True
    assert status["progress"] == 1.0
    assert status["next_stage"] == "HARDER_OPEN_WORLD_CURRICULUM"


def test_graduation_environment_order_prioritizes_largest_deficit(tmp_path):
    engine = GameIntelligence(tmp_path)
    episode_counts = {
        "PONG": 29,
        "MAZE": 25,
        "FORAGING": 22,
        "ROVER": 12,
    }
    transition_counts = {
        "PONG": 892,
        "MAZE": 803,
        "FORAGING": 639,
        "ROVER": 360,
    }
    engine.state["episodes"] = [
        {"environment": env, "episode_id": f"{env}:{index}", "reward": 0.0}
        for env, count in episode_counts.items()
        for index in range(count)
    ]
    engine.state["transitions"] = [
        {
            "environment": env,
            "episode_id": f"{env}:0",
            "state_key": str(index),
            "action": "RIGHT",
            "reward": 0.0,
            "next_state_key": str(index + 1),
        }
        for env, count in transition_counts.items()
        for index in range(count)
    ]

    assert engine.graduation_environment_order(
        ("PONG", "MAZE", "FORAGING", "ROVER")
    ) == ("ROVER", "FORAGING", "MAZE", "PONG")


def test_foundation_graduation_is_durably_latched(tmp_path):
    engine = GameIntelligence(tmp_path)
    base = ("PONG", "MAZE", "FORAGING", "ROVER")
    engine.state["episodes"] = [
        {"environment": env, "episode_id": f"{env}:{i}", "reward": 0.1}
        for env in base for i in range(40)
    ]
    engine.state["transitions"] = [
        {
            "environment": env,
            "episode_id": f"{env}:0",
            "state_key": str(i),
            "action": "A",
            "reward": 0.1,
            "next_state_key": str(i + 1),
        }
        for env in base for i in range(750)
    ]
    engine.state["skills"] = {
        "macro:maze:a": {"kind": "MACRO", "prospectively_validated": True},
        "macro:pong:b": {"kind": "MACRO", "prospectively_validated": True},
    }
    engine.state["transfer_evaluations"] = [
        {"interpretation": "MEASURED_CROSS_GAME_REUSE"} for _ in range(3)
    ]
    engine.state["population"] = {
        "SCREEPS": {
            "matches": [{"episode_id": f"s:{i}"} for i in range(50)],
            "members": {
                name: {"games": 5, "wins": 3, "losses": 2, "reward_sum": 1.0, "params": {}}
                for name in ("a", "b", "c", "d")
            },
        }
    }
    first = engine.graduation_status()
    assert first["graduated"] is True
    assert first["latched"] is True

    engine.state["episodes"] = []
    engine.state["transitions"] = []
    engine._persist()
    reloaded = GameIntelligence(tmp_path).graduation_status()
    assert reloaded["graduated"] is True
    assert reloaded["latched"] is True
    assert reloaded["progress"] == 1.0


def test_open_world_graduation_requires_depth_macros_transfer_and_progress(tmp_path):
    engine = GameIntelligence(tmp_path)
    worlds = ("WORLD_0001", "WORLD_0002", "WORLD_0003")
    engine.state["episodes"] = [
        {"environment": env, "episode_id": f"{env}:{i}", "reward": 0.4}
        for env in worlds for i in range(20)
    ]
    engine.state["transitions"] = [
        {
            "environment": env,
            "episode_id": f"{env}:0",
            "state_key": str(i),
            "action": "A0",
            "reward": 0.1,
            "next_state_key": str(i + 1),
        }
        for env in worlds for i in range(500)
    ]
    engine.state["skills"] = {
        f"macro:{env.lower()}:x": {
            "kind": "MACRO",
            "prospectively_validated": True,
        }
        for env in worlds
    }
    engine.state["transfer_evaluations"] = [
        {
            "source_environment": worlds[i % 3],
            "target_environment": worlds[(i + 1) % 3],
            "interpretation": "MEASURED_CROSS_GAME_REUSE",
        }
        for i in range(6)
    ]
    engine.state["curricula"] = {
        "WORLD_0001": {"level": 2},
        "WORLD_0002": {"level": 3},
        "WORLD_0003": {"level": 1},
    }
    status = engine.open_world_graduation_status(worlds)
    assert status["graduated"] is True
    assert status["progress"] == 1.0
    assert status["next_stage"] == "LONG_HORIZON_ADVERSARIAL_OPEN_WORLD"
