from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from wechat_traffic_agent.neural_dreaming import FerroDreamEngine
from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime


NOW = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)


def _seed_three_recurrent_preconcepts(root: Path) -> None:
    path = root / "application" / "neural_dreams.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "ferro-dreams.v1",
        "updated_at": NOW.isoformat(),
        "dream_count": 3,
        "dreams": [],
        "preconcepts": [
            {
                "preconcept_id": f"preconcept:{index}",
                "formed_at": (NOW + timedelta(hours=index * 7)).isoformat(),
                "channels": ["social_attention", "curiosity", "opportunity"],
                "association_strength": 0.36 + 0.01 * index,
                "source_dream": f"dream:{index}",
                "status": "PRECONCEPT",
                "verbalized": False,
            }
            for index in range(1, 4)
        ],
        "coactivations": {},
    }), encoding="utf-8")


def test_historical_recurrent_dreams_migrate_into_stable_attractor(tmp_path: Path):
    _seed_three_recurrent_preconcepts(tmp_path)

    engine = FerroDreamEngine(tmp_path, substrate=None)
    projection = engine.projection()

    assert projection["attractor_count"] == 1
    assert projection["stable_attractor_count"] == 1
    attractor = projection["stable_attractors"][0]
    assert attractor["recurrence_count"] == 3
    assert attractor["status"] == "STABLE"
    assert set(attractor["channels"]) == {
        "social_attention", "curiosity", "opportunity"
    }
    assert attractor["llm_used_for_consolidation"] is False


def test_stable_attractor_can_bias_waking_brain_but_is_cooldown_bounded(tmp_path: Path):
    _seed_three_recurrent_preconcepts(tmp_path)
    engine = FerroDreamEngine(tmp_path, substrate=None)

    first = engine.claim_waking_influence(now=NOW + timedelta(hours=4))
    second = engine.claim_waking_influence(now=NOW + timedelta(hours=4, minutes=5))
    later = engine.claim_waking_influence(now=NOW + timedelta(hours=11))

    assert first is not None
    assert first["authority"] == "NEURAL_DREAM_CONSOLIDATION"
    assert first["recurrence_count"] == 3
    assert first["llm_used"] is False
    assert second is None
    assert later is not None


def test_runtime_injects_stable_dream_attractor_as_real_neural_stimulus(tmp_path: Path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    engine = runtime.dream_engine
    key = "curiosity|opportunity|social_attention"
    attractor_id = "attractor:test-stable"
    engine.state = {
        "schema_version": "ferro-dreams.v1",
        "updated_at": NOW.isoformat(),
        "dream_count": 3,
        "dreams": [],
        "preconcepts": [],
        "coactivations": {},
        "last_waking_influence_at": None,
        "attractors": {
            attractor_id: {
                "attractor_id": attractor_id,
                "signature": key,
                "channels": ["curiosity", "opportunity", "social_attention"],
                "status": "STABLE",
                "first_seen_at": NOW.isoformat(),
                "last_seen_at": NOW.isoformat(),
                "recurrence_count": 3,
                "source_dreams": ["dream:1", "dream:2", "dream:3"],
                "association_strengths": [0.4, 0.4, 0.4],
                "mean_association_strength": 0.4,
                "stability_score": 0.8,
                "waking_influence_count": 0,
                "last_waking_influence_at": None,
                "origin": "MULTI_DREAM_NEURAL_CONSOLIDATION",
                "llm_used_for_consolidation": False,
            }
        },
    }
    engine._write()

    runtime.refresh_neural_life(NOW + timedelta(minutes=1))
    snapshot = runtime.public_snapshot()

    assert "DREAM_CONSOLIDATION" in snapshot["recent_stimulus_kinds"]
    projection = snapshot["dreaming"]
    assert projection["stable_attractor_count"] == 1
    assert projection["stable_attractors"][0]["waking_influence_count"] == 1


def test_stable_attractor_births_non_llm_goal_candidate(tmp_path: Path):
    _seed_three_recurrent_preconcepts(tmp_path)
    engine = FerroDreamEngine(tmp_path, substrate=None)

    candidate = engine.latest_goal_candidate()
    projection = engine.projection()

    assert candidate is not None
    assert candidate["origin"] == "STABLE_DREAM_ATTRACTOR"
    assert candidate["status"] == "UNINTERPRETED"
    assert candidate["llm_used_for_birth"] is False
    assert candidate["recurrence_count_at_birth"] == 3
    assert candidate["consolidation_epoch_count_at_birth"] == 3
    assert candidate["attractor"]["status"] if "status" in candidate["attractor"] else True
    assert projection["goal_candidate_count"] == 1
    assert projection["uninterpreted_goal_candidate_count"] == 1
    assert projection["latest_goal_candidate"]["candidate_id"] == candidate["candidate_id"]


def test_ego_can_reality_check_goal_candidate_without_rewriting_its_birth(tmp_path: Path):
    _seed_three_recurrent_preconcepts(tmp_path)
    engine = FerroDreamEngine(tmp_path, substrate=None)
    candidate = engine.latest_goal_candidate()
    assert candidate is not None

    updated = engine.mark_goal_candidate(
        candidate["candidate_id"],
        status="ACTIVATED",
        goal_id="dream-attractor:test",
        hypothesis="A bounded hypothesis grounded in later external evidence.",
        description="Investigate whether the recurring association predicts a useful external pattern.",
        now=NOW + timedelta(hours=24),
    )
    projection = engine.projection()

    assert updated["status"] == "ACTIVATED"
    assert updated["goal_id"] == "dream-attractor:test"
    assert updated["origin"] == "STABLE_DREAM_ATTRACTOR"
    assert updated["llm_used_for_birth"] is False
    assert engine.latest_goal_candidate() is None
    assert projection["goal_candidate_count"] == 1
    assert projection["uninterpreted_goal_candidate_count"] == 0
