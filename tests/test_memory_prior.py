from __future__ import annotations

from wechat_traffic_agent.memory_prior import MemoryDecisionPrior
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


def _episode(store, memory_id, profile, index, reward, pageviews):
    store.append_memory({
        "memory_id": memory_id,
        "memory_type": "EPISODIC",
        "kind": "EPISODE",
        "text": f"Goal: preserve lived experience in content-audience | Outcome: {reward}",
        "created_at": f"2026-09-{10 + index:02d}T00:00:00Z",
        "status": "ACTIVE",
        "source_type": "TRAJECTORY_CONSOLIDATION",
        "source_ref": f"event:{memory_id}",
        "access_scope": "PRIVATE_OWNER",
        "topics": ["virtualbrain-life", "virtualbrain-life:content-audience"],
        "evidence_refs": [f"metric:{memory_id}"],
        "payload": {
            "outcome": str(reward),
            "observations": [{
                "text": f"Website strategy {profile} on topic crypto-market produced {pageviews} pageviews in the mature 24h window."
            }],
            "attributes": {"source": "website-audience-outcome", "domain": "content-audience"},
        },
    })


def test_memory_prior_comes_from_canonical_lived_outcomes(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _episode(store, "m:e1", "evidence-brief", 1, 0.9, 120)
    _episode(store, "m:e2", "evidence-brief", 2, 0.8, 110)
    _episode(store, "m:p1", "practical-guide", 3, 0.2, 20)
    _episode(store, "m:p2", "practical-guide", 4, 0.3, 30)
    prior = MemoryDecisionPrior(tmp_path, store=store).strategy_prior(topic="crypto-market")
    assert prior["causal_claim"] is False
    assert prior["profiles"]["evidence-brief"]["sample_count"] == 2
    assert prior["profiles"]["evidence-brief"]["score"] > prior["profiles"]["practical-guide"]["score"]
    assert set(prior["profiles"]["evidence-brief"]["memory_ids"]) == {"m:e1", "m:e2"}


def test_memory_prior_generalizes_when_new_topic_has_no_exact_lived_outcomes(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    _episode(store, "m:e1", "evidence-brief", 1, 0.9, 120)
    _episode(store, "m:e2", "evidence-brief", 2, 0.8, 110)
    _episode(store, "m:p1", "practical-guide", 3, 0.2, 20)
    _episode(store, "m:p2", "practical-guide", 4, 0.3, 30)

    prior = MemoryDecisionPrior(tmp_path, store=store).strategy_prior(
        topic="agent-reliability"
    )

    assert prior["transfer_scope"] == "GLOBAL_TRANSFER"
    assert prior["memory_ids"]
    assert prior["profiles"]["evidence-brief"]["sample_count"] == 2
    assert prior["profiles"]["evidence-brief"]["transfer_strength"] == 0.25
    assert (
        prior["profiles"]["evidence-brief"]["score"]
        > prior["profiles"]["practical-guide"]["score"]
    )
    assert prior["profiles"]["evidence-brief"]["score"] < 0.9
