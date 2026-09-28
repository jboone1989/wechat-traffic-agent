from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from wechat_traffic_agent.neural_dreaming import FerroDreamEngine
from wechat_traffic_agent.neural_playground import FerroBrainLab


NOW = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)


class FakeSubstrate:
    def __init__(self) -> None:
        self.stimuli = []
        self.feedback = []

    def stimulate(self, currents, observed_at, *, source_event_id=None, feedback_eligible=None):
        self.stimuli.append((dict(currents), observed_at, source_event_id, feedback_eligible))
        delta = {key: float(value) * 10.0 for key, value in currents.items()}
        # Keep all normal semantic channels visible to motor selection.
        for key in (
            "curiosity", "opportunity", "prediction_due", "error_recovery",
            "task_urgency", "social_attention", "resource_pressure", "threat_risk",
        ):
            delta.setdefault(key, 0.0)
        return {
            "seq": len(self.stimuli),
            "delta_readout_hz": delta,
            "spikes": 100 + len(self.stimuli),
            "active_neurons": 50 + len(self.stimuli),
        }

    def apply_feedback(self, feedback, *, utility):
        self.feedback.append((feedback, utility))
        return {
            "status": "APPLIED",
            "changed_synapse_count": 3 if abs(float(utility)) > 0 else 0,
            "outcome_id": feedback.outcome_id,
        }


def test_brain_lab_uses_same_substrate_and_persists_all_four_worlds(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)

    results = [
        lab.run_session(environment=name, steps=3, now=NOW)
        for name in FerroBrainLab.ENVIRONMENTS
    ]

    assert all(row["status"] == "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM" for row in results)
    # One neural frame can drive several fast motor frames. Each explicit
    # 3-step session begins with a fresh neural frame, so four environments
    # produce four full-connectome stimuli but still twelve measured rewards.
    assert len(substrate.stimuli) == 4
    assert len(substrate.feedback) == 12
    assert lab.projection()["total_steps"] == 12
    assert set(lab.projection()["environments"]) == {"PONG", "MAZE", "FORAGING", "ROVER"}

    restored = FerroBrainLab(tmp_path, substrate=substrate)
    assert restored.projection()["total_steps"] == 12
    assert restored.projection()["same_production_connectome"] is True


def test_motor_frames_reuse_neural_decision_and_keep_unique_reward_outcomes(
    tmp_path: Path,
):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)

    lab.run_session(environment="PONG", steps=5, now=NOW)

    assert len(substrate.stimuli) == 2
    assert len(substrate.feedback) == 5
    source_ids = [feedback.source_event_id for feedback, _ in substrate.feedback]
    outcome_ids = [feedback.outcome_id for feedback, _ in substrate.feedback]
    assert source_ids[:3] == [source_ids[0]] * 3
    assert source_ids[3:] == [source_ids[3]] * 2
    assert source_ids[3] != source_ids[0]
    assert len(set(outcome_ids)) == 5
    recent = lab.state["recent"][-5:]
    assert [row["neural_frame_reused"] for row in recent] == [
        False, True, True, False, True
    ]
    assert all(
        row["neural_source_event_id"] == source_ids[index]
        for index, row in enumerate(recent)
    )


def test_reused_neural_frame_still_reselects_motor_action_each_world_step(
    tmp_path: Path,
):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)
    calls = []

    def choose(**kwargs):
        calls.append(kwargs["event_id"])
        action = ("UP", "DOWN", "STAY")[(len(calls) - 1) % 3]
        channels = {
            "UP": "prediction_due",
            "DOWN": "error_recovery",
            "STAY": "opportunity",
        }
        return action, channels[action]

    lab._choose = choose
    lab.run_session(environment="PONG", steps=3, now=NOW)

    assert len(substrate.stimuli) == 1
    assert len(calls) == 3
    assert [row["action"] for row in lab.state["recent"][-3:]] == [
        "UP",
        "DOWN",
        "STAY",
    ]
    assert [row["neural_frame_reused"] for row in lab.state["recent"][-3:]] == [
        False,
        True,
        True,
    ]


def test_td_policy_bootstraps_future_value_but_not_across_terminal_boundary(
    tmp_path: Path,
):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    policy = lab._policy_row("MAZE")
    policy["states"]["next"] = {"UP": 0.8}

    lab._learn_policy(
        name="MAZE",
        state_key="start",
        action="RIGHT",
        reward=0.0,
        next_state_key="next",
        terminal=False,
    )
    propagated = policy["states"]["start"]["RIGHT"]
    assert propagated > 0.0

    policy["states"]["terminal-start"] = {"RIGHT": 0.0}
    lab._learn_policy(
        name="MAZE",
        state_key="terminal-start",
        action="RIGHT",
        reward=0.0,
        next_state_key="next",
        terminal=True,
    )
    assert policy["states"]["terminal-start"]["RIGHT"] == 0.0


def test_learned_motor_skill_beats_extreme_neural_tie_break_noise(tmp_path: Path):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    lab._phase = lambda _key: 0.99
    policy = lab._policy_row("PONG")
    policy["states"]["s"] = {"UP": 0.25, "DOWN": 0.0}

    action, channel = lab._choose(
        name="PONG",
        state_key="s",
        delta={"prediction_due": -100.0, "error_recovery": 100.0},
        options=(("UP", "prediction_due"), ("DOWN", "error_recovery")),
        event_id="motor-arbitration:test",
    )

    assert action == "UP"
    assert channel == "prediction_due"


def test_world_model_uses_same_policy_state_key_as_action_selection(tmp_path: Path):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    row = lab.step("PONG", now=NOW)

    prediction = lab.game_intelligence.predict(
        environment="PONG",
        state_key=row["policy_state_key"],
        skill_id=f"PONG:{row['action']}",
    )

    assert prediction["status"] == "EMPIRICAL_STATE_SKILL_MODEL"
    assert prediction["samples"] == 1


def test_td_policy_migration_resets_only_corrupted_foundation_policies(tmp_path: Path):
    app = tmp_path / "application"
    app.mkdir(parents=True)
    state = {
        "schema_version": FerroBrainLab.SCHEMA_VERSION,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "total_steps": 10,
        "total_sessions": 2,
        "active_environment": "PONG",
        "environments": {
            name: FerroBrainLab(tmp_path, substrate=None)._default_env(name)
            for name in FerroBrainLab.ENVIRONMENTS
        },
        "policies": {
            "PONG": {"states": {"old": {"UP": 0.4}}, "updates": 1, "explorations": 0},
            "MAZE": {"states": {"old": {"RIGHT": 0.4}}, "updates": 1, "explorations": 0},
            "FORAGING": {"states": {"old": {"UP": 0.4}}, "updates": 1, "explorations": 0},
            "ROVER": {"states": {"old": {"FORWARD": 0.8}}, "updates": 1, "explorations": 0},
        },
        "recent": [],
    }
    (app / "brain_lab.json").write_text(json.dumps(state), encoding="utf-8")

    lab = FerroBrainLab(tmp_path, substrate=None)

    assert lab.state["policy_algorithm_version"] == FerroBrainLab.POLICY_ALGORITHM_VERSION
    assert "PONG" not in lab.state["policies"]
    assert "MAZE" not in lab.state["policies"]
    assert "FORAGING" not in lab.state["policies"]
    assert "ROVER" in lab.state["policies"]
    assert lab.state["learning_migrations"][-1]["environments"] == [
        "PONG",
        "MAZE",
        "FORAGING",
    ]


def test_game_reward_is_provenance_bound_to_the_step(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)

    row = lab.step("PONG", now=NOW)

    feedback, utility = substrate.feedback[-1]
    assert feedback.source_event_id == row["event_id"]
    assert feedback.outcome_id == row["event_id"] + ":reward"
    assert feedback.pathway == (row["action_channel"],)
    assert -1.0 <= utility <= 1.0


def test_persistent_policy_can_override_repeated_bad_neural_impulse(tmp_path: Path):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    options = (
        ("RIGHT", "opportunity"),
        ("LEFT", "social_attention"),
        ("UP", "prediction_due"),
    )
    delta = {"social_attention": 10.0, "opportunity": 9.0, "prediction_due": 0.0}
    state = "0,0"

    first, _ = lab._choose(
        name="MAZE", state_key=state, delta=delta, options=options, event_id="fixed-first"
    )
    assert first == "LEFT"

    for _ in range(8):
        lab._learn_policy(name="MAZE", state_key=state, action="LEFT", reward=-0.2)
        lab._learn_policy(name="MAZE", state_key=state, action="RIGHT", reward=0.04)

    learned, channel = lab._choose(
        name="MAZE", state_key=state, delta=delta, options=options, event_id="fixed-learned"
    )
    assert learned == "RIGHT"
    assert channel == "opportunity"
    assert lab.projection()["policy_learning"]["MAZE"]["updates"] == 16


def test_dream_replays_game_experience_without_llm_and_forms_preconcept(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)
    lab.run_session(environment="PONG", steps=5, now=NOW)
    lab.run_session(environment="FORAGING", steps=5, now=NOW)

    before = len(substrate.stimuli)
    dreams = FerroDreamEngine(tmp_path, substrate=substrate)
    result = dreams.dream(now=NOW, replay_count=4)

    assert result["status"] == "DREAMED"
    assert len(substrate.stimuli) == before + 4
    assert result["dream"]["llm_used_for_replay"] is False
    assert result["dream"]["replay_count"] == 4
    assert result["preconcept"] is not None
    assert len(result["preconcept"]["channels"]) >= 2
    assert dreams.projection()["dream_count"] == 1


def test_dream_preconcept_can_be_marked_as_ego_interpreted(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)
    lab.run_session(environment="MAZE", steps=4, now=NOW)
    dreams = FerroDreamEngine(tmp_path, substrate=substrate)
    result = dreams.dream(now=NOW, replay_count=3)
    pre = result["preconcept"]
    assert pre is not None

    interpreted = dreams.mark_verbalized(
        pre["preconcept_id"],
        hypothesis="Repeated navigation and urgency coactivation may be worth testing.",
        now=NOW,
    )
    assert interpreted["status"] == "EGO_INTERPRETED"
    assert interpreted["verbalized"] is True


def test_dream_replays_real_world_motor_experience_alongside_games(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)
    lab.run_session(environment="MAZE", steps=6, now=NOW)

    motor_path = tmp_path / "application" / "neural_motor.json"
    motor_path.parent.mkdir(parents=True, exist_ok=True)
    motor_path.write_text(
        __import__("json").dumps({
            "decisions": [
                {
                    "decision_id": "motor-decision:world-1",
                    "source_event_id": "motor-probe:world-1",
                    "created_at": NOW.isoformat(),
                    "motor_id": "motor_003",
                    "activation": 0.9,
                    "stimulus_channels": ["social_attention", "curiosity", "opportunity"],
                    "task": "CHECK_X",
                    "status": "SETTLED",
                    "outcome": {
                        "observed_at": NOW.isoformat(),
                        "status": "OK",
                        "reward": 0.75,
                    },
                    "burst": {"spikes": 321, "active_neurons": 123},
                },
                {
                    "decision_id": "motor-decision:world-2",
                    "source_event_id": "motor-probe:world-2",
                    "created_at": NOW.isoformat(),
                    "motor_id": "motor_019",
                    "activation": 0.8,
                    "stimulus_channels": ["resource_pressure", "error_recovery"],
                    "task": "RESOURCE_REVIEW",
                    "status": "SETTLED",
                    "outcome": {
                        "observed_at": NOW.isoformat(),
                        "status": "DEGRADED",
                        "reward": -0.85,
                    },
                    "burst": {"spikes": 456, "active_neurons": 140},
                },
            ]
        }),
        encoding="utf-8",
    )

    dreams = FerroDreamEngine(tmp_path, substrate=substrate)
    result = dreams.dream(now=NOW, replay_count=6)

    assert result["status"] == "DREAMED"
    counts = result["dream"]["source_kind_counts"]
    assert counts["BRAIN_LAB"] > 0
    assert counts["WORLD_MOTOR"] == 2
    assert any(
        "WORLD_MOTOR" in row["source_kinds"]
        for row in result["dream"]["replays"]
    )
    assert any(
        "CHECK_X" in row["source_actions"] or "RESOURCE_REVIEW" in row["source_actions"]
        for row in result["dream"]["replays"]
    )


def test_ferro_born_world_runs_on_same_substrate_with_unsemantic_actions(tmp_path: Path):
    import json
    from wechat_traffic_agent.developmental_agency import DevelopmentalAgency

    transfer_path = tmp_path / "application" / "neural_transfer.json"
    transfer_path.parent.mkdir(parents=True, exist_ok=True)
    transfer_path.write_text(json.dumps({
        "experiments": [
            {
                "experiment_id": f"transfer:{index:06d}",
                "seq": index,
                "assignment": arm,
                "source_environment": "ROVER",
                "target_task": "CHECK_X",
                "status": "SETTLED",
            }
            for index, arm in enumerate(("TREATMENT", "TREATMENT", "TREATMENT", "CONTROL"), 1)
        ]
    }), encoding="utf-8")

    agency = DevelopmentalAgency(tmp_path)
    agency.refresh(
        now=NOW,
        neural_life={
            "drives": {"evolution": 0.6},
            "affect": {"frustration": 0.55, "confidence": 0.6},
            "history": [],
        },
        neural_transfer={
            "causal_design": "STRATIFIED_BY_SOURCE_TARGET_PAIR_V2",
            "strata": {
                "ROVER->CHECK_X": {
                    "source_environment": "ROVER",
                    "target_task": "CHECK_X",
                    "complete": True,
                    "estimated_target_utility_effect": -0.533333,
                    "arms": {
                        "TREATMENT": {"count": 3},
                        "CONTROL": {"count": 1},
                    },
                }
            },
        },
        neural_motor={"recent_decisions": []},
    )

    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)
    result = lab.run_session(environment="WORLD_0001", steps=8, now=NOW)

    assert result["status"] == "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM"
    assert result["environment"] == "WORLD_0001"
    assert len(substrate.stimuli) == 8
    assert len(substrate.feedback) == 8
    projection = lab.projection()
    world = projection["generated_worlds"]["WORLD_0001"]
    assert world["target_task"] == "CHECK_X"
    assert world["avoids_environment"] == "ROVER"
    assert world["steps"] == 8
    assert set(projection["environments"]["WORLD_0001"]["last_action"]).issubset(set("A0123"))
    assert projection["available_environments"][-1] == "WORLD_0001"


def test_accelerated_arena_trains_multiple_worlds_and_records_transfer(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = FerroBrainLab(tmp_path, substrate=substrate)

    result = lab.run_accelerated_arena(
        environments=("MAZE", "FORAGING"),
        sessions_per_environment=2,
        base_steps=8,
        now=NOW,
    )

    assert result["mode"] == "HEADLESS_ACCELERATED_ARENA"
    assert result["session_count"] == 4
    assert result["total_steps"] >= 32
    assert len(result["transfer_evaluations"]) == 1
    game = lab.projection()["game_intelligence"]
    assert game["accelerated_batch_count"] == 1
    assert game["episode_count"] == 4
    assert game["transition_count"] >= 32
    assert game["macro_skill_count"] >= 1
    # Macro execution is probabilistic/bounded and neural-frame cadence is
    # intentionally slower than motor-frame cadence. Discovery must happen in
    # this short batch; prospective execution/validation is covered separately.


def test_accelerated_arena_prioritizes_environment_with_largest_graduation_gap(
    tmp_path: Path,
):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    lab.game_intelligence.state["episodes"] = [
        {"environment": env, "episode_id": f"{env}:{index}", "reward": 0.0}
        for env, count in {
            "PONG": 29,
            "MAZE": 25,
            "FORAGING": 22,
            "ROVER": 12,
        }.items()
        for index in range(count)
    ]
    lab.game_intelligence.state["transitions"] = [
        {
            "environment": env,
            "episode_id": f"{env}:0",
            "state_key": str(index),
            "action": "RIGHT",
            "reward": 0.0,
            "next_state_key": str(index + 1),
        }
        for env, count in {
            "PONG": 892,
            "MAZE": 803,
            "FORAGING": 639,
            "ROVER": 360,
        }.items()
        for index in range(count)
    ]
    lab.game_intelligence._persist(NOW)

    result = lab.run_accelerated_arena(
        sessions_per_environment=1,
        base_steps=8,
        now=NOW,
    )

    assert result["results"][0]["environment"] == "ROVER"


def test_open_world_curriculum_selects_active_difficult_and_diverse_worlds(tmp_path: Path):
    app = tmp_path / "application"
    app.mkdir(parents=True)
    worlds = {
        "WORLD_0001": {
            "status": "CANDIDATE",
            "target_task": "CHECK_X",
            "sensor_channels": ["a", "b", "c", "d"],
            "actions": ["A0", "A1", "A2", "A3"],
            "noise": 0.12,
            "context_switch_period": 4,
            "episode_horizon": 64,
        },
        "WORLD_0002": {
            "status": "EXPERIMENTING",
            "target_task": "REFLECT",
            "sensor_channels": ["a", "b", "c", "d"],
            "actions": ["A0", "A1", "A2", "A3"],
            "noise": 0.10,
            "context_switch_period": 5,
            "episode_horizon": 60,
        },
        "WORLD_0003": {
            "status": "ADOPTED",
            "target_task": "OBSERVE_WORLD",
            "sensor_channels": ["a", "b", "c", "d"],
            "actions": ["A0", "A1", "A2"],
            "noise": 0.08,
            "context_switch_period": 7,
            "episode_horizon": 58,
        },
        "WORLD_0004": {
            "status": "REVISE_REQUIRED",
            "target_task": "CONTENT_COGNITION",
            "sensor_channels": ["a", "b", "c", "d"],
            "actions": ["A0", "A1", "A2", "A3"],
            "noise": 0.20,
            "context_switch_period": 2,
            "episode_horizon": 80,
        },
    }
    (app / "developmental_agency.json").write_text(
        json.dumps({"worlds": worlds}),
        encoding="utf-8",
    )
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    selected = lab.open_world_curriculum_environments(limit=3)
    assert set(selected) == {"WORLD_0001", "WORLD_0002", "WORLD_0003"}
    assert "WORLD_0004" not in selected


def test_foundation_competence_distinguishes_playing_skill_from_sample_volume(tmp_path: Path):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    envs = lab.state["environments"]

    envs["PONG"].update({"hits": 30, "misses": 70, "best_streak": 4})
    envs["MAZE"].update({"reaches": 40, "collisions": 900, "step": 3000})
    envs["FORAGING"].update({"foods": 40, "step": 8000, "reward_sum": -20.0})
    envs["ROVER"].update(
        {"gates": 40, "step": 1800, "off_course": 20, "reward_sum": 90.0}
    )

    status = lab.foundation_competence()
    assert status["graduated"] is False
    assert status["progress"] == 0.25
    assert status["environments"]["PONG"]["passed"] is False
    assert status["environments"]["MAZE"]["passed"] is False
    assert status["environments"]["FORAGING"]["passed"] is False
    assert status["environments"]["ROVER"]["passed"] is True
    assert status["remediation_environments"] == ["PONG", "MAZE", "FORAGING"]


def test_recent_competence_can_overcome_old_bad_history(tmp_path: Path):
    lab = FerroBrainLab(tmp_path, substrate=FakeSubstrate())
    for name in lab.ENVIRONMENTS:
        lab.state["competence_sessions"][name] = [
            {
                "observed_at": NOW.isoformat(),
                "environment": name,
                "passed": True,
                "score": 1.0,
                "metrics": {},
            }
            for _ in range(12)
        ]

    status = lab.foundation_competence()
    assert status["graduated"] is True
    assert status["progress"] == 1.0
    assert all(row["recent_passed"] for row in status["environments"].values())
