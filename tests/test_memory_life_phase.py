from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from agent_runtime.clock import FrozenClock
from agent_runtime.runtime import AgentRuntime

from wechat_traffic_agent.content_strategy import PROFILES, choose_content_strategy
from wechat_traffic_agent.memory_consolidation import recall_tier, recall_weight
from wechat_traffic_agent.memory_evolution import MemoryEvolutionEngine
from wechat_traffic_agent.memory_evolution_bridge import (
    ALLOWED_CANDIDATE_TYPES,
    MemoryRuntimeEvolutionBridge,
)
from wechat_traffic_agent.memory_life import MemoryLifeRuntime
from wechat_traffic_agent.memory_observation import MemoryLifeObservatory
from wechat_traffic_agent.memory_prior import MemoryDecisionPrior
from wechat_traffic_agent.memory_prior_replay import replay_memory_prior_ab
from wechat_traffic_agent.memory_procedural import ProceduralMemoryService
from wechat_traffic_agent.memory_salience import MemorySaliencePolicy
from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.traffic_learning import TopicTrafficSignal


NOW = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)


def _traffic(topic: str) -> TopicTrafficSignal:
    return TopicTrafficSignal(
        topic=topic, article_count=0, orphan_article_count=0,
        search_rows=0, impressions=0, clicks=0, ctr=None, average_position=None,
        pageview_samples_24h=0, mean_pageviews_24h=None, trend_hits=0,
        demand_score=0.0, priority_score=0.0, exposure_status="UNKNOWN",
    )


def test_observatory_records_retrieval_failure_decision_and_outcome_metrics(tmp_path):
    obs = MemoryLifeObservatory(tmp_path)
    retrieval = obs.record_retrieval(
        query="what happened before migration",
        retrieved_memory_ids=["m:1", "m:2"],
        recall_latency_ms=12.5,
        source="test",
    )
    decision = obs.record_decision_use(
        query="choose strategy",
        retrieved_memory_ids=["m:1", "m:2"],
        used_memory_ids=["m:2"],
        decision_ref="strategy:abc",
        evidence_refs=["world:1"],
        metadata={"profile_name": "evidence-brief"},
    )
    obs.record_failure(
        "MISSED_RECALL",
        query="forgot old fact",
        retrieved_memory_ids=[],
        detail="expected durable fact was not retrieved",
        evidence_refs=["owner:1"],
    )
    obs.settle(
        decision["memory_use_id"],
        outcome_ref="outcome:1",
        outcome_success=True,
        memory_helped=True,
        evidence_ref="metric:1",
        detail="better mature outcome association",
    )
    metrics = obs.metrics()
    assert retrieval["recall_latency_ms"] == 12.5
    assert obs.decision_use("strategy:abc")["memory_use_id"] == decision["memory_use_id"]
    assert metrics["retrieval_count"] == 1
    assert metrics["decision_use_count"] == 1
    assert metrics["failure_counts"]["MISSED_RECALL"] == 1
    assert metrics["memory_helped_count"] == 1
    assert metrics["memory_help_rate"] == 1.0


def _add_audience_episode(life: MemoryLifeRuntime, index: int, *, profile: str, reward: float, pageviews: int):
    return life.observe_life_event({
        "event_id": f"content-strategy-outcome:{profile}:{index}",
        "summary": f"Website strategy {profile} on topic crypto-market produced {pageviews} pageviews in the mature 24h window.",
        "evidence_refs": [f"metric:{profile}:{index}", f"strategy:{profile}:{index}"],
        "source": "website-audience-outcome",
        "domain": "content-audience",
        "outcome": reward,
        "significance": 0.7,
        "confidence": 1.0,
    })


def test_memory_prior_is_derived_from_lived_outcomes_and_changes_same_input_decision(tmp_path):
    life = MemoryLifeRuntime(tmp_path)
    _add_audience_episode(life, 1, profile="evidence-brief", reward=0.9, pageviews=120)
    _add_audience_episode(life, 2, profile="evidence-brief", reward=0.8, pageviews=110)
    _add_audience_episode(life, 1, profile="practical-guide", reward=0.2, pageviews=20)
    _add_audience_episode(life, 2, profile="practical-guide", reward=0.3, pageviews=30)

    prior = MemoryDecisionPrior(tmp_path, store=life.store).strategy_prior(topic="crypto-market")
    assert prior["profiles"]["evidence-brief"]["sample_count"] == 2
    assert prior["profiles"]["practical-guide"]["sample_count"] == 2
    assert prior["profiles"]["evidence-brief"]["score"] > prior["profiles"]["practical-guide"]["score"]

    cycle = next(
        f"memory-prior-ab-{i}" for i in range(2000)
        if int(hashlib.sha256(f"memory-prior-ab-{i}|crypto-market".encode()).hexdigest()[:8], 16) % 100 >= 25
    )
    empty_context = {
        "topic": "crypto-market",
        "samples_by_profile": {p.name: 0 for p in PROFILES},
        "mean_relative_reward_by_profile": {p.name: None for p in PROFILES},
        "evidence_refs": [],
    }
    baseline = choose_content_strategy(
        state_root=tmp_path, account="https://example.test", cycle=cycle,
        topic="crypto-market", traffic_signal=_traffic("crypto-market"), as_of=NOW,
        mind_strategy_context=empty_context,
    )
    with_memory = choose_content_strategy(
        state_root=tmp_path, account="https://example.test", cycle=cycle,
        topic="crypto-market", traffic_signal=_traffic("crypto-market"), as_of=NOW,
        mind_strategy_context={**empty_context, "memory_prior": prior},
    )
    assert with_memory.profile.name == "evidence-brief"
    assert with_memory.memory_prior_applied is True
    assert with_memory.memory_prior_memory_ids
    assert "bounded strategy prior" in with_memory.decision_reason
    assert baseline.profile.name != with_memory.profile.name or baseline.memory_prior_applied is False
    events_before_replay = MemoryLifeObservatory(tmp_path).metrics()["event_count"]
    replay = replay_memory_prior_ab(
        state_root=tmp_path,
        account="https://example.test",
        cycle=cycle,
        topic="crypto-market",
        traffic_signal=_traffic("crypto-market"),
        as_of=NOW,
        mind_strategy_context={**empty_context, "memory_prior": prior},
    )
    assert MemoryLifeObservatory(tmp_path).metrics()["event_count"] == events_before_replay
    assert replay["baseline_profile"] == baseline.profile.name
    assert replay["memory_assisted_profile"] == with_memory.profile.name
    assert replay["causal_claim"] is False
    decision = MemoryLifeObservatory(tmp_path).decision_use(with_memory.strategy_id)
    assert decision is not None
    assert set(decision["used_memory_ids"]) == set(with_memory.memory_prior_memory_ids)


def test_neural_salience_changes_memory_strength_not_fact_authority(tmp_path):
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
    assert high["consolidation_priority"] > low["consolidation_priority"]
    assert high["decay_rate"] < low["decay_rate"]
    assert high["recall_boost"] > low["recall_boost"]
    assert high_event == {
        "significance": 0.9, "prediction_error": 0.9,
        "identity_relevance": 0.8, "emotional_intensity": 0.7,
    }

    created = "2026-08-01T00:00:00Z"
    high_memory = {
        "created_at": created, "importance": 0.7, "status": "ACTIVE",
        "payload": {"attributes": {"decay_rate": 0.2, "recall_boost": 1.45}},
    }
    low_memory = {
        "created_at": created, "importance": 0.7, "status": "ACTIVE",
        "payload": {"attributes": {"decay_rate": 1.25, "recall_boost": 0.9}},
    }
    assert recall_tier(high_memory, now=NOW) == "WARM"
    assert recall_tier(low_memory, now=NOW) == "COLD"
    assert recall_weight(high_memory, now=NOW) > recall_weight(low_memory, now=NOW)


def test_procedure_variants_promote_mature_better_path_and_preserve_history(tmp_path):
    service = ProceduralMemoryService(tmp_path)
    current = service.learn_procedure(
        name="verify-page",
        description="Verify a live page.",
        preconditions=["owner read-only"],
        capabilities=["web.fetch"],
        steps=["search", "fetch", "verify"],
        verification=["receipt exists"],
        source_type="VERIFIED_EXECUTION_OUTCOME",
        source_ref="receipt:baseline",
        evidence_refs=["receipt:baseline"],
        success_count=1,
        failure_count=2,
        last_outcome="FAILURE",
    )
    baseline_id = current["payload"]["default_variant_id"]
    better_steps = ["fetch full url", "verify receipt"]
    for index in range(3):
        revised = service.record_outcome(
            current["memory_id"],
            success=True,
            evidence_ref=f"receipt:better:{index}",
            variant_steps=better_steps,
            observed_cost=0.1,
            latency_ms=100.0,
        )
        assert service.store.get_memory(current["memory_id"])["status"] == "SUPERSEDED"
        current = revised

    payload = current["payload"]
    assert payload["procedure_schema"] == 2
    assert payload["default_variant_id"] != baseline_id
    assert payload["variant_policy_status"] == "PROMOTED_BETTER_VARIANT"
    selected = service.select_variant(
        current["memory_id"],
        context={"satisfied_preconditions": ["owner read-only"]},
    )
    assert selected["steps"] == better_steps
    assert selected["execution_count"] == 3
    assert selected["success_count"] == 3


def _seed_benchmark_memories(root):
    store = CanonicalMemoryStore(root)
    for index in range(12):
        store.append_memory({
            "memory_id": f"m:{index}",
            "memory_type": "SEMANTIC",
            "kind": "OWNER_PREFERENCE",
            "text": f"shared durable project preference unique-token-{index}",
            "created_at": f"2026-09-{index + 1:02d}T00:00:00Z",
            "status": "ACTIVE",
            "source_type": "OWNER_CONVERSATION",
            "source_ref": f"owner:{index}",
            "access_scope": "PRIVATE_OWNER",
            "importance": 0.5,
            "confidence": 1.0,
        })
    policy = store.root / "active_recall_policy.json"
    policy.write_text(json.dumps({
        "weights": {
            "rrf_k": 60.0,
            "lexical_weight": 0.0,
            "vector_weight": 0.0,
            "structured_weight": 0.0,
            "importance_weight": 0.0,
            "confidence_weight": 0.0,
            "temporal_boost": 0.0,
            "search_multiplier": 8,
            "decay_enabled": True,
        }
    }), encoding="utf-8")
    return store


def test_procedure_failures_route_to_verified_prospective_policy_not_fake_replay(tmp_path):
    runtime_root = tmp_path / "runtime-procedure-policy"
    state_root = tmp_path / "state-procedure-policy"
    rt = AgentRuntime.bootstrap(runtime_root, clock=FrozenClock(NOW))
    engine = MemoryEvolutionEngine(state_root)
    engine.observe_signal(
        "PROCEDURE_FAILURE",
        evidence_ref="receipt:procedure:failure:1",
        detail="bounded owner tool invocation failed once",
    )
    engine.observe_signal(
        "PROCEDURE_FAILURE",
        evidence_ref="receipt:procedure:failure:2",
        detail="bounded owner tool invocation failed again",
    )

    result = MemoryRuntimeEvolutionBridge(runtime=rt, state_root=state_root).sync()
    skill = next(
        row for row in result["candidates"]
        if row.get("candidate_type") == "SKILL_VARIANT_PROMOTION"
    )
    assert skill["status"] == "PROSPECTIVE_POLICY_OWNS_EVALUATION"
    assert skill["evaluation_mode"] == "VERIFIED_PROSPECTIVE_EXECUTION"
    assert skill["replay_required"] is False
    assert skill["minimum_samples_per_variant"] == 3


def test_memory_failures_enter_total_evolution_engine_only_after_replay_improves(tmp_path):
    runtime_root = tmp_path / "runtime"
    state_root = tmp_path / "state"
    rt = AgentRuntime.bootstrap(runtime_root, clock=FrozenClock(NOW))
    _seed_benchmark_memories(state_root)
    memory_evolution = MemoryEvolutionEngine(state_root)
    first = memory_evolution.observe_signal(
        "RECALL_FAILURE", evidence_ref="owner:failure:1",
        detail="older durable unique preference was missed",
    )
    second = memory_evolution.observe_signal(
        "RECALL_FAILURE", evidence_ref="owner:failure:2",
        detail="another durable preference was missed",
    )
    bridge = MemoryRuntimeEvolutionBridge(runtime=rt, state_root=state_root)
    result = bridge.sync()
    recall = next(
        row for row in result["candidates"]
        if row.get("candidate_type") == "RECALL_WEIGHT_CHANGE"
    )
    assert recall["stage"] == "SHADOW"
    assert recall["replay"]["challenger_score"] > recall["replay"]["baseline_score"]
    candidate = next(row for row in rt.evolution_candidates if row.candidate_id == recall["candidate_id"])
    assert candidate.target.startswith("memory.recall.policy:")
    assert candidate.mutation_layer.value == "RETRIEVAL"
    assert "RECALL_WEIGHT_CHANGE" in ALLOWED_CANDIDATE_TYPES
    projected_refs = {row["source_ref"] for row in result["projected_failures"]}
    assert {first["signal_id"], second["signal_id"]}.issubset(projected_refs)

    # New memories formed after the frozen prospective start become genuine future trial data.
    rt.clock.advance(120)
    store = CanonicalMemoryStore(state_root)
    for index, seconds in enumerate((90, 100, 110)):
        created = (NOW + __import__("datetime").timedelta(seconds=seconds)).isoformat()
        store.append_memory({
            "memory_id": f"m:prospective:{index}",
            "memory_type": "SEMANTIC",
            "kind": "OWNER_PREFERENCE",
            "text": f"prospective unique memory quartz-signal-{index} says prefer bounded verification {index}",
            "created_at": created,
            "status": "ACTIVE",
            "source_type": "OWNER_CONVERSATION",
            "source_ref": f"owner:prospective:{index}",
            "access_scope": "PRIVATE_OWNER",
            "importance": 0.7,
            "confidence": 1.0,
        })
    second_sync = bridge.sync()
    advanced = next(
        row for row in second_sync["candidates"]
        if row.get("candidate_type") == "RECALL_WEIGHT_CHANGE"
    )
    assert advanced["stage"] == "PROSPECTIVE_TRIAL"
    assert advanced["prospective"]["baseline_samples"] >= 2
    assert advanced["prospective"]["candidate_samples"] >= 2
    assert advanced["prospective"]["result"] in {
        "IMPROVEMENT_OBSERVED", "NO_IMPROVEMENT", "REGRESSION_OBSERVED"
    }
    if advanced["prospective"]["result"] == "IMPROVEMENT_OBSERVED":
        assert advanced["status"] == "PROMOTED"
        policy = json.loads(
            (state_root / "application" / "memory_system" / "active_recall_policy.json").read_text()
        )
        assert policy["authority"] == "agent-runtime-evolution"
        assert policy["capability_version_id"] == advanced["capability_version_id"]


def test_runtime_evolution_is_only_recall_policy_promotion_authority_and_can_rollback(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime-promotion"
    state_root = tmp_path / "state-promotion"
    rt = AgentRuntime.bootstrap(runtime_root, clock=FrozenClock(NOW))
    _seed_benchmark_memories(state_root)
    engine = MemoryEvolutionEngine(state_root)
    engine.observe_signal("RECALL_FAILURE", evidence_ref="owner:promotion:1", detail="miss one")
    engine.observe_signal("RECALL_FAILURE", evidence_ref="owner:promotion:2", detail="miss two")
    bridge = MemoryRuntimeEvolutionBridge(runtime=rt, state_root=state_root)
    first = bridge.sync()
    recall = next(row for row in first["candidates"] if row["candidate_type"] == "RECALL_WEIGHT_CHANGE")
    assert recall["stage"] == "SHADOW"
    assert recall["awe_experiment_id"]
    awe_before = bridge.awe_hillclimb.optimizer_view(recall["awe_experiment_id"])
    assert awe_before["status"] == "AWAITING_AWE"
    assert awe_before["evaluator_receipt"] is None

    # Force a deterministic prospective quality difference while still using real Runtime
    # experiment/evidence/sample/promotion machinery.
    def trial_metrics(row, *, weights, now):
        better = float(weights.vector_weight) > 0.0 or float(weights.lexical_weight) > 0.0
        return {
            "memory_recall_quality": 1.0 if better else 0.2,
            "memory_exact_recall": 1.0,
        }
    monkeypatch.setattr(bridge, "_memory_trial_metrics", trial_metrics)

    rt.clock.advance(120)
    store = CanonicalMemoryStore(state_root)
    for index, seconds in enumerate((90, 100, 110)):
        store.append_memory({
            "memory_id": f"m:promotion:{index}",
            "memory_type": "SEMANTIC",
            "kind": "OWNER_PREFERENCE",
            "text": f"prospective promotion memory {index} with unique evidence token",
            "created_at": (NOW + __import__("datetime").timedelta(seconds=seconds)).isoformat(),
            "status": "ACTIVE",
            "source_type": "OWNER_CONVERSATION",
            "source_ref": f"owner:promotion:new:{index}",
            "access_scope": "PRIVATE_OWNER",
            "importance": 0.7,
            "confidence": 1.0,
        })
    second = bridge.sync()
    promoted = next(row for row in second["candidates"] if row["candidate_type"] == "RECALL_WEIGHT_CHANGE")
    assert promoted["status"] == "PROMOTED"
    assert promoted["prospective"]["result"] == "IMPROVEMENT_OBSERVED"
    assert promoted["awe_gate"]["status"] == "PROMOTION_APPROVED"
    awe_after = bridge.awe_hillclimb.optimizer_view(promoted["awe_experiment_id"])
    assert (
        awe_after["evaluator_receipt"]["train"]["candidate_mean"]
        > awe_after["evaluator_receipt"]["train"]["baseline_mean"]
    )
    assert awe_after["evaluator_receipt"]["hidden"]["n_baseline"] >= 2
    assert awe_after["evaluator_receipt"]["hidden"]["n_candidate"] >= 2
    assert "memory_id" not in str(awe_after["evaluator_receipt"]["hidden"])
    policy_path = state_root / "application" / "memory_system" / "active_recall_policy.json"
    policy = json.loads(policy_path.read_text())
    assert policy["authority"] == "agent-runtime-evolution"
    assert policy["status"] == "PROMOTED"
    active = rt.evolution_capability_registry.active("memory.recall.policy")
    assert active.version_id == promoted["capability_version_id"]
    assert active.rollback_target is not None

    # Two measured post-promotion recall failures trigger Runtime rollback first,
    # then materialize the restored baseline policy.
    promoted_at = __import__("datetime").datetime.fromisoformat(promoted["promoted_at"])
    later1 = (promoted_at + __import__("datetime").timedelta(seconds=10)).isoformat()
    later2 = (promoted_at + __import__("datetime").timedelta(seconds=20)).isoformat()
    monkeypatch.setattr(bridge, "_sources", lambda: [
        {
            "source_ref": "memory-use:post-promotion-failure-1",
            "kind": "MISSED_RECALL",
            "detail": "miss after promotion one",
            "observed_at": later1,
            "evidence_refs": ["owner:post:1"],
        },
        {
            "source_ref": "memory-use:post-promotion-failure-2",
            "kind": "MISSED_RECALL",
            "detail": "miss after promotion two",
            "observed_at": later2,
            "evidence_refs": ["owner:post:2"],
        },
    ])
    rolled = bridge._maybe_rollback_recall_policy(dict(promoted))
    assert rolled["status"] == "ROLLED_BACK"
    restored = json.loads(policy_path.read_text())
    assert restored["status"] == "ROLLED_BACK"
    assert restored["weights"] == promoted["baseline_weights"]
    active_after = rt.evolution_capability_registry.active("memory.recall.policy")
    assert active_after.version_id != promoted["capability_version_id"]
