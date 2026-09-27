from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from wechat_traffic_agent.developmental_transfer import DevelopmentalWorldTransferLab


NOW = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)


class AgencyStub:
    def __init__(self):
        self.state = {
            "worlds": {
                "WORLD_0001": {
                    "world_id": "WORLD_0001",
                    "status": "EXPERIMENTING",
                    "need_id": "need:1",
                    "proposal_id": "proposal:1",
                    "target_task": "CHECK_X",
                    "trial_count": 1,
                }
            }
        }
        self.recorded = []
        self.evaluations = []

    def next_candidate_world(self):
        row = self.state["worlds"]["WORLD_0001"]
        if row["status"] not in {"CANDIDATE", "EXPERIMENTING"}:
            return None
        return dict(row)

    def record_world_trial(self, *, world_id, result, observed_at):
        self.recorded.append((world_id, dict(result), observed_at))
        return dict(self.state["worlds"][world_id])

    def apply_world_transfer_evaluation(self, *, world_id, evaluation, observed_at):
        self.evaluations.append((world_id, dict(evaluation), observed_at))
        decision = evaluation["decision"]
        self.state["worlds"][world_id]["status"] = {
            "ADOPT": "ADOPTED",
            "REJECT": "REJECTED",
            "REVISE": "REVISE_REQUIRED",
        }.get(decision, "EXPERIMENTING")
        return dict(self.state["worlds"][world_id])


class BrainLabStub:
    def __init__(self, state_root: Path):
        self.state_root = state_root
        self.calls = []
        self.assignment_seen_before_training = []

    def run_session(self, *, environment, steps, now):
        ledger = json.loads(
            (self.state_root / "application" / "developmental_transfer.json").read_text(
                encoding="utf-8"
            )
        )
        self.assignment_seen_before_training.append(
            ledger["trials"][-1]["status"] == "ASSIGNED"
        )
        self.calls.append((environment, steps, now))
        return {
            "status": "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM",
            "environment": environment,
            "steps": steps,
            "reward": 1.2,
            "changed_synapses": 64,
        }


def _x_result(*, strong: bool):
    observation = {
        "status": "OK_BROWSER_FALLBACK",
        "identity_receipt_id": "receipt:id",
        "mention_count": 0,
        "x_growth_mission": {"current": 1835},
    }
    if strong:
        observation["x_growth_mission"]["metric_observation_id"] = "mission-observation:1"
    return {"task": "CHECK_X", "status": "OK", "x_social_observation": observation}


def test_assignment_is_balanced_and_persisted_before_treatment(tmp_path: Path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)

    assignments = []
    for _ in range(8):
        started = lab.begin(now=NOW)
        row = started["trial"]
        assignments.append(row["assignment"])
        lab.settle_target(task="CHECK_X", result=_x_result(strong=True), trial_id=row["trial_id"], observed_at=NOW)

    assert assignments.count("TREATMENT") == 4
    assert assignments.count("CONTROL") == 4
    assert brain.assignment_seen_before_training == [True] * 4
    assert len(brain.calls) == 4
    assert len(agency.recorded) == 4


def test_real_target_evidence_can_adopt_world_after_four_per_arm(tmp_path: Path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)

    for _ in range(8):
        row = lab.begin(now=NOW)["trial"]
        # Strong target evidence is deliberately associated with the treatment
        # arm in this test; the ledger never sees the outcome before assignment.
        result = _x_result(strong=row["assignment"] == "TREATMENT")
        settled = lab.settle_target(task="CHECK_X", result=result, trial_id=row["trial_id"], observed_at=NOW)
        assert settled is not None

    projection = lab.projection()["worlds"]["WORLD_0001"]
    assert projection["arms"]["TREATMENT"]["count"] == 4
    assert projection["arms"]["CONTROL"]["count"] == 4
    assert projection["causal_claim_ready"] is True
    assert projection["estimated_real_world_effect"] == 0.15
    assert projection["decision"] == "ADOPT"
    assert agency.state["worlds"]["WORLD_0001"]["status"] == "ADOPTED"


def test_no_real_world_advantage_requires_revision_not_self_congratulation(tmp_path: Path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)

    for _ in range(8):
        trial = lab.begin(now=NOW)["trial"]
        lab.settle_target(task="CHECK_X", result=_x_result(strong=True), trial_id=trial["trial_id"], observed_at=NOW)

    projection = lab.projection()["worlds"]["WORLD_0001"]
    assert projection["estimated_real_world_effect"] == 0.0
    assert projection["decision"] == "REVISE"
    assert agency.state["worlds"]["WORLD_0001"]["status"] == "REVISE_REQUIRED"


def test_pending_trial_blocks_second_intervention(tmp_path: Path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)

    first = lab.begin(now=NOW)
    second = lab.begin(now=NOW)

    assert second["status"] == "PENDING_EXISTS"
    assert second["trial"]["trial_id"] == first["trial"]["trial_id"]


def test_shutdown_checkpoint_is_inconclusive_and_excluded_from_effect(tmp_path: Path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)

    started = lab.begin(now=NOW)
    row = lab.settle_target(
        task="CHECK_X",
        result={"task": "CHECK_X", "status": "SHUTDOWN_CHECKPOINT"},
        trial_id=started["trial"]["trial_id"],
        observed_at=NOW,
    )

    assert row is not None
    assert row["status"] == "INCONCLUSIVE"
    assert row["target_outcome"]["utility"] is None
    projection = lab.projection()
    assert projection["settled_count"] == 0
    assert projection["worlds"]["WORLD_0001"]["arms"]["TREATMENT"]["count"] == 0
    assert projection["worlds"]["WORLD_0001"]["arms"]["CONTROL"]["count"] == 0


def test_restart_migrates_old_shutdown_trial_out_of_causal_sample(tmp_path: Path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    path = tmp_path / "application" / "developmental_transfer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "ferro-developmental-transfer.v1",
        "updated_at": NOW.isoformat(),
        "seq": 1,
        "trials": [{
            "trial_id": "development-transfer:world_0001:000001",
            "seq": 1,
            "world_id": "WORLD_0001",
            "target_task": "CHECK_X",
            "assignment": "CONTROL",
            "status": "SETTLED",
            "target_outcome": {
                "observed_at": NOW.isoformat(),
                "status": "SHUTDOWN_CHECKPOINT",
                "utility": -0.85,
            },
        }],
    }), encoding="utf-8")

    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)
    projection = lab.projection()

    assert projection["settled_count"] == 0
    assert projection["recent"][0]["status"] == "INCONCLUSIVE"
    assert projection["recent"][0]["target_outcome"]["utility"] is None


from wechat_traffic_agent.developmental_architecture import DevelopmentalArchitectureSearch


class ArchitectureGenomeStub:
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
            "mutation_count": 0,
            "history": [],
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


class ArchitectureSubstrateStub:
    def __init__(self):
        self.genome = ArchitectureGenomeStub()
        self._state = {"developmental_genome": self.genome.snapshot()}

    def _gene(self, name: str) -> float:
        return float(self.genome.genes[name])


class ArchitectureBrainLab(BrainLabStub):
    def __init__(self, state_root: Path, substrate: ArchitectureSubstrateStub):
        super().__init__(state_root)
        self.substrate = substrate
        self.effective_birth_scales = []

    def run_session(self, *, environment, steps, now):
        self.effective_birth_scales.append(
            self.substrate._gene("birth_threshold_scale")
        )
        return super().run_session(environment=environment, steps=steps, now=now)


def test_n29_treatment_executes_persisted_n33_phenotype_before_real_outcome(tmp_path: Path):
    agency = AgencyStub()
    substrate = ArchitectureSubstrateStub()
    brain = ArchitectureBrainLab(tmp_path, substrate)
    architecture = DevelopmentalArchitectureSearch(tmp_path, substrate=substrate)
    lab = DevelopmentalWorldTransferLab(
        tmp_path,
        brain_lab=brain,
        agency=agency,
        architecture=architecture,
    )

    treatment_row = None
    settled = None
    for _ in range(2):
        row = lab.begin(now=NOW)["trial"]
        if row["assignment"] == "TREATMENT":
            treatment_row = row
            assert row["architecture_trial_id"]
            assert row["architecture_assignment"] in {
                "ADAPTIVE_FAST", "CONSERVATIVE_STABLE",
            }
            assert row["architecture_overlay"]["status"] == "OVERLAY_ACTIVE"
        settled = lab.settle_target(
            task="CHECK_X",
            result=_x_result(strong=True),
            trial_id=row["trial_id"],
            observed_at=NOW,
        )

    assert treatment_row is not None
    assert brain.effective_birth_scales
    assert any(value != 1.0 for value in brain.effective_birth_scales)
    architecture_state = architecture.projection()
    assert architecture_state["trial_count"] == 1
    assert architecture_state["trials"][0]["status"] == "SETTLED"
    assert settled is not None


class AdoptedAgencyStub(AgencyStub):
    def __init__(self):
        super().__init__()
        self.state["worlds"]["WORLD_0001"]["status"] = "ADOPTED"
        self.state["worlds"]["WORLD_0001"]["last_transfer_evaluation_at"] = NOW.isoformat()

    def next_candidate_world(self):
        return None


class AdoptedWithCandidateAgencyStub(AdoptedAgencyStub):
    def __init__(self):
        super().__init__()
        self.state["worlds"]["WORLD_0002"] = {
            "world_id": "WORLD_0002",
            "status": "CANDIDATE",
            "need_id": "need:2",
            "proposal_id": "proposal:2",
            "target_task": "REFLECT",
            "trial_count": 0,
        }

    def next_candidate_world(self):
        return dict(self.state["worlds"]["WORLD_0002"])


def _select_adaptive_architecture(
    architecture: DevelopmentalArchitectureSearch,
) -> None:
    for index in range(8):
        assigned = architecture.assign(
            world_id="WORLD_0001",
            source_trial_id=f"source-initial-{index}",
            now=NOW + timedelta(minutes=index),
        )
        trial = assigned["trial"]
        utility = (
            0.8
            if trial["assignment"] == "ADAPTIVE_FAST"
            else 0.4
        )
        architecture.settle(
            trial_id=trial["trial_id"],
            utility=utility,
            target_status="OK",
            observed_at=NOW + timedelta(minutes=index, seconds=30),
        )
    assert architecture.projection()["selected_variant"] == "ADAPTIVE_FAST"


def test_due_architecture_revalidation_preempts_new_world_to_avoid_causal_confounding(
    tmp_path: Path,
):
    agency = AdoptedWithCandidateAgencyStub()
    substrate = ArchitectureSubstrateStub()
    brain = ArchitectureBrainLab(tmp_path, substrate)
    architecture = DevelopmentalArchitectureSearch(
        tmp_path,
        substrate=substrate,
    )
    _select_adaptive_architecture(architecture)
    lab = DevelopmentalWorldTransferLab(
        tmp_path,
        brain_lab=brain,
        agency=agency,
        architecture=architecture,
    )

    started = lab.begin(now=NOW + timedelta(minutes=10))
    row = started["trial"]

    assert started["status"] == "STARTED"
    assert row["world_id"] == "WORLD_0001"
    assert row["world_status_at_assignment"] == "ADOPTED"
    assert row["assignment"] == "ARCHITECTURE_ONLY"
    assert row["architecture_only"] is True
    assert row["architecture_assignment"] in {"SELECTED", "BASELINE"}
    assert agency.state["worlds"]["WORLD_0002"]["status"] == "CANDIDATE"
    assert agency.recorded == []


def test_selected_architecture_is_rolled_back_after_real_world_regression(
    tmp_path: Path,
):
    substrate = ArchitectureSubstrateStub()
    architecture = DevelopmentalArchitectureSearch(
        tmp_path,
        substrate=substrate,
    )
    _select_adaptive_architecture(architecture)
    assert substrate._ferro_architecture_variant == "ADAPTIVE_FAST"
    assert architecture.needs_evidence(NOW + timedelta(minutes=10)) is True

    seen = set()
    final = None
    for index in range(6):
        assigned = architecture.assign(
            world_id="WORLD_0001",
            source_trial_id=f"source-revalidate-{index}",
            now=NOW + timedelta(minutes=10 + index),
        )
        trial = assigned["trial"]
        assert trial["phase"] == "POST_SELECTION_REVALIDATION"
        seen.add(trial["assignment"])
        with architecture.trial_overlay(trial["trial_id"]):
            effective = substrate._gene("birth_threshold_scale")
            if trial["assignment"] == "SELECTED":
                assert effective == 0.9
                utility = 0.2
            else:
                assert trial["assignment"] == "BASELINE"
                assert effective == 1.0
                utility = 0.7
        final = architecture.settle(
            trial_id=trial["trial_id"],
            utility=utility,
            target_status="OK",
            observed_at=NOW + timedelta(minutes=10 + index, seconds=30),
        )

    assert seen == {"SELECTED", "BASELINE"}
    assert final is not None
    assert final["evaluation"]["status"] == "ROLLED_BACK"
    assert final["evaluation"]["decision"] == "ROLLBACK"
    projection = architecture.projection()
    assert projection["selected_variant"] is None
    assert projection["search_epoch"] == 1
    assert getattr(substrate, "_ferro_architecture_variant", None) is None
    assert projection["selection_history"][-1]["action"] == (
        "ROLLED_BACK_AFTER_REAL_WORLD_REGRESSION"
    )


def test_selected_architecture_is_retained_then_revalidated_again_later(
    tmp_path: Path,
):
    substrate = ArchitectureSubstrateStub()
    architecture = DevelopmentalArchitectureSearch(
        tmp_path,
        substrate=substrate,
    )
    _select_adaptive_architecture(architecture)

    final = None
    for index in range(6):
        assigned = architecture.assign(
            world_id="WORLD_0001",
            source_trial_id=f"source-retain-{index}",
            now=NOW + timedelta(minutes=10 + index),
        )
        trial = assigned["trial"]
        utility = 0.75 if trial["assignment"] == "SELECTED" else 0.60
        final = architecture.settle(
            trial_id=trial["trial_id"],
            utility=utility,
            target_status="OK",
            observed_at=NOW + timedelta(minutes=10 + index, seconds=30),
        )

    assert final is not None
    assert final["evaluation"]["status"] == "RETAINED"
    projection = architecture.projection()
    assert projection["selected_variant"] == "ADAPTIVE_FAST"
    assert projection["post_selection_round"] == 1
    assert projection["selection_history"][-1]["action"] == (
        "RETAINED_AFTER_REAL_WORLD_REVALIDATION"
    )
    retained_at = NOW + timedelta(minutes=15, seconds=30)
    assert architecture.needs_evidence(retained_at + timedelta(days=1)) is False
    assert architecture.needs_evidence(retained_at + timedelta(days=8)) is True


def test_architecture_only_reuses_adopted_world_without_reopening_n29(tmp_path: Path):
    agency = AdoptedAgencyStub()
    substrate = ArchitectureSubstrateStub()
    brain = ArchitectureBrainLab(tmp_path, substrate)
    architecture = DevelopmentalArchitectureSearch(tmp_path, substrate=substrate)
    lab = DevelopmentalWorldTransferLab(
        tmp_path,
        brain_lab=brain,
        agency=agency,
        architecture=architecture,
    )

    started = lab.begin(now=NOW)
    row = started["trial"]

    assert started["status"] == "STARTED"
    assert row["assignment"] == "ARCHITECTURE_ONLY"
    assert row["architecture_only"] is True
    assert row["world_status_at_assignment"] == "ADOPTED"
    assert row["architecture_trial_id"]
    assert agency.state["worlds"]["WORLD_0001"]["status"] == "ADOPTED"
    assert agency.recorded == []

    settled = lab.settle_target(
        task="CHECK_X",
        result=_x_result(strong=True),
        trial_id=row["trial_id"],
        observed_at=NOW,
    )

    assert settled is not None
    assert settled["architecture_evaluation"]["status"] == "SETTLED"
    assert agency.state["worlds"]["WORLD_0001"]["status"] == "ADOPTED"
    assert agency.evaluations == []
    world_projection = lab.projection()["worlds"]["WORLD_0001"]
    assert world_projection["arms"]["TREATMENT"]["count"] == 0
    assert world_projection["arms"]["CONTROL"]["count"] == 0


def test_real_world_utility_overrides_ok_transport_status(tmp_path):
    agency = AgencyStub()
    brain = BrainLabStub(tmp_path)
    lab = DevelopmentalWorldTransferLab(tmp_path, brain_lab=brain, agency=agency)
    trial = lab.begin(now=NOW)["trial"]
    assert lab.settle_target(task="CHECK_X", result={"status": "OK", "world_utility": -1.0},
                             observed_at=NOW) is None
    settled = lab.settle_target(task="CHECK_X", result={"status": "OK", "world_utility": -1.0},
                                trial_id=trial["trial_id"], observed_at=NOW)
    assert settled is not None
    assert settled["target_outcome"]["utility"] == -1.0
