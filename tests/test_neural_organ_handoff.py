from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.neural_organ_handoff import (
    AUDIT_EVERY,
    MIN_PER_ARM,
    NeuralOrganHandoff,
)


NOW = datetime(2026, 9, 20, 7, 0, tzinfo=timezone.utc)


def brain_lab(total_sessions: int = 0):
    return {
        "total_sessions": total_sessions,
        "ancestral_environments": ["PONG", "MAZE", "FORAGING", "ROVER"],
    }


def substrate(*, active_members=("ferro-neuron:166701", "ferro-neuron:166702")):
    return {
        "developmental_neurogenesis": {
            "last_organ_activity": {
                "source_event_id": "neural-event:organ",
                "event_seq": 42,
                "organs": [
                    {
                        "organ_id": "organ_001",
                        "activation": 0.75,
                        "active_member_count": len(active_members),
                        "member_count": 4,
                        "active_members": list(active_members),
                        "dominant_parent_channels": ["curiosity", "opportunity"],
                        "origin_circuit_id": "circuit_001",
                        "function_label": None,
                        "function_assignment": "UNSEMANTIC_ACTIVITY_ONLY",
                    }
                ],
            }
        }
    }


def settle_decision(
    controller: NeuralOrganHandoff,
    decision: dict,
    *,
    utility: float,
):
    trial = decision["trial"]
    assert trial is not None
    steps = 10
    return controller.settle(
        decision_id=trial["decision_id"],
        result={
            "status": "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM",
            "steps": steps,
            "reward": utility * steps,
        },
        observed_at=NOW,
    )


def test_without_emergent_organ_python_baseline_remains_authoritative(tmp_path: Path):
    controller = NeuralOrganHandoff(tmp_path)
    decision = controller.choose(
        now=NOW,
        brain_lab=brain_lab(),
        substrate_projection={"developmental_neurogenesis": {}},
    )

    assert decision["status"] == "NO_ELIGIBLE_EMERGENT_ORGAN"
    assert decision["authority"] == "PYTHON_BASELINE"
    assert decision["environment"] == "PONG"
    assert decision["trial"] is None
    assert decision["external_action_authority"] is False


def test_neural_choice_is_driven_by_real_member_firing_pattern(tmp_path: Path):
    first = NeuralOrganHandoff(tmp_path / "a")
    second = NeuralOrganHandoff(tmp_path / "b")

    a = first.choose(
        now=NOW,
        brain_lab=brain_lab(),
        substrate_projection=substrate(
            active_members=("ferro-neuron:166701", "ferro-neuron:166702"),
        ),
    )
    b = second.choose(
        now=NOW,
        brain_lab=brain_lab(),
        substrate_projection=substrate(
            active_members=("ferro-neuron:166703", "ferro-neuron:166704"),
        ),
    )

    # The A/B arm itself is prospective and may be Python on either first trial.
    # When an arm is neural, its decoder stores the actual firing pattern and
    # an unsemantic score over existing environments rather than a hand-written
    # organ function label.
    neural = next(
        row for row in (a, b)
        if row["authority"] == "NEURAL_ORGAN"
    )
    trial = neural["trial"]
    assert trial["active_member_count"] == 2
    assert trial["active_members"]
    assert trial["neural_environment_scores"]
    assert set(trial["neural_environment_scores"]) == {
        "PONG", "MAZE", "FORAGING", "ROVER",
    }
    assert neural["organ_id"] == "organ_001"


def test_balanced_prospective_evidence_can_handoff_low_risk_function(tmp_path: Path):
    controller = NeuralOrganHandoff(tmp_path)
    arms = []
    last = None
    for index in range(MIN_PER_ARM * 2):
        decision = controller.choose(
            now=NOW,
            brain_lab=brain_lab(index),
            substrate_projection=substrate(),
        )
        arms.append(decision["authority"])
        utility = 0.55 if decision["authority"] == "NEURAL_ORGAN" else 0.50
        last = settle_decision(controller, decision, utility=utility)

    assert arms.count("NEURAL_ORGAN") == MIN_PER_ARM
    assert arms.count("PYTHON_BASELINE") == MIN_PER_ARM
    assert last is not None
    assert last["evaluation"]["status"] == "HANDOFF"

    projection = controller.projection()
    assert projection["phase"] == "NEURAL_AUTHORITY"
    assert projection["selected_organ_id"] == "organ_001"
    assert projection["python_baseline_preserved"] is True
    assert projection["automatic_rollback"] is True
    assert projection["llm_used_for_handoff"] is False


def test_inferior_neural_policy_never_receives_handoff(tmp_path: Path):
    controller = NeuralOrganHandoff(tmp_path)
    last = None
    for index in range(MIN_PER_ARM * 2):
        decision = controller.choose(
            now=NOW,
            brain_lab=brain_lab(index),
            substrate_projection=substrate(),
        )
        utility = 0.05 if decision["authority"] == "NEURAL_ORGAN" else 0.55
        last = settle_decision(controller, decision, utility=utility)

    assert last is not None
    assert last["evaluation"]["status"] == "ROLLBACK"
    projection = controller.projection()
    assert projection["phase"] == "PYTHON_ROLLBACK"
    assert projection["selected_organ_id"] is None
    assert projection["rollback_reason"] == (
        "NEURAL_ORGAN_INFERIOR_DURING_PROSPECTIVE_TRIAL"
    )

    decision = controller.choose(
        now=NOW,
        brain_lab=brain_lab(99),
        substrate_projection=substrate(),
    )
    assert decision["authority"] == "PYTHON_BASELINE"
    assert decision["status"] == "PYTHON_ROLLBACK_ACTIVE"


def test_longitudinal_python_audits_can_automatically_rollback_regression(tmp_path: Path):
    controller = NeuralOrganHandoff(tmp_path)

    # First earn handoff.
    for index in range(MIN_PER_ARM * 2):
        decision = controller.choose(
            now=NOW,
            brain_lab=brain_lab(index),
            substrate_projection=substrate(),
        )
        settle_decision(
            controller,
            decision,
            utility=0.60 if decision["authority"] == "NEURAL_ORGAN" else 0.55,
        )
    assert controller.projection()["phase"] == "NEURAL_AUTHORITY"

    # Then the organ regresses while periodic Python audits stay strong.
    audit_seen = 0
    neural_seen = 0
    for index in range(AUDIT_EVERY * MIN_PER_ARM):
        decision = controller.choose(
            now=NOW,
            brain_lab=brain_lab(100 + index),
            substrate_projection=substrate(),
        )
        if decision["authority"] == "PYTHON_AUDIT":
            audit_seen += 1
            utility = 0.60
        else:
            neural_seen += 1
            utility = -0.20
        settle_decision(controller, decision, utility=utility)
        if controller.projection()["phase"] == "PYTHON_ROLLBACK":
            break

    assert audit_seen >= MIN_PER_ARM
    assert neural_seen >= MIN_PER_ARM
    projection = controller.projection()
    assert projection["phase"] == "PYTHON_ROLLBACK"
    assert projection["rollback_reason"] == "LONGITUDINAL_NEURAL_ORGAN_REGRESSION"


def test_handoff_state_round_trip_preserves_authority_and_associations(tmp_path: Path):
    controller = NeuralOrganHandoff(tmp_path)
    for index in range(MIN_PER_ARM * 2):
        decision = controller.choose(
            now=NOW,
            brain_lab=brain_lab(index),
            substrate_projection=substrate(),
        )
        settle_decision(
            controller,
            decision,
            utility=0.60 if decision["authority"] == "NEURAL_ORGAN" else 0.55,
        )

    restored = NeuralOrganHandoff(tmp_path)
    projection = restored.projection()
    assert projection["phase"] == "NEURAL_AUTHORITY"
    assert projection["selected_organ_id"] == "organ_001"
    assert projection["trial_count"] == MIN_PER_ARM * 2
    assert "organ_001" in projection["associations"]
