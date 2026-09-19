from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.memory_consolidation import (
    MemoryConsolidationService, TrajectoryColdArchive, recall_tier, recall_weight,
)
from wechat_traffic_agent.memory_episode import EpisodeMemoryService


def test_recall_tiers_decay_without_deleting_memory():
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    hot = {"created_at": "2026-09-18T00:00:00Z", "importance": 0.7, "status": "ACTIVE"}
    warm = {"created_at": "2026-08-25T00:00:00Z", "importance": 0.7, "status": "ACTIVE"}
    cold = {"created_at": "2026-06-01T00:00:00Z", "importance": 0.7, "status": "ACTIVE"}
    archived = {"created_at": "2025-01-01T00:00:00Z", "importance": 0.7, "status": "ACTIVE"}
    assert recall_tier(hot, now=now) == "HOT"
    assert recall_tier(warm, now=now) == "WARM"
    assert recall_tier(cold, now=now) == "COLD"
    assert recall_tier(archived, now=now) == "ARCHIVED"
    assert recall_weight(hot, now=now) > recall_weight(archived, now=now)


def test_daily_and_long_term_consolidation_keep_derived_from(tmp_path):
    episodes = EpisodeMemoryService(tmp_path)
    ids = []
    for index, lesson in enumerate([
        "Offload replaceable work first.",
        "Keep identity state on reliable nodes.",
        "Verify recovery before moving more work.",
    ]):
        result = episodes.consolidate(
            [{"event_id": f"event:{index}", "actor": "ferro", "text": lesson,
              "created_at": f"2026-09-1{index+1}T00:00:00Z"}],
            goal="Reduce survival resource pressure",
            outcome="Pressure reduced",
            lesson=lesson,
            topics=["survival"],
        )
        ids.append(result.memory_id)
    service = MemoryConsolidationService(tmp_path, store=episodes.store)
    daily = service.consolidate_daily_episodes(ids[:2], source_ref="reflection:daily", topic="survival")
    assert daily is not None
    assert daily["payload"]["derived_from"] == ids[:2]
    long_term = service.consolidate_long_term_pattern(
        topic="survival", source_ref="reflection:long-term", minimum_episodes=3,
    )
    assert long_term is not None
    assert set(long_term["payload"]["derived_from"]) == set(ids)


def test_trajectory_cold_archive_is_lossless(tmp_path):
    source = tmp_path / "events.jsonl"
    original = b'{"event_id":"e1"}\n{"event_id":"e2"}\n'
    source.write_bytes(original)
    archive = TrajectoryColdArchive(tmp_path)
    path = archive.archive_jsonl(source)
    assert path.exists()
    assert source.exists()
    assert archive.read_archive(path) == original
    manifest = Path(str(path) + ".json")
    assert manifest.exists()
