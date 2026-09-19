from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.memory_self import (
    AuthoritativeStateAdapters,
    ProspectiveSelfMemoryService,
)


def test_prospective_memory_stores_reference_not_mutable_runtime_copy(tmp_path):
    current = {"goal_id": "goal:1", "status": "ACTIVE", "description": "Grow X"}
    adapters = AuthoritativeStateAdapters()
    adapters.register("goal_runtime", lambda object_id: current if object_id == "goal:1" else None)
    service = ProspectiveSelfMemoryService(tmp_path, adapters=adapters)
    memory = service.remember_reference(
        reference_kind="GOAL",
        object_id="goal:1",
        description="Current X growth goal",
        authority="goal_runtime",
        source_ref="goal-event:1",
    )
    payload = memory["payload"]
    assert payload["object_id"] == "goal:1"
    assert "status" not in payload
    assert service.resolve_reference(memory["memory_id"])["status"] == "ACTIVE"
    current["status"] = "COMPLETED"
    assert service.resolve_reference(memory["memory_id"])["status"] == "COMPLETED"


def test_self_snapshot_reads_live_runtime_and_mind_authority(tmp_path):
    goal = SimpleNamespace(goal_id="goal:1", description="Ship memory", status="ACTIVE", priority=0.8)
    mission = SimpleNamespace(
        mission_id="mission:1", goal_id="goal:1", description="Finish roadmap",
        metric="milestones", target=12, status="ACTIVE", deadline=None,
    )
    domain_runtime = SimpleNamespace(active_goals=[goal], external_missions=[mission])
    application = SimpleNamespace(
        domain_runtime=domain_runtime,
        runtime=SimpleNamespace(capabilities={"web.search": object(), "x.read": object()}),
    )
    mind = SimpleNamespace(public_projection=lambda: {
        "mind_schema": 7,
        "mind_tick": 42,
        "interests": [{"name": "memory"}],
        "open_inquiries": [{"question": "how to recall"}],
        "beliefs": [{"proposition": "memory is structured"}],
        "latest_plan": {"goal": "M9"},
        "resource_awareness": {"pressure": "NORMAL"},
        "predictions": [{"prediction_id": "p:1"}],
    })
    packet = ProspectiveSelfMemoryService.snapshot_from_runtime(application, mind)
    assert {row["kind"] for row in packet.current_intent} == {"GOAL", "MISSION"}
    assert packet.current_resources["pressure"] == "NORMAL"
    assert set(packet.current_capabilities) == {"web.search", "x.read"}
    assert packet.current_predictions[0]["prediction_id"] == "p:1"
    assert packet.self_model["mind_tick"] == 42


def test_unknown_authority_reference_fails_closed_at_resolution(tmp_path):
    service = ProspectiveSelfMemoryService(tmp_path)
    memory = service.remember_reference(
        reference_kind="RESOURCE",
        object_id="resource:1",
        description="Live resource",
        authority="resource_runtime",
        source_ref="resource-event:1",
    )
    assert service.resolve_reference(memory["memory_id"]) is None
