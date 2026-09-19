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
    assert set(feedback.pathway)
    assert -1.0 <= utility <= 1.0


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
