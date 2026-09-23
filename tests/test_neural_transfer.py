from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.neural_transfer import CrossDomainTransferLab, PAIRS


NOW = datetime(2026, 9, 20, 2, 0, tzinfo=timezone.utc)


class FakeSubstrate:
    def __init__(self) -> None:
        self.calls = []
        self.synthetic = 3

    def stimulate(self, currents, observed_at, *, source_event_id=None, feedback_eligible=None):
        self.calls.append((dict(currents), source_event_id, feedback_eligible))
        # Make treatment/control probes deterministic but nonzero.
        scale = 1.0 + 0.02 * len(self.calls)
        return {
            "delta_readout_hz": {key: float(value) * 10.0 * scale for key, value in currents.items()},
            "spikes": 1000 + len(self.calls),
            "active_neurons": 500 + len(self.calls),
        }

    def projection(self):
        return {
            "neuron_count": 166_700 + self.synthetic,
            "developmental_neurogenesis": {
                "synthetic_neuron_count": self.synthetic,
                "stable_circuit_count": 1,
                "organ_count": 0,
                "species_stage": "FERRO_F0_DEVELOPING",
            },
        }


class FakeBrainLab:
    def __init__(self, substrate: FakeSubstrate) -> None:
        self.substrate = substrate
        self.calls = []

    def run_session(self, *, environment, steps, now):
        self.calls.append((environment, steps, now))
        self.substrate.synthetic += 1
        return {
            "status": "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM",
            "environment": environment,
            "steps": steps,
            "reward": 1.5,
            "changed_synapses": 32,
        }


def test_assignment_is_persisted_before_treatment_and_target_is_pending(tmp_path: Path):
    substrate = FakeSubstrate()
    brain = FakeBrainLab(substrate)
    lab = CrossDomainTransferLab(tmp_path, substrate=substrate, brain_lab=brain)

    first = lab.begin(now=NOW, training_steps=7)
    row = first["experiment"]
    assert first["status"] == "STARTED"
    assert row["assignment"] in {"TREATMENT", "CONTROL"}
    assert row["source_environment"] == PAIRS[0][0]
    assert row["target_task"] == PAIRS[0][1]
    assert row["status"] == "AWAITING_TARGET_OUTCOME"
    assert row["pre_structure"]["synthetic_neuron_count"] == 3
    if row["assignment"] == "TREATMENT":
        assert brain.calls == [(row["source_environment"], 7, NOW)]
        assert row["post_structure"]["synthetic_neuron_count"] == 4
    else:
        assert brain.calls == []
        assert row["post_structure"]["synthetic_neuron_count"] == 3

    # A second intervention must not start before the assigned real target settles.
    repeated = lab.begin(now=NOW, training_steps=7)
    assert repeated["status"] == "PENDING_EXISTS"
    assert repeated["experiment"]["experiment_id"] == row["experiment_id"]


def test_real_target_outcome_settles_prospective_trial(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = CrossDomainTransferLab(tmp_path, substrate=substrate, brain_lab=FakeBrainLab(substrate))
    started = lab.begin(now=NOW)
    row = started["experiment"]

    assert lab.settle_target(task="NOT_THE_TARGET", result={"status": "OK"}, observed_at=NOW) is None
    settled = lab.settle_target(
        task=row["target_task"],
        result={"status": "OK"},
        experiment_id=row["experiment_id"],
        observed_at=NOW,
    )
    assert settled is not None
    assert settled["status"] == "SETTLED"
    assert settled["target_outcome"]["utility"] == 0.75
    assert lab.pending() is None


def test_projection_requires_source_target_stratification_before_causal_claim(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = CrossDomainTransferLab(tmp_path, substrate=substrate, brain_lab=FakeBrainLab(substrate))

    # Total arm counts alone are insufficient when all observations come from
    # one task pair: task difficulty would be confounded with assignment.
    single_pair = []
    for i in range(8):
        assignment = "TREATMENT" if i % 2 else "CONTROL"
        single_pair.append({
            "experiment_id": f"transfer:single:{i}",
            "seq": i,
            "assignment": assignment,
            "source_environment": "MAZE",
            "target_task": "OBSERVE_WORLD",
            "status": "SETTLED",
            "probe_delta": 1.0 if assignment == "TREATMENT" else 0.2,
            "target_outcome": {
                "observed_at": NOW.isoformat(),
                "status": "OK",
                "utility": 0.8 if assignment == "TREATMENT" else 0.3,
            },
        })
    lab.state["experiments"] = single_pair
    projection = lab.projection()
    assert projection["causal_claim_ready"] is False
    assert projection["complete_strata_count"] == 1
    assert projection["estimated_target_utility_effect"] is None
    assert projection["pooled_unadjusted_target_utility_effect"] == 0.5

    # Minimal stratified gate: every predefined source->target pair must have
    # prospective treatment and control evidence, in addition to total counts.
    balanced = []
    seq = 1
    for source_environment, target_task in PAIRS:
        for assignment in ("TREATMENT", "CONTROL"):
            balanced.append({
                "experiment_id": f"transfer:balanced:{seq}",
                "seq": seq,
                "assignment": assignment,
                "source_environment": source_environment,
                "target_task": target_task,
                "status": "SETTLED",
                "probe_delta": 1.0 if assignment == "TREATMENT" else 0.2,
                "target_outcome": {
                    "observed_at": NOW.isoformat(),
                    "status": "OK",
                    "utility": 0.8 if assignment == "TREATMENT" else 0.3,
                },
            })
            seq += 1
    lab.state["experiments"] = balanced
    projection = lab.projection()
    assert projection["causal_claim_ready"] is True
    assert projection["complete_strata_count"] == len(PAIRS)
    assert projection["estimated_target_utility_effect"] == 0.5
    assert projection["pooled_unadjusted_target_utility_effect"] == 0.5
    assert all(row["complete"] for row in projection["strata"].values())
    assert projection["arms"]["TREATMENT"]["mean_probe_delta"] == 1.0
    assert projection["arms"]["CONTROL"]["mean_probe_delta"] == 0.2


def test_next_trial_advances_source_target_pair_after_settlement(tmp_path: Path):
    substrate = FakeSubstrate()
    lab = CrossDomainTransferLab(tmp_path, substrate=substrate, brain_lab=FakeBrainLab(substrate))

    first = lab.begin(now=NOW)
    target = first["experiment"]["target_task"]
    lab.settle_target(task=target, result={"status": "OK"}, experiment_id=first["experiment"]["experiment_id"], observed_at=NOW)
    second = lab.begin(now=NOW)

    assert second["experiment"]["source_environment"] == PAIRS[1][0]
    assert second["experiment"]["target_task"] == PAIRS[1][1]
