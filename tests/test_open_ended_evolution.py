from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from wechat_traffic_agent.developmental_agency import DevelopmentalAgency
from wechat_traffic_agent.open_ended_evolution import (
    MAX_CONSOLIDATION_SESSIONS,
    OpenEndedEvolution,
)


NOW = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


class FakeAgency:
    def __init__(self, world):
        self.state = {"worlds": {world["world_id"]: dict(world)}}


class FakeBrainLab:
    def __init__(self, state_root: Path, *, structural_after: int | None = None):
        self.state_root = Path(state_root)
        self.structural_after = structural_after
        self.calls = []

    def run_session(self, *, environment, steps, now):
        self.calls.append((environment, steps, now))
        if self.structural_after is not None and len(self.calls) >= self.structural_after:
            write_stable_circuit(self.state_root, environment)
        return {
            "status": "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM",
            "environment": str(environment).upper(),
            "steps": int(steps),
            "reward": 3.5,
            "changed_synapses": 21,
        }


def world(*, decision="ADOPT", effect=0.22, world_id="WORLD_0001"):
    status = {
        "ADOPT": "ADOPTED",
        "REJECT": "REJECTED",
        "REVISE": "REVISE_REQUIRED",
    }[decision]
    return {
        "world_id": world_id,
        "status": status,
        "need_id": "development-need:test",
        "proposal_id": "development-proposal:test",
        "target_task": "CHECK_X",
        "seed": "abcdef0123456789",
        "sensor_channels": [
            "social_attention",
            "curiosity",
            "opportunity",
            "prediction_due",
        ],
        "actions": ["A0", "A1", "A2"],
        "trial_count": 5,
        "training_steps": 60,
        "reward_sum": 12.0,
        "changed_synapses": 300,
        "transfer_evaluation": {
            "world_id": world_id,
            "settled_count": 8,
            "arms": {
                "TREATMENT": {
                    "count": 4,
                    "mean_target_utility": 0.72,
                },
                "CONTROL": {
                    "count": 4,
                    "mean_target_utility": 0.50,
                },
            },
            "estimated_real_world_effect": effect,
            "causal_claim_ready": True,
            "decision": decision,
            "minimum_per_arm": 4,
        },
    }


def write_development(root: Path, payload):
    path = root / "application" / "synthetic_neural_development.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def linked_neuron(neuron_id: str, world_id: str, ordinal: int):
    return {
        "neuron_id": neuron_id,
        "ordinal": ordinal,
        "generation": 1,
        "state": "MATURE",
        "born_at": NOW.isoformat(),
        "parent_channels": ["curiosity", "opportunity"],
        "developmental_trigger": {
            "signature": "curiosity|opportunity",
            "score": 8.5,
            "count": 12,
            "recent_source_events": [
                f"brain-lab:{world_id.lower()}:0:{ordinal}",
            ],
        },
    }


def write_stable_circuit(root: Path, world_id: str = "WORLD_0001"):
    n1 = "ferro-neuron:166701"
    n2 = "ferro-neuron:166702"
    write_development(root, {
        "version": 1,
        "neurons": {
            n1: linked_neuron(n1, world_id, 166701),
            n2: linked_neuron(n2, world_id, 166702),
        },
        "circuits": {
            "circuit_0001": {
                "circuit_id": "circuit_0001",
                "state": "STABLE",
                "members": [n1, n2, "ferro-neuron:166703", "ferro-neuron:166704"],
                "stability_events": 18,
                "active_events": 9,
                "human_function_label": None,
            }
        },
        "organs": {},
    })


def test_adopted_world_without_structural_emergence_is_not_called_evolved(tmp_path: Path):
    raw_world = world()
    agency = FakeAgency(raw_world)
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=agency,
    )

    projection = evolution.refresh(now=NOW)

    assert projection["cycle_count"] == 1
    assert projection["evolved_cycle_count"] == 0
    cycle = projection["recent_cycles"][0]
    assert cycle["status"] == "ADOPTED_AWAITING_EMERGENCE"
    assert cycle["reality_decision"] == "ADOPT"
    assert cycle["causal_claim_ready"] is True
    assert cycle["structural_evidence"]["world_linked_neuron_count"] == 0
    assert cycle["completion_kind"] is None
    assert projection["next_consolidation"]["world_id"] == "WORLD_0001"


def test_reality_reject_and_revise_never_count_as_evolved(tmp_path: Path):
    for decision, expected in (
        ("REJECT", "REJECTED_BY_REALITY"),
        ("REVISE", "REVISION_REQUIRED"),
    ):
        root = tmp_path / decision.lower()
        raw_world = world(decision=decision)
        evolution = OpenEndedEvolution(
            root,
            brain_lab=FakeBrainLab(root),
            agency=FakeAgency(raw_world),
        )
        projection = evolution.refresh(now=NOW)
        cycle = projection["recent_cycles"][0]
        assert cycle["status"] == expected
        assert cycle["completion_kind"] is None
        assert projection["evolved_cycle_count"] == 0
        assert projection["next_consolidation"] is None


def test_world_linked_single_neurons_are_visible_but_not_mislabeled_as_capability(tmp_path: Path):
    n1 = "ferro-neuron:166701"
    write_development(tmp_path, {
        "version": 1,
        "neurons": {
            n1: linked_neuron(n1, "WORLD_0001", 166701),
        },
        "circuits": {},
        "organs": {},
    })
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(world()),
    )

    cycle = evolution.refresh(now=NOW)["recent_cycles"][0]

    assert cycle["status"] == "ADOPTED_AWAITING_EMERGENCE"
    evidence = cycle["structural_evidence"]
    assert evidence["world_linked_neuron_count"] == 1
    assert evidence["completion_kind"] is None
    assert evidence["semantic_capability_claim"] is False


def test_adopted_real_transfer_plus_exact_world_stable_circuit_closes_n36(tmp_path: Path):
    write_stable_circuit(tmp_path, "WORLD_0001")
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(world()),
    )

    projection = evolution.refresh(now=NOW)
    cycle = projection["recent_cycles"][0]

    assert cycle["status"] == "EVOLVED"
    assert cycle["completion_kind"] == "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT"
    assert cycle["emergence_claim"] == (
        "ADOPTED_REAL_WORLD_TRANSFER_WITH_EXACT_WORLD_NEURAL_PROVENANCE"
    )
    assert projection["evolved_cycle_count"] == 1
    chain = cycle["provenance_chain"]
    assert chain["need_id"] == "development-need:test"
    assert chain["proposal_id"] == "development-proposal:test"
    assert chain["world_id"] == "WORLD_0001"
    assert chain["causal_claim_ready"] is True
    assert chain["real_world_effect"] == 0.22
    assert chain["stable_world_linked_circuit_count"] == 1
    assert cycle["structural_evidence"]["semantic_capability_claim"] is False


def test_neurons_from_another_world_cannot_complete_cycle(tmp_path: Path):
    write_stable_circuit(tmp_path, "WORLD_9999")
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(world(world_id="WORLD_0001")),
    )

    cycle = evolution.refresh(now=NOW)["recent_cycles"][0]

    assert cycle["status"] == "ADOPTED_AWAITING_EMERGENCE"
    assert cycle["structural_evidence"]["world_linked_neuron_count"] == 0
    assert cycle["structural_evidence"]["stable_world_linked_circuit_count"] == 0


def test_world_linked_organ_is_stronger_completion_evidence(tmp_path: Path):
    write_stable_circuit(tmp_path, "WORLD_0001")
    path = tmp_path / "application" / "synthetic_neural_development.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["organs"] = {
        "organ_001": {
            "organ_id": "organ_001",
            "origin_circuit_id": "circuit_0001",
            "state": "EMERGENT_ORGAN",
            "member_count": 4,
            "function_label": None,
            "function_assignment": "OBSERVATIONAL_ONLY",
        }
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(world()),
    )

    cycle = evolution.refresh(now=NOW)["recent_cycles"][0]

    assert cycle["status"] == "EVOLVED"
    assert cycle["completion_kind"] == "WORLD_PROVENANCE_EMERGENT_ORGAN"
    assert cycle["structural_evidence"]["world_linked_organ_count"] == 1


def test_bounded_consolidation_can_turn_adoption_into_real_emergence(tmp_path: Path):
    raw_world = world()
    brain = FakeBrainLab(tmp_path, structural_after=1)
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=brain,
        agency=FakeAgency(raw_world),
    )
    evolution.refresh(now=NOW)

    result = evolution.run_consolidation(now=NOW, steps=24)

    assert brain.calls[0][0] == "WORLD_0001"
    assert brain.calls[0][1] == 24
    assert result["status"] == "EVOLVED"
    assert result["cycle"]["completion_kind"] == (
        "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT"
    )
    assert result["cycle"]["consolidation_session_count"] == 1


def test_consolidation_budget_exhaustion_is_explicit_not_fake_success(tmp_path: Path):
    brain = FakeBrainLab(tmp_path, structural_after=None)
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=brain,
        agency=FakeAgency(world()),
    )
    evolution.refresh(now=NOW)

    result = None
    for _ in range(MAX_CONSOLIDATION_SESSIONS):
        result = evolution.run_consolidation(now=NOW, steps=8)

    assert result is not None
    projection = evolution.projection()
    cycle = projection["recent_cycles"][0]
    assert len(brain.calls) == MAX_CONSOLIDATION_SESSIONS
    assert cycle["consolidation_session_count"] == MAX_CONSOLIDATION_SESSIONS
    assert cycle["status"] == "CONSOLIDATION_BUDGET_EXHAUSTED"
    assert cycle["completion_kind"] is None
    assert projection["evolved_cycle_count"] == 0
    assert projection["next_consolidation"] is None


def test_cycle_state_survives_restart(tmp_path: Path):
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(world()),
    )
    evolution.refresh(now=NOW)
    evolution.run_consolidation(now=NOW, steps=5)

    restored = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(world()),
    )
    projection = restored.projection()

    assert projection["cycle_count"] == 1
    assert projection["recent_cycles"][0]["consolidation_session_count"] == 1
    assert projection["completion_requires_real_world_adoption"] is True
    assert projection["single_neuron_is_not_called_a_new_capability"] is True



def test_non_ferro_world_cannot_satisfy_n36_even_with_structure(tmp_path: Path):
    raw_world = world(world_id="LEGACY_WORLD")
    write_stable_circuit(tmp_path, "LEGACY_WORLD")
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=FakeAgency(raw_world),
    )

    cycle = evolution.refresh(now=NOW)["recent_cycles"][0]

    assert cycle["ferro_born_world_verified"] is False
    assert cycle["status"] == "INELIGIBLE_NON_FERRO_WORLD"
    assert cycle["completion_kind"] is None
    assert evolution.projection()["evolved_cycle_count"] == 0
    assert evolution.projection()["completion_requires_ferro_born_world"] is True



def test_real_developmental_agency_adoption_flows_into_n36_structural_closure(tmp_path: Path):
    agency = DevelopmentalAgency(tmp_path)
    candidate = world()
    evaluation = dict(candidate.pop("transfer_evaluation"))
    candidate["status"] = "EXPERIMENTING"
    agency.state["worlds"] = {"WORLD_0001": candidate}
    agency.state["proposals"] = {
        "development-proposal:test": {
            "proposal_id": "development-proposal:test",
            "need_id": "development-need:test",
            "status": "EXPERIMENTING",
        }
    }
    agency.state["needs"] = {
        "development-need:test": {
            "need_id": "development-need:test",
            "status": "ACTIVE",
        }
    }

    adopted = agency.apply_world_transfer_evaluation(
        world_id="WORLD_0001",
        evaluation=evaluation,
        observed_at=NOW,
    )
    assert adopted["status"] == "ADOPTED"
    assert agency.state["proposals"]["development-proposal:test"]["status"] == "ADOPTED"

    write_stable_circuit(tmp_path, "WORLD_0001")
    evolution = OpenEndedEvolution(
        tmp_path,
        brain_lab=FakeBrainLab(tmp_path),
        agency=agency,
    )

    cycle = evolution.refresh(now=NOW)["recent_cycles"][0]

    assert cycle["status"] == "EVOLVED"
    assert cycle["provenance_chain"]["need_id"] == "development-need:test"
    assert cycle["provenance_chain"]["proposal_id"] == "development-proposal:test"
    assert cycle["provenance_chain"]["world_id"] == "WORLD_0001"
    assert cycle["provenance_chain"]["reality_decision"] == "ADOPT"
    assert cycle["provenance_chain"]["stable_world_linked_circuit_count"] == 1
