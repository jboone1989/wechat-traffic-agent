from __future__ import annotations

from datetime import datetime, timezone

from wechat_traffic_agent.memory_consolidation import recall_tier, recall_weight
from wechat_traffic_agent.memory_salience import MemorySaliencePolicy


def test_neural_salience_changes_strength_only_and_slows_decay(tmp_path):
    policy = MemorySaliencePolicy(tmp_path)
    low_event = {
        "significance": 0.1, "prediction_error": 0.0,
        "identity_relevance": 0.0, "emotional_intensity": 0.0,
    }
    high_event = {
        "significance": 0.9, "prediction_error": 0.9,
        "identity_relevance": 0.8, "emotional_intensity": 0.7,
    }
    neural = {
        "affect": {"arousal": 0.9, "curiosity": 0.8, "frustration": 0.7, "resource_hunger": 0.8},
        "body": {"cpu_pressure": 0.8, "memory_pressure": 0.7, "disk_pressure": 0.7},
        "drives": {"survival": 0.85},
    }
    low = policy.score(low_event, neural_life={})
    high = policy.score(high_event, neural_life=neural)
    assert high["salience"] > low["salience"]
    assert high["decay_rate"] < low["decay_rate"]
    assert high["recall_boost"] > low["recall_boost"]
    # Policy must not mutate the event/factual payload.
    assert high_event["prediction_error"] == 0.9

    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    high_memory = {
        "created_at": "2026-08-01T00:00:00Z", "importance": 0.7, "status": "ACTIVE",
        "payload": {"attributes": {"decay_rate": 0.2, "recall_boost": 1.45}},
    }
    low_memory = {
        "created_at": "2026-08-01T00:00:00Z", "importance": 0.7, "status": "ACTIVE",
        "payload": {"attributes": {"decay_rate": 1.25, "recall_boost": 0.9}},
    }
    assert recall_tier(high_memory, now=now) == "WARM"
    assert recall_tier(low_memory, now=now) == "COLD"
    assert recall_weight(high_memory, now=now) > recall_weight(low_memory, now=now)
