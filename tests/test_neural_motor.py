from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.neural_motor import NeuralMotorCortex, MOTOR_UNIT_COUNT


NOW = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)


class FakeSubstrate:
    def __init__(self) -> None:
        self.stimuli = []
        self.feedback = []

    def stimulate(self, currents, observed_at, *, source_event_id=None, feedback_eligible=None):
        self.stimuli.append((dict(currents), observed_at, source_event_id, feedback_eligible))
        return {
            "response_spike_pattern_digest": "sha256:" + "ab" * 32,
            "spikes": 2222,
            "active_neurons": 888,
            "synaptic_events": 9999,
        }

    def apply_feedback(self, feedback, *, utility):
        self.feedback.append((feedback, utility))
        return {
            "status": "APPLIED",
            "changed_synapse_count": 7,
            "outcome_id": feedback.outcome_id,
        }


def life():
    return {
        "drives": {"survival": 0.2, "reproduction": 0.3, "evolution": 0.8},
        "affect": {
            "resource_hunger": 0.1,
            "social_need": 0.3,
            "curiosity": 0.9,
            "frustration": 0.1,
            "arousal": 0.6,
            "confidence": 0.7,
            "boredom": 0.2,
        },
        "intention": {"name": "EVOLVE", "strength": 0.8},
    }


def test_motor_units_start_without_semantics(tmp_path: Path):
    cortex = NeuralMotorCortex(tmp_path, substrate=FakeSubstrate(), allowed_actions=("OBSERVE_WORLD",))
    projection = cortex.projection()
    assert projection["motor_unit_count"] == MOTOR_UNIT_COUNT
    assert projection["semantics_preassigned"] is False
    assert projection["habit_count"] == 0


def test_motor_decision_uses_connectome_and_waits_for_world_outcome(tmp_path: Path):
    substrate = FakeSubstrate()
    cortex = NeuralMotorCortex(tmp_path, substrate=substrate, allowed_actions=("OBSERVE_WORLD",))
    decision = cortex.select(neural_life=life(), now=NOW)
    assert decision["authority"] == "NEURAL_MOTOR"
    assert decision["decision_kind"] == "EXPLORATION"
    assert decision["task"] == "OBSERVE_WORLD"
    assert substrate.stimuli[-1][3] is True

    repeated = cortex.select(neural_life=life(), now=NOW)
    assert repeated["decision_id"] == decision["decision_id"]
    assert repeated["status"] == "PENDING_OUTCOME_EXISTING"

    learned = cortex.observe_outcome(
        task="OBSERVE_WORLD",
        result={"status": "OK"},
        observed_at=NOW,
    )
    assert learned is not None
    assert learned["reward"] == 0.15
    assert learned["reward_source"] == "EXECUTION_SUCCESS_ONLY"
    assert learned["world_grounded"] is False
    assert learned["physical_feedback"]["changed_synapse_count"] == 7
    assert substrate.feedback[-1][0].source_event_id == decision["source_event_id"]


def test_repeated_success_teaches_a_motor_unit_a_habit(tmp_path: Path):
    substrate = FakeSubstrate()
    cortex = NeuralMotorCortex(tmp_path, substrate=substrate, allowed_actions=("OBSERVE_WORLD",))
    motor_id = None
    for _ in range(cortex.HABIT_MIN_TRIALS):
        decision = cortex.select(neural_life=life(), now=NOW)
        motor_id = motor_id or decision["motor_id"]
        assert decision["motor_id"] == motor_id
        learned = cortex.observe_outcome(
            task="OBSERVE_WORLD",
            result={"status": "OK", "world_utility": 0.8},
            observed_at=NOW,
        )
        assert learned is not None
        assert learned["reward_source"] == "WORLD_UTILITY"
        assert learned["world_grounded"] is True

    projection = cortex.projection()
    assert projection["habit_count"] == 1
    habit = projection["habits"][0]
    assert habit["motor_id"] == motor_id
    assert habit["meaning"] == "OBSERVE_WORLD"
    assert habit["association"]["trials"] == cortex.HABIT_MIN_TRIALS
    assert habit["association"]["mean_reward"] == 0.8

    next_decision = cortex.select(neural_life=life(), now=NOW)
    assert next_decision["decision_kind"] == "HABIT"
    assert next_decision["authority"] == "NEURAL_HABIT"
    assert next_decision["task"] == "OBSERVE_WORLD"


def test_repeated_failure_can_make_a_motor_association_dormant(tmp_path: Path):
    substrate = FakeSubstrate()
    cortex = NeuralMotorCortex(tmp_path, substrate=substrate, allowed_actions=("OBSERVE_WORLD",))
    for _ in range(cortex.DORMANT_MIN_TRIALS):
        cortex.select(neural_life=life(), now=NOW)
        learned = cortex.observe_outcome(
            task="OBSERVE_WORLD",
            result={"status": "ERROR"},
            observed_at=NOW,
        )
        assert learned is not None

    projection = cortex.projection()
    assert projection["dormant_unit_count"] == 1
    assert projection["habit_count"] == 0


def test_motor_cortex_can_grow_new_unsemantic_units_without_granting_actions(tmp_path: Path):
    substrate = FakeSubstrate()
    cortex = NeuralMotorCortex(
        tmp_path,
        substrate=substrate,
        allowed_actions=("OBSERVE_WORLD",),
    )

    born = cortex.extend_units(
        count=4,
        origin_ref="motor_ext_0001",
        now=NOW,
    )
    projection = cortex.projection()

    assert born == ("motor_032", "motor_033", "motor_034", "motor_035")
    assert projection["motor_unit_count"] == MOTOR_UNIT_COUNT + 4
    assert projection["ferro_born_motor_unit_count"] == 4
    assert projection["allowed_actions"] == ["OBSERVE_WORLD"]

    decision = cortex.select(neural_life=life(), now=NOW)
    assert decision["motor_id"].startswith("motor_")
    assert decision["task"] == "OBSERVE_WORLD"
