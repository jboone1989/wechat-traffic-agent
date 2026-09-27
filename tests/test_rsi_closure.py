from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.developmental_agency import DevelopmentalAgency
from wechat_traffic_agent.developmental_history import DevelopmentalHistory
from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def _record_mastery_failures(history: DevelopmentalHistory, *, start: int = 0, count: int = 3):
    for index in range(start, start + count):
        history.record(
            kind="WORLD_GROUNDED_ACTION_OUTCOME",
            domain="motor:check_x",
            outcome=-0.8,
            evidence_refs=(f"real-world-failure:{index}",),
            now=NOW + timedelta(minutes=index),
        )
    return history.projection()


def _empty_neural_life():
    return {"affect": {}, "drives": {}, "history": []}


def test_mastery_need_becomes_self_generated_prospective_experiment(tmp_path):
    history = DevelopmentalHistory(tmp_path)
    development = _record_mastery_failures(history)
    transition = development["open_developmental_needs"][0]

    agency = DevelopmentalAgency(tmp_path)
    projection = agency.refresh(
        now=NOW + timedelta(minutes=4),
        neural_life=_empty_neural_life(),
        neural_transfer={},
        neural_motor={"recent_decisions": []},
        developmental_history=development,
    )

    mirrored = next(
        row for row in projection["needs"]
        if row.get("source_transition_id") == transition["transition_id"]
    )
    assert mirrored["status"] == "ACTIVE"
    assert mirrored["kind"] == "EVIDENCE_BOUND_MASTERY_NEED"
    assert mirrored["target_task"] == "CHECK_X"
    assert mirrored["neural_pressure"] >= 0.25

    proposal = next(
        row for row in projection["proposals"]
        if row["need_id"] == mirrored["need_id"]
    )
    assert proposal["experiment_contract"]["prospective_assignment_required"] is True
    assert proposal["experiment_contract"]["adopt_only_if_world_outcome_improves"] is True
    assert proposal["experiment_contract"]["rollback_required"] is True

    world = next(
        row for row in projection["worlds"]
        if row["need_id"] == mirrored["need_id"]
    )
    assert world["generation"] == "FERRO_BORN"
    assert world["target_task"] == "CHECK_X"
    assert world["safety"]["prospective_transfer_required_before_adoption"] is True


def test_resolved_mastery_need_can_reopen_after_real_regression(tmp_path):
    history = DevelopmentalHistory(tmp_path)
    first = _record_mastery_failures(history)
    original = first["open_developmental_needs"][0]

    resolved = history.resolve_transition(
        transition_id=original["transition_id"],
        evidence_ref="development-transfer-decision:adopted",
        now=NOW + timedelta(minutes=4),
        reason="prospective transfer improved the target task",
    )
    assert resolved is not None
    assert resolved["status"] == "RESOLVED"
    assert history.projection()["open_developmental_needs"] == []

    history.record(
        kind="WORLD_GROUNDED_ACTION_OUTCOME",
        domain="motor:check_x",
        outcome=-0.9,
        evidence_refs=("real-world-relapse:4",),
        now=NOW + timedelta(minutes=5),
    )
    reopened = history.projection()["open_developmental_needs"]

    assert len(reopened) == 1
    assert reopened[0]["kind"] == "MASTERY_NEED"
    assert reopened[0]["transition_id"] != original["transition_id"]


class _TransferResult:
    def __init__(self, payload):
        self.payload = dict(payload)

    def settle_target(self, **_kwargs):
        return dict(self.payload)


def _runtime_with_mastery_world(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    development = _record_mastery_failures(runtime.developmental_history)
    transition = development["open_developmental_needs"][0]
    projection = runtime.developmental_agency.refresh(
        now=NOW + timedelta(minutes=4),
        neural_life=_empty_neural_life(),
        neural_transfer={},
        neural_motor={"recent_decisions": []},
        developmental_history=development,
    )
    need = next(
        row for row in projection["needs"]
        if row.get("source_transition_id") == transition["transition_id"]
    )
    world = next(row for row in projection["worlds"] if row["need_id"] == need["need_id"])
    proposal = next(
        row for row in projection["proposals"]
        if row["proposal_id"] == world["proposal_id"]
    )
    return runtime, transition, need, proposal, world


def test_real_world_adopt_closes_weakness_and_updates_self_model(tmp_path, monkeypatch):
    runtime, transition, need, proposal, world = _runtime_with_mastery_world(tmp_path)
    observed_at = NOW + timedelta(hours=1)
    evaluation = {
        "world_id": world["world_id"],
        "causal_claim_ready": True,
        "decision": "ADOPT",
        "estimated_real_world_effect": 0.22,
        "arms": {
            "TREATMENT": {"count": 4, "mean_target_utility": 0.72},
            "CONTROL": {"count": 4, "mean_target_utility": 0.50},
        },
    }
    runtime.developmental_agency.apply_world_transfer_evaluation(
        world_id=world["world_id"],
        evaluation=evaluation,
        observed_at=observed_at,
    )
    runtime.developmental_world_transfer = _TransferResult({
        "trial_id": "development-transfer:world_0001:000008",
        "world_id": world["world_id"],
        "need_id": need["need_id"],
        "proposal_id": proposal["proposal_id"],
        "target_task": "CHECK_X",
        "assignment": "TREATMENT",
        "status": "SETTLED",
        "world_evaluation": evaluation,
    })
    monkeypatch.setattr(runtime.open_ended_evolution, "refresh", lambda now: {})

    settled = runtime.observe_development_transfer_outcome(
        task="CHECK_X",
        result={"status": "OK", "world_utility": 0.8},
        trial_id="development-transfer:world_0001:000008",
        observed_at=observed_at,
    )

    assert settled is not None
    assert settled["rsi_feedback"]["decision"] == "ADOPT"
    assert settled["rsi_feedback"]["source_transition_resolved"] is True
    assert runtime.developmental_history.projection()["open_developmental_needs"] == []
    assert runtime.developmental_agency.state["needs"][need["need_id"]]["status"] == "RESOLVED"
    belief = runtime.self_model.projection()["capability_beliefs"]["motor:check_x"]
    assert belief["successes"] == 1
    assert belief["supporting_evidence_count"] == 1


def test_real_world_reject_preserves_weakness_and_records_counterevidence(tmp_path, monkeypatch):
    runtime, transition, need, proposal, world = _runtime_with_mastery_world(tmp_path)
    observed_at = NOW + timedelta(hours=1)
    evaluation = {
        "world_id": world["world_id"],
        "causal_claim_ready": True,
        "decision": "REJECT",
        "estimated_real_world_effect": -0.24,
        "arms": {
            "TREATMENT": {"count": 4, "mean_target_utility": 0.31},
            "CONTROL": {"count": 4, "mean_target_utility": 0.55},
        },
    }
    runtime.developmental_agency.apply_world_transfer_evaluation(
        world_id=world["world_id"],
        evaluation=evaluation,
        observed_at=observed_at,
    )
    runtime.developmental_world_transfer = _TransferResult({
        "trial_id": "development-transfer:world_0001:000008",
        "world_id": world["world_id"],
        "need_id": need["need_id"],
        "proposal_id": proposal["proposal_id"],
        "target_task": "CHECK_X",
        "assignment": "TREATMENT",
        "status": "SETTLED",
        "world_evaluation": evaluation,
    })
    monkeypatch.setattr(runtime.open_ended_evolution, "refresh", lambda now: {})

    settled = runtime.observe_development_transfer_outcome(
        task="CHECK_X",
        result={"status": "OK", "world_utility": 0.2},
        trial_id="development-transfer:world_0001:000008",
        observed_at=observed_at,
    )

    assert settled is not None
    assert settled["rsi_feedback"]["decision"] == "REJECT"
    assert settled["rsi_feedback"]["source_transition_resolved"] is False
    open_needs = runtime.developmental_history.projection()["open_developmental_needs"]
    assert any(row["transition_id"] == transition["transition_id"] for row in open_needs)
    assert runtime.developmental_agency.state["needs"][need["need_id"]]["status"] == "ACTIVE"
    belief = runtime.self_model.projection()["capability_beliefs"]["motor:check_x"]
    assert belief["failures"] == 1
    assert belief["counter_evidence_count"] == 1
