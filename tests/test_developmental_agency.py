from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from wechat_traffic_agent.developmental_agency import DevelopmentalAgency


NOW = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)


def _life():
    return {
        "drives": {"survival": 0.7, "reproduction": 0.6, "evolution": 0.55},
        "affect": {
            "frustration": 0.57,
            "confidence": 0.58,
            "curiosity": 0.52,
        },
        "history": [],
    }


def _transfer(effect: float = -0.533333):
    return {
        "causal_design": "STRATIFIED_BY_SOURCE_TARGET_PAIR_V2",
        "strata": {
            "ROVER->CHECK_X": {
                "source_environment": "ROVER",
                "target_task": "CHECK_X",
                "complete": True,
                "estimated_target_utility_effect": effect,
                "arms": {
                    "TREATMENT": {"count": 3, "mean_target_utility": 0.216667},
                    "CONTROL": {"count": 1, "mean_target_utility": 0.75},
                },
            }
        },
    }


def _write_transfer(root: Path):
    path = root / "application" / "neural_transfer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for seq, arm in enumerate(("TREATMENT", "TREATMENT", "TREATMENT", "CONTROL"), 1):
        rows.append({
            "experiment_id": f"transfer:{seq:06d}",
            "seq": seq,
            "assignment": arm,
            "source_environment": "ROVER",
            "target_task": "CHECK_X",
            "status": "SETTLED",
        })
    path.write_text(json.dumps({"experiments": rows}), encoding="utf-8")


def test_negative_transfer_becomes_evidence_bound_development_need(tmp_path: Path):
    _write_transfer(tmp_path)
    agency = DevelopmentalAgency(tmp_path)
    projection = agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(),
        neural_motor={"recent_decisions": []},
    )

    assert projection["active_need_count"] == 1
    need = projection["needs"][0]
    assert need["kind"] == "NEGATIVE_TRANSFER"
    assert need["source_environment"] == "ROVER"
    assert need["target_task"] == "CHECK_X"
    assert need["severity"] == 0.533333
    assert need["neural_pressure"] > 0.4
    assert need["llm_used_for_detection"] is False
    evidence = need["evidence"][-1]
    assert evidence["effect"] == -0.533333
    assert evidence["experiment_refs"] == [
        "transfer:000001",
        "transfer:000002",
        "transfer:000003",
        "transfer:000004",
    ]


def test_refreshing_same_fact_does_not_manufacture_evidence(tmp_path: Path):
    _write_transfer(tmp_path)
    agency = DevelopmentalAgency(tmp_path)
    first = agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(),
        neural_motor={"recent_decisions": []},
    )
    second = agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(),
        neural_motor={"recent_decisions": []},
    )
    assert first["needs"][0]["evidence_count"] == 1
    assert second["needs"][0]["evidence_count"] == 1


def test_active_need_creates_non_llm_prospective_development_proposal(tmp_path: Path):
    _write_transfer(tmp_path)
    agency = DevelopmentalAgency(tmp_path)
    projection = agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(),
        neural_motor={"recent_decisions": []},
    )
    assert projection["proposal_count"] == 1
    proposal = projection["proposals"][0]
    assert proposal["strategy"] == "ALTERNATIVE_TRAINING_WORLD"
    assert proposal["authority"] == "NEURAL_DEVELOPMENT_PRESSURE"
    assert proposal["target_task"] == "CHECK_X"
    assert proposal["avoid_source_environment"] == "ROVER"
    assert proposal["llm_used_for_proposal"] is False
    assert proposal["experiment_contract"]["prospective_assignment_required"] is True
    assert proposal["experiment_contract"]["adopt_only_if_world_outcome_improves"] is True


def test_proposal_generates_bounded_ferro_born_world_without_external_authority(tmp_path: Path):
    _write_transfer(tmp_path)
    agency = DevelopmentalAgency(tmp_path)
    projection = agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(),
        neural_motor={"recent_decisions": []},
    )
    assert projection["ferro_born_world_count"] == 1
    world = projection["worlds"][0]
    assert world["world_id"] == "WORLD_0001"
    assert world["generation"] == "FERRO_BORN"
    assert world["target_task"] == "CHECK_X"
    assert world["avoids_environment"] == "ROVER"
    assert 3 <= len(world["actions"]) <= 4
    assert len(world["sensor_channels"]) == 4
    assert world["safety"]["brain_lab_only"] is True
    assert world["safety"]["no_external_actions"] is True
    assert world["safety"]["no_llm_motor_loop"] is True


def test_recovered_transfer_resolves_existing_need_without_erasing_history(tmp_path: Path):
    _write_transfer(tmp_path)
    agency = DevelopmentalAgency(tmp_path)
    agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(),
        neural_motor={"recent_decisions": []},
    )
    projection = agency.refresh(
        now=NOW,
        neural_life=_life(),
        neural_transfer=_transfer(effect=0.1),
        neural_motor={"recent_decisions": []},
    )
    need = projection["needs"][0]
    assert need["status"] == "RESOLVED"
    assert need["evidence_count"] == 2
    assert need["evidence"][-1]["kind"] == "TRANSFER_EFFECT_RECOVERED"


def test_affect_and_motor_failure_create_executable_worlds_and_recover(tmp_path: Path):
    agency = DevelopmentalAgency(tmp_path)
    distressed = _life()
    distressed["history"] = [
        {"at": f"2026-09-20T0{i}:00:00+00:00", "affect": {"frustration": 0.9, "confidence": 0.2}}
        for i in range(8)
    ]
    failed_motor = {"recent_decisions": [
        {"decision_id": f"motor-decision:{i}", "task": "CHECK_X", "status": "SETTLED",
         "outcome": {"reward": -0.8}} for i in range(4)
    ]}
    first = agency.refresh(now=NOW, neural_life=distressed, neural_transfer={"strata": {}},
                           neural_motor=failed_motor)
    assert {item["strategy"] for item in first["proposals"]} == {
        "DEVELOPMENTAL_EXPLORATION", "MOTOR_CURRICULUM_SEARCH",
    }
    assert {item["target_task"] for item in first["worlds"]} == {"REFLECT", "CHECK_X"}
    assert agency.next_candidate_world() is not None

    recovered = _life()
    recovered["history"] = [
        {"at": f"2026-09-20T1{i}:00:00+00:00", "affect": {"frustration": 0.1, "confidence": 0.8}}
        for i in range(8)
    ]
    successful_motor = {"recent_decisions": [
        {"decision_id": f"motor-decision:recovered-{i}", "task": "CHECK_X", "status": "SETTLED",
         "outcome": {"reward": 0.8}} for i in range(4)
    ]}
    second = agency.refresh(now=NOW, neural_life=recovered, neural_transfer={"strata": {}},
                            neural_motor=successful_motor)
    assert all(need["status"] == "RESOLVED" for need in second["needs"])
    assert agency.next_candidate_world() is None
