"""Goal formation and mission discovery are callable without the publication stack."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop


def test_prior_cycle_plan_is_loaded_without_replanning(tmp_path):
    plan = {"cycle": "v2-test", "required_topic": "ai-memory", "goal": {"origin": "INQUIRY"}}
    target = tmp_path / "mind_plan.json"
    target.write_text(json.dumps(plan), encoding="utf-8")
    result = asyncio.run(ContinuousContentLoop._choose_mind_plan(
        SimpleNamespace(), tmp_path, "v2-test", None, {}, None, None, None,
        False, target, True,
    ))
    assert result == plan


def test_stable_dream_requires_multi_epoch_evidence():
    selection = ContinuousContentLoop._select_stable_attractor
    candidate = {"status": "UNINTERPRETED", "candidate_id": "goal-1"}
    rows = {
        "weak": {
            "status": "STABLE", "goal_candidate": candidate,
            "consolidation_epoch_count": 2, "stability_score": 0.99,
        },
        "verified": {
            "status": "STABLE", "goal_candidate": candidate,
            "consolidation_epoch_count": 3, "stability_score": 0.6,
        },
    }
    selected, attractor = selection({"attractors": rows})
    assert selected == candidate
    assert attractor == rows["verified"]
    assert selection({"attractors": {"weak": rows["weak"]}}) == (None, None)


def test_external_mission_maps_to_topic_without_claiming_publication():
    decision = SimpleNamespace(
        reader_need="Explain memory causality",
        topic="ai-memory-causality",
        model_dump=lambda **kwargs: {"topic": "ai-memory-causality"},
    )
    plan = ContinuousContentLoop._apply_mission_discovery_plan(
        {"goal": None}, {"mission_id": "mission-1", "roadmap_step": {"step_id": "step-1"}},
        {"goal_id": "owner-1"}, decision,
    )
    assert plan["goal"]["origin"] == "EXTERNAL_MISSION"
    assert plan["required_topic"] == "ai-memory-causality"
    assert plan["goal"]["goal_id"] == "external-mission-discovery:mission-1:step-1"
    assert plan["owner_goal_id"] == "owner-1"


def test_owner_mission_failure_preserves_retry_identity(tmp_path):
    plan_path = tmp_path / "mind_plan.json"
    previous = {"goal": None}
    changed, error = ContinuousContentLoop._failed_mission_discovery(
        SimpleNamespace(), tmp_path, "v2-cycle", plan_path, previous,
        {"mission_id": "mission-1", "roadmap_step": {"step_id": "step-1"}},
        {"goal_id": "owner-1", "description": "Investigate memory"}, RuntimeError("offline"),
    )
    assert error["status"] == "ERROR" and error["retryable"] is True
    assert changed["goal"]["origin"] == "EXTERNAL_MISSION"
    assert changed["goal"]["goal_id"] == "external-mission-pending:mission-1"
    assert json.loads(plan_path.read_text())["owner_goal_id"] == "owner-1"
    record = json.loads((tmp_path / "mission_discovery_error.json").read_text())
    assert record["publication_authority"] is False
    assert record["roadmap_step_id"] == "step-1"


def test_mission_failure_without_owner_does_not_fabricate_owner_goal(tmp_path):
    plan = {"goal": None}
    changed, error = ContinuousContentLoop._failed_mission_discovery(
        SimpleNamespace(), tmp_path, "v2-cycle", tmp_path / "mind_plan.json",
        plan, {"mission_id": "mission-1"}, None, ValueError("invalid"),
    )
    assert changed == plan and error is None
    assert not (tmp_path / "mind_plan.json").exists()
