from __future__ import annotations

from datetime import datetime, timezone
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
    assert len(substrate.stimuli) == 12
    assert len(substrate.feedback) == 12
    assert lab.projection()["total_steps"] == 12
    assert set(lab.projection()["environments"]) == {"PONG", "MAZE", "FORAGING", "ROVER"}

    restored = FerroBrainLab(tmp_path, substrate=substrate)
    assert restored.projection()["total_steps"] == 12
    assert restored.projection()["same_production_connectome"] is True


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
