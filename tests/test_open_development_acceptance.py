from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from wechat_traffic_agent.longitudinal_individuality import LongitudinalIndividuality
from wechat_traffic_agent.open_development_acceptance import OpenDevelopmentAcceptance


NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def write(root: Path, name: str, payload):
    path = root / "application" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_empty_state_never_confuses_engineering_frontier_with_production_acceptance(tmp_path: Path):
    audit = OpenDevelopmentAcceptance(tmp_path).projection()

    assert audit["engineering_frontier"] == "N36"
    assert audit["production_behavior_accepted_count"] == 0
    assert audit["production_behavior_missing_count"] == 11
    assert audit["accepted_stages"] == []
    assert audit["missing_stages"] == [
        "N26", "N27", "N28", "N29", "N30", "N31",
        "N32", "N33", "N34", "N35", "N36",
    ]
    assert audit["production_accepted_contiguously_through"] is None
    assert "engineering implementation is not" in audit["audit_policy"]


def seed_n26_to_n34_and_n36(root: Path):
    write(root, "developmental_agency.json", {
        "schema_version": "ferro-developmental-agency.v1",
        "needs": {
            "development-need:test": {
                "need_id": "development-need:test",
                "status": "ACTIVE",
                "evidence_count": 2,
                "evidence": [{"evidence_id": "development-evidence:1"}],
                "llm_used_for_detection": False,
            }
        },
        "proposals": {
            "development-proposal:test": {
                "proposal_id": "development-proposal:test",
                "need_id": "development-need:test",
                "status": "ADOPTED",
                "experiment_contract": {
                    "prospective_assignment_required": True,
                    "minimum_treatment_outcomes": 4,
                    "minimum_control_outcomes": 4,
                },
                "llm_used_for_proposal": False,
            }
        },
        "worlds": {
            "WORLD_0001": {
                "world_id": "WORLD_0001",
                "status": "ADOPTED",
                "seed": "seed-1",
                "sensor_channels": ["curiosity", "opportunity"],
                "actions": ["A0", "A1", "A2"],
                "safety": {
                    "brain_lab_only": True,
                    "no_external_actions": True,
                    "no_llm_motor_loop": True,
                },
                "transfer_evaluation": {
                    "decision": "ADOPT",
                    "causal_claim_ready": True,
                    "estimated_real_world_effect": 0.21,
                    "arms": {
                        "TREATMENT": {"count": 4, "mean_target_utility": 0.71},
                        "CONTROL": {"count": 4, "mean_target_utility": 0.50},
                    },
                },
            }
        },
    })
    write(root, "neural_dreams.json", {
        "schema_version": "ferro-neural-dreams.v1",
        "dream_count": 12,
        "attractors": {
            "attractor:test": {
                "attractor_id": "attractor:test",
                "status": "STABLE",
                "recurrence_count": 4,
                "consolidation_epoch_count": 3,
                "goal_candidate": {
                    "candidate_id": "dream-goal-candidate:test",
                    "status": "UNINTERPRETED",
                    "llm_used_for_birth": False,
                },
            }
        },
    })
    write(root, "neural_extensions.json", {
        "schema_version": "ferro-neural-extensions.v1",
        "extensions": {
            "sensor_ext_0001": {
                "extension_id": "sensor_ext_0001",
                "extension_type": "SENSOR",
                "status": "ACTIVE_EXPERIMENT",
                "sample_count": 3,
            },
            "motor_ext_0002": {
                "extension_id": "motor_ext_0002",
                "extension_type": "MOTOR_CAPACITY",
                "status": "ACTIVE_EXPERIMENT",
                "born_motor_ids": ["motor_032", "motor_033"],
            },
        },
    })
    architecture_trials = []
    for index in range(8):
        architecture_trials.append({
            "trial_id": f"architecture:{index}",
            "status": "SETTLED",
            "assignment": (
                "ADAPTIVE_FAST" if index % 2 == 0 else "CONSERVATIVE_STABLE"
            ),
        })
    write(root, "synthetic_neural_development.json", {"version": 1})
    for index, row in enumerate(architecture_trials):
        row["target_outcome"] = {
            "status": "OK",
            "utility": 0.62 if row["assignment"] == "ADAPTIVE_FAST" else 0.50,
        }
    write(root, "developmental_architecture.json", {
        "schema_version": "ferro-developmental-architecture.v1",
        "selected_variant": "ADAPTIVE_FAST",
        "selected_at": NOW.isoformat(),
        "trials": architecture_trials,
        "selection_history": [{
            "selected_variant": "ADAPTIVE_FAST",
            "selected_at": NOW.isoformat(),
            "selected_at_trial_seq": 8,
            "effect_adaptive_minus_conservative": 0.12,
            "causal_source": "REAL_TARGET_OUTCOMES",
        }],
    })
    handoff_trials = []
    for index in range(8):
        arm = "NEURAL_ORGAN" if index % 2 == 0 else "PYTHON_BASELINE"
        handoff_trials.append({
            "decision_id": f"organ-handoff:{index}",
            "phase": "EVALUATING",
            "status": "SETTLED",
            "arm": arm,
            "outcome": {
                "utility": 0.58 if arm == "NEURAL_ORGAN" else 0.55,
            },
        })
    write(root, "neural_organ_handoff.json", {
        "schema_version": "ferro-neural-organ-handoff.v1",
        "phase": "NEURAL_AUTHORITY",
        "selected_organ_id": "organ_001",
        "handoff_at": NOW.isoformat(),
        "trials": handoff_trials,
    })
    write(root, "open_ended_evolution.json", {
        "schema_version": "ferro-open-ended-evolution.v1",
        "cycles": {
            "evolution-cycle:test": {
                "cycle_id": "evolution-cycle:test",
                "status": "EVOLVED",
                "world_id": "WORLD_0001",
                "ferro_born_world_verified": True,
                "causal_claim_ready": True,
                "completion_kind": "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT",
            }
        },
    })


def seed_n35(root: Path):
    ledger = LongitudinalIndividuality(root)
    first_cortex = {
        "organism_id": "ferro-organism:test",
        "current_cortex": {
            "class": "OPENAI_COMPATIBLE",
            "provider_name": "provider-a",
            "model": "model-a",
            "base_url": "https://a.invalid/v1",
            "selected_resource_id": "resource:a",
        },
    }
    second_cortex = {
        "organism_id": "ferro-organism:test",
        "current_cortex": {
            "class": "OPENAI_COMPATIBLE",
            "provider_name": "provider-b",
            "model": "model-b",
            "base_url": "https://b.invalid/v1",
            "selected_resource_id": "resource:b",
        },
    }
    base_graph = {
        "substrate": {
            "source_fingerprint": "sha256:ancestral",
            "neuron_count": 166700,
            "developmental_neurogenesis": {
                "synthetic_neuron_count": 0,
                "birth_seq": 0,
                "event_seq": 0,
                "developmental_genome": {
                    "fingerprint": "sha256:g0",
                    "mutation_count": 0,
                },
                "circuits": [],
                "organs": [],
            },
        }
    }
    later_graph = {
        "substrate": {
            "source_fingerprint": "sha256:ancestral",
            "neuron_count": 166706,
            "developmental_neurogenesis": {
                "synthetic_neuron_count": 6,
                "birth_seq": 6,
                "event_seq": 300,
                "developmental_genome": {
                    "fingerprint": "sha256:g4",
                    "mutation_count": 4,
                },
                "circuits": [{
                    "circuit_id": "circuit_0001",
                    "state": "STABLE",
                }],
                "organs": [{
                    "organ_id": "organ_001",
                    "state": "EMERGENT_ORGAN",
                }],
            },
        }
    }
    ledger.observe(
        now=NOW,
        force=True,
        personality={"traits": {
            "novelty_seeking": 0.5,
            "persistence": 0.5,
            "sociality": 0.5,
            "caution": 0.5,
            "independence": 0.5,
            "playfulness": 0.5,
        }},
        social_state={"relationships": {}},
        motor_state={"units": {}},
        dream_state={"dream_count": 0, "attractors": {}},
        cortex=first_cortex,
        neural_graph=base_graph,
    )
    ledger.observe(
        now=NOW + timedelta(days=30),
        force=True,
        personality={"traits": {
            "novelty_seeking": 0.8,
            "persistence": 0.72,
            "sociality": 0.65,
            "caution": 0.4,
            "independence": 0.75,
            "playfulness": 0.7,
        }},
        social_state={"relationships": {
            "actor:pseudo": {
                "interactions": 20,
                "familiarity": 0.8,
                "valence": 0.4,
                "approach_bias": 0.7,
            }
        }},
        motor_state={"units": {
            "motor_003": {
                "motor_id": "motor_003",
                "state": "HABIT",
                "meaning": "OBSERVE_WORLD",
                "associations": {
                    "OBSERVE_WORLD": {
                        "trials": 8,
                        "mean_reward": 0.75,
                    }
                },
            }
        }},
        dream_state={"dream_count": 15, "attractors": {
            "attractor:x": {
                "attractor_id": "attractor:x",
                "signature": "curiosity|opportunity",
                "status": "STABLE",
                "recurrence_count": 5,
                "stability_score": 0.8,
                "channels": ["curiosity", "opportunity"],
            }
        }},
        cortex=second_cortex,
        neural_graph=later_graph,
    )


def test_all_stages_require_their_own_behavior_evidence(tmp_path: Path):
    seed_n26_to_n34_and_n36(tmp_path)
    seed_n35(tmp_path)

    audit = OpenDevelopmentAcceptance(tmp_path).projection()

    assert audit["production_behavior_accepted_count"] == 11
    assert audit["production_behavior_missing_count"] == 0
    assert audit["accepted_stages"] == [
        "N26", "N27", "N28", "N29", "N30", "N31",
        "N32", "N33", "N34", "N35", "N36",
    ]
    assert audit["production_accepted_contiguously_through"] == "N36"
    assert audit["stages"]["N29"]["evidence"]["decisions"][0]["decision"] == "ADOPT"
    assert audit["stages"]["N33"]["evidence"]["selected_variant"] == "ADAPTIVE_FAST"
    assert audit["stages"]["N34"]["evidence"]["initial_neural_settled"] == 4
    assert audit["stages"]["N35"]["evidence"]["preserved_cortex_transition_count"] == 1
    assert audit["stages"]["N36"]["evidence"]["latest_completion_kind"] == (
        "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT"
    )


def test_fake_n36_status_without_required_gates_is_not_accepted(tmp_path: Path):
    write(tmp_path, "open_ended_evolution.json", {
        "schema_version": "ferro-open-ended-evolution.v1",
        "cycles": {
            "fake": {
                "cycle_id": "fake",
                "status": "EVOLVED",
                "ferro_born_world_verified": False,
                "causal_claim_ready": False,
                "completion_kind": "SOMETHING_ELSE",
            }
        },
    })

    stage = OpenDevelopmentAcceptance(tmp_path).projection()["stages"]["N36"]

    assert stage["production_behavior_accepted"] is False
    assert stage["status"] == "WAITING_FOR_REAL_N36_EVOLUTION"
