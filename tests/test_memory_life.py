from __future__ import annotations

from wechat_traffic_agent.memory_life import MemoryLifeRuntime


def test_repeated_real_process_outcomes_become_episode_procedure_relation_and_knowledge(tmp_path):
    life = MemoryLifeRuntime(tmp_path)
    results = []
    for index in range(1, 11):
        results.append(life.observe_process_outcome(
            process_id=f"cycle:{index}",
            topic="resource-research",
            action="fetch-and-verify-resource-evidence",
            success=index != 4,
            outcome="verified evidence persisted" if index != 4 else "provider timeout",
            evidence_refs=[f"receipt:{index}"],
            capabilities=["web.fetch"],
            steps=["fetch public evidence", "verify receipt", "persist evidence"],
            verification=["receipt_id exists", "evidence persisted"],
            failure_mode="provider_timeout" if index == 4 else "",
            recovery_step="retry with bounded fallback" if index == 4 else "",
        ))

    episodes = life.store.query_memories(
        memory_types=["EPISODIC"], statuses=["ACTIVE"], topic="resource-research", limit=20
    )
    assert len(episodes) == 10

    procedures = life.store.query_memories(
        memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=20
    )
    assert len(procedures) == 1
    payload = procedures[0]["payload"]
    assert payload["execution_count"] == 10
    assert payload["success_count"] == 9
    assert payload["failure_count"] == 1
    assert "provider_timeout" in payload["failure_modes"]

    relations = life.store.query_memories(
        memory_types=["RELATIONAL"], statuses=["ACTIVE"], limit=20
    )
    assert any((row.get("payload") or {}).get("object") == "web.fetch" for row in relations)

    assert results[2]["daily_knowledge_id"]
    assert any(result["knowledge_memory_ids"] for result in results[1:])
    assert results[-1]["long_term_knowledge_id"]
    long_term = life.store.get_memory(results[-1]["long_term_knowledge_id"])
    assert long_term is not None
    assert long_term["kind"] == "LONG_TERM_PATTERN"
    assert len(long_term["evidence_refs"]) == 10
    assert results[-1]["continuity_checkpoint"] is not None
    assert results[-1]["continuity_checkpoint"]["sequence"] >= 1


def test_process_outcome_replay_is_idempotent(tmp_path):
    life = MemoryLifeRuntime(tmp_path)
    kwargs = dict(
        process_id="cycle:stable",
        topic="content-learning",
        action="content-create-verify-publish",
        success=True,
        outcome="PUBLISHED",
        evidence_refs=["cycle:stable", "draft:abc"],
        capabilities=["content.generate"],
    )
    first = life.observe_process_outcome(**kwargs)
    second = life.observe_process_outcome(**kwargs)
    assert second["status"] == "ALREADY_RECORDED"
    assert second["episode_id"] == first["episode_id"]
    procedures = life.store.query_memories(memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=10)
    assert len(procedures) == 1
    assert procedures[0]["payload"]["execution_count"] == 1


def test_virtualbrain_life_events_form_idempotent_noncausal_episodes_and_knowledge(tmp_path):
    life = MemoryLifeRuntime(tmp_path)
    first = life.observe_life_event({
        "event_id": "prediction-outcome:p1",
        "summary": "Dated prediction p1 was refuted by observed funding evidence.",
        "evidence_refs": ["prediction:p1", "world:funding:1"],
        "source": "prospective-world-outcome",
        "domain": "crypto-finance-world",
        "outcome": 0.0,
        "prediction_error": 0.8,
        "confidence": 0.9,
        "significance": 0.9,
    })
    assert first["status"] == "RECORDED"
    episode = life.store.get_memory(first["episode_id"])
    assert episode is not None
    assert episode["payload"]["attributes"]["experience_kind"] == "VIRTUALBRAIN_AUTOBIOGRAPHICAL_EVENT"
    assert episode["payload"]["attributes"]["causal_claim"] is False
    assert "world:funding:1" in episode["evidence_refs"]

    duplicate = life.observe_life_event({
        "event_id": "prediction-outcome:p1",
        "summary": "Dated prediction p1 was refuted by observed funding evidence.",
        "evidence_refs": ["prediction:p1", "world:funding:1"],
        "source": "prospective-world-outcome",
        "domain": "crypto-finance-world",
    })
    assert duplicate["status"] == "ALREADY_RECORDED"
    assert duplicate["episode_id"] == first["episode_id"]

    second = life.observe_life_event({
        "event_id": "prediction-outcome:p2",
        "summary": "Dated prediction p2 was confirmed by later liquidity evidence.",
        "evidence_refs": ["prediction:p2", "world:liquidity:2"],
        "source": "prospective-world-outcome",
        "domain": "crypto-finance-world",
        "outcome": 1.0,
        "prediction_error": 0.1,
        "confidence": 0.85,
        "significance": 0.8,
    })
    assert second["knowledge_memory_ids"]
    knowledge = life.store.get_memory(second["knowledge_memory_ids"][0])
    assert knowledge is not None
    assert knowledge["kind"] == "EXPERIENCE_KNOWLEDGE"
    assert "calibrated against observed evidence" in knowledge["text"]


def test_virtualbrain_life_history_backfill_is_bounded_and_idempotent(tmp_path):
    life = MemoryLifeRuntime(tmp_path)
    events = [{
        "event_id": f"life:{index}",
        "summary": f"Observed lived event {index}.",
        "evidence_refs": [f"evidence:{index}"],
        "source": "content-process",
        "domain": "content-process",
        "significance": 0.5,
    } for index in range(5)]
    first = life.backfill_life_history(events, limit=3)
    assert first["status"] == "OK"
    assert first["scanned"] == 3
    assert first["recorded"] == 3
    assert first["already_recorded"] == 0
    second = life.backfill_life_history(events, limit=3)
    assert second["recorded"] == 0
    assert second["already_recorded"] == 3
    episodes = life.store.query_memories(
        memory_types=["EPISODIC"], statuses=["ACTIVE"], topic="virtualbrain-life", limit=20
    )
    assert len(episodes) == 3
