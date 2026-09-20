from __future__ import annotations

from wechat_traffic_agent.memory_consolidation import MemoryConsolidationService
from wechat_traffic_agent.memory_episode import EpisodeMemoryService


def _episode(service, index, lesson):
    result = service.consolidate(
        [{
            "event_id": f"event:{index}",
            "actor": "ferro",
            "text": f"Observed outcome {index}",
            "created_at": f"2026-09-20T00:0{index}:00Z",
        }],
        goal="owner memory integration",
        outcome=f"outcome {index}",
        lesson=lesson,
        topics=["owner-private-chat"],
    )
    return result.memory_id


def test_daily_consolidation_revises_prior_summary_without_erasing_history(tmp_path):
    episodes = EpisodeMemoryService(tmp_path)
    first_id = _episode(episodes, 1, "First verified lesson")
    second_id = _episode(episodes, 2, "Second verified lesson")
    consolidation = MemoryConsolidationService(tmp_path, store=episodes.store)
    first = consolidation.consolidate_daily_episodes(
        [first_id, second_id], source_ref=second_id, topic="owner-private-chat"
    )
    assert first is not None

    third_id = _episode(episodes, 3, "Third verified lesson")
    second = consolidation.consolidate_daily_episodes(
        [first_id, second_id, third_id], source_ref=third_id, topic="owner-private-chat"
    )
    assert second is not None
    assert second["memory_id"] != first["memory_id"]
    current = episodes.store.get_memory(second["memory_id"])
    prior = episodes.store.get_memory(first["memory_id"])
    assert current["status"] == "ACTIVE"
    assert current["supersedes"] == first["memory_id"]
    assert prior["status"] == "SUPERSEDED"
    assert prior["superseded_by"] == second["memory_id"]
