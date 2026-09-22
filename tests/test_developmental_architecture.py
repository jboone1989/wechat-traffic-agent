from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.developmental_architecture import (
    DevelopmentalArchitectureSearch,
    MIN_PER_VARIANT,
    VARIANTS,
)


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


class GenomeStub:
    def __init__(self):
        self.state = {
            "schema_version": "developmental-genome.v1",
            "ancestral_anchor": "DROSOPHILA_SEEDED_BASELINE",
            "genes": {
                "birth_threshold_scale": 1.0,
                "maturation_time_scale": 1.0,
                "synaptogenesis_scale": 1.0,
                "recurrent_affinity_scale": 1.0,
                "dormancy_resilience_scale": 1.0,
                "organ_threshold_scale": 1.0,
            },
            "mutation_count": 7,
            "history": [{"mutation_seq": 7}],
        }

    @property
    def genes(self):
        return dict(self.state["genes"])

    def snapshot(self):
        return {
            "schema_version": self.state["schema_version"],
            "ancestral_anchor": self.state["ancestral_anchor"],
            "genes": dict(self.state["genes"]),
            "mutation_count": self.state["mutation_count"],
            "history": [dict(row) for row in self.state["history"]],
        }


class SubstrateStub:
    def __init__(self):
        self.genome = GenomeStub()
        self._state = {"developmental_genome": self.genome.snapshot()}

    def _gene(self, name: str) -> float:
        return float(self.genome.genes[name])


def test_balanced_architecture_trials_select_from_real_target_outcomes(tmp_path: Path):
    substrate = SubstrateStub()
    search = DevelopmentalArchitectureSearch(tmp_path, substrate=substrate)

    assignments = []
    final = None
    for index in range(MIN_PER_VARIANT * 2):
        assigned = search.assign(
            world_id="WORLD_0001",
            source_trial_id=f"development-transfer:{index}",
            now=NOW,
        )
        trial = assigned["trial"]
        assignments.append(trial["assignment"])
        final = search.settle(
            trial_id=trial["trial_id"],
            utility=0.80 if trial["assignment"] == "ADAPTIVE_FAST" else 0.45,
            target_status="OK",
            observed_at=NOW,
        )

    assert assignments.count("ADAPTIVE_FAST") == MIN_PER_VARIANT
    assert assignments.count("CONSERVATIVE_STABLE") == MIN_PER_VARIANT
    assert final is not None
    assert final["evaluation"]["status"] == "SELECTED"
    assert final["evaluation"]["selected_variant"] == "ADAPTIVE_FAST"
    projection = search.projection()
    assert projection["selected_variant"] == "ADAPTIVE_FAST"
    assert projection["selection_history"][-1]["causal_source"] == "REAL_TARGET_OUTCOMES"


def test_trial_overlay_changes_effective_genes_but_restores_inherited_genome(tmp_path: Path):
    substrate = SubstrateStub()
    search = DevelopmentalArchitectureSearch(tmp_path, substrate=substrate)
    assigned = search.assign(
        world_id="WORLD_0001",
        source_trial_id="development-transfer:1",
        now=NOW,
    )
    trial = assigned["trial"]
    before = substrate.genome.snapshot()

    with search.trial_overlay(trial["trial_id"]) as overlay:
        assert overlay["status"] == "OVERLAY_ACTIVE"
        expected = VARIANTS[trial["assignment"]]["birth_threshold_scale"]
        assert substrate._gene("birth_threshold_scale") == expected
        # Simulate genome adaptation during training; the unselected phenotype
        # itself must not leak this into inherited state.
        substrate.genome.state["genes"]["birth_threshold_scale"] = 1.23
        substrate.genome.state["mutation_count"] = 99
        substrate.genome.state["history"].append({"mutation_seq": 99})

    assert substrate._gene("birth_threshold_scale") == 1.0
    assert substrate.genome.snapshot() == before
    assert substrate._state["developmental_genome"] == before


def test_selected_architecture_is_reapplied_after_runtime_restart(tmp_path: Path):
    first_substrate = SubstrateStub()
    search = DevelopmentalArchitectureSearch(tmp_path, substrate=first_substrate)
    for index in range(MIN_PER_VARIANT * 2):
        trial = search.assign(
            world_id="WORLD_0002",
            source_trial_id=f"development-transfer:{index}",
            now=NOW,
        )["trial"]
        search.settle(
            trial_id=trial["trial_id"],
            utility=0.72 if trial["assignment"] == "ADAPTIVE_FAST" else 0.40,
            target_status="OK",
            observed_at=NOW,
        )

    second_substrate = SubstrateStub()
    restored = DevelopmentalArchitectureSearch(tmp_path, substrate=second_substrate)

    assert restored.projection()["selected_variant"] == "ADAPTIVE_FAST"
    assert second_substrate._gene("birth_threshold_scale") == 0.90
    assert second_substrate._gene("maturation_time_scale") == 0.80


def test_projection_does_not_select_without_a_new_settlement(tmp_path: Path):
    substrate = SubstrateStub()
    search = DevelopmentalArchitectureSearch(tmp_path, substrate=substrate)
    # Keep one arm below the evidence floor.
    for index in range((MIN_PER_VARIANT * 2) - 1):
        trial = search.assign(
            world_id="WORLD_0003",
            source_trial_id=f"development-transfer:{index}",
            now=NOW,
        )["trial"]
        search.settle(
            trial_id=trial["trial_id"],
            utility=0.5,
            target_status="OK",
            observed_at=NOW,
        )

    projection = search.projection()
    assert projection["selected_variant"] is None
    assert projection["evaluation"]["status"] == "COLLECTING_EVIDENCE"
