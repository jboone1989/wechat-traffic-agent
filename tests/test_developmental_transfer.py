from __future__ import annotations

from datetime import datetime, timezone
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
        lab.settle_target(task="CHECK_X", result=_x_result(strong=True), observed_at=NOW)

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
        settled = lab.settle_target(task="CHECK_X", result=result, observed_at=NOW)
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
        lab.begin(now=NOW)
        lab.settle_target(task="CHECK_X", result=_x_result(strong=True), observed_at=NOW)

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
