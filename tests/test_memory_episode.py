from __future__ import annotations

from wechat_traffic_agent.memory_episode import EpisodeMemoryService
from wechat_traffic_agent.memory_retrieval import MemoryRouter
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


def test_episode_consolidates_bounded_trajectory_with_evidence(tmp_path):
    service = EpisodeMemoryService(tmp_path)
    events = [
        {
            "message_id": "operator-chat:1",
            "role": "operator",
            "text": "The server disk is nearly full; optimize before moving everything.",
            "created_at": "2026-09-18T10:00:00Z",
        },
        {
            "message_id": "operator-chat:2",
            "role": "ferro",
            "text": "I moved a rebuildable research workload to a low-trust worker.",
            "created_at": "2026-09-18T10:05:00Z",
        },
    ]
    result = service.consolidate(
        events,
        goal="Reduce pressure without moving the core identity",
        decision="Offload only rebuildable non-core work",
        actions=["migrate resource research worker"],
        outcome="Disk pressure reduced while the core remained local",
        lesson="Low-trust capacity is suitable for reconstructable workloads",
        topics=["survival-resources"],
    )
    memory = service.store.get_memory(result.memory_id)
    assert memory is not None
    assert memory["memory_type"] == "EPISODIC"
    assert memory["evidence_refs"] == ["operator-chat:1", "operator-chat:2"]
    assert memory["payload"]["decision"] == "Offload only rebuildable non-core work"
    assert memory["payload"]["derived_from"] == ["operator-chat:1", "operator-chat:2"]


def test_episode_is_recalled_for_why_and_how_query(tmp_path):
    service = EpisodeMemoryService(tmp_path)
    service.consolidate(
        [{
            "event_id": "event:1", "actor": "ferro",
            "text": "X login browser was not visible to the owner.",
            "created_at": "2026-09-19T01:00:00Z",
        }, {
            "event_id": "event:2", "actor": "ferro",
            "text": "Switched to a persistent account browser profile and verified the session.",
            "created_at": "2026-09-19T01:10:00Z",
        }],
        goal="Restore usable X login",
        outcome="Persistent browser session became reusable",
        lesson="Account-scoped persistent profiles are required for repeatable login",
        topics=["x-login"],
    )
    router = MemoryRouter(tmp_path, store=service.store)
    rows = router.recall(
        "X login 是怎么解决的",
        memory_types=["EPISODIC"],
        topic="x-login",
    )
    assert rows
    assert rows[0]["payload"]["outcome"] == "Persistent browser session became reusable"


def test_episode_requires_real_trajectory_identifiers(tmp_path):
    service = EpisodeMemoryService(tmp_path)
    try:
        service.consolidate([{"role": "operator", "text": "missing id"}])
    except ValueError as exc:
        assert "at least one trajectory event" in str(exc)
    else:
        raise AssertionError("episode without trajectory provenance must fail")
