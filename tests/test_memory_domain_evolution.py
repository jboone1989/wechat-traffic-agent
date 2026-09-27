from datetime import datetime, timedelta, timezone
import json

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.runtime import AgentRuntime
from wechat_traffic_agent.memory_domain_evolution import DomainEvaluator, MemoryDomainEvolution, aware
from wechat_traffic_agent.memory_domain_policy import DEFAULTS, DOMAIN_SPECS, load_policies, validate_policy
from wechat_traffic_agent.memory_evolution_bridge import MemoryRuntimeEvolutionBridge
from wechat_traffic_agent.memory_evolution import MemoryEvolutionEngine
from wechat_traffic_agent.memory_observation import MemoryLifeObservatory
from wechat_traffic_agent.memory_retrieval import MemoryRouter, RecallWeights
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


NOW = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)
KINDS = list(DOMAIN_SPECS)


def memory(store, name, text, *, age=1, importance=0.5, boost=1.0):
    return store.append_memory({
        "memory_id": name, "memory_type": "SEMANTIC", "kind": "VERIFIED_LESSON",
        "text": text, "created_at": (NOW - timedelta(days=age)).isoformat(),
        "status": "ACTIVE", "source_type": "VERIFIED_EXECUTION_OUTCOME",
        "source_ref": "receipt:" + name, "evidence_refs": ["receipt:" + name],
        "access_scope": "PRIVATE_OWNER", "importance": importance, "confidence": 1.0,
        "payload": {"attributes": {"recall_boost": boost}},
    })


def fixture(root, domain):
    store = CanonicalMemoryStore(root)
    if domain == "index":
        memory(store, "target", "quartz project calibration unique target")
        for index in range(75):
            memory(store, f"noise:{index}", f"quartz project distractor noise {index}", importance=0.2)
        query = "quartz calibration"
    elif domain == "decay":
        memory(store, "target", "quartz project calibration unique target", age=9, importance=0.8)
        memory(store, "noise", "quartz project distractor noise", importance=0.5)
        query = "quartz project"
    else:
        memory(store, "target", "quartz project calibration unique target", boost=1.2, importance=0.05)
        memory(store, "noise", "quartz project distractor noise", boost=1.0, importance=0.9)
        query = "quartz project"
    return store, query


def settled(root, query, index, at, *, expected="target", outcome=None, helped=True):
    obs = MemoryLifeObservatory(root)
    router = MemoryRouter(root)
    router.recall(query, now=at, access_scope="PRIVATE_OWNER")
    use = obs.record_decision_use(query=query, retrieved_memory_ids=["noise", expected],
                                  used_memory_ids=[expected], decision_ref=f"decision:{index}",
                                  metadata={"domain_policy_refs": router.last_domain_policy_refs})
    result = obs.settle(use["memory_use_id"], outcome_ref=outcome or f"outcome:{index}",
                       outcome_success=helped, memory_helped=helped, evidence_ref=f"world:{index}")
    # Controlled event clock only; evaluator, Router and Runtime are real implementations.
    rows = obs.rows(limit=10000)
    for row in rows:
        if row["memory_use_id"] == use["memory_use_id"]:
            row["observed_at"] = at.isoformat()
        if row["memory_use_id"] == result["memory_use_id"]:
            row["observed_at"] = (at + timedelta(seconds=1)).isoformat()
    obs.events_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def contradict(root, use_id, outcome, at):
    obs = MemoryLifeObservatory(root)
    result = obs.settle(
        use_id,
        outcome_ref=outcome,
        outcome_success=False,
        memory_helped=False,
        evidence_ref="world:counter:" + outcome,
    )
    rows = obs.rows(limit=10000)
    for row in rows:
        if row["memory_use_id"] == result["memory_use_id"]:
            row["observed_at"] = at.isoformat()
    obs.events_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def setup(tmp_path, domain):
    root = tmp_path / "state"
    store, query = fixture(root, domain)
    runtime = AgentRuntime.bootstrap(tmp_path / "runtime", clock=FrozenClock(NOW))
    for index in range(4):
        settled(root, query, index, NOW - timedelta(seconds=60 - index * 5))
    runtime = AgentRuntime.restore(tmp_path / "runtime", clock=runtime.clock)
    engine = MemoryDomainEvolution(runtime=runtime, state_root=root)
    kind = next(kind for kind, (name, _) in DOMAIN_SPECS.items() if name == domain)
    sources = {kind: [{"evidence_refs": ["failure:one"]}, {"evidence_refs": ["failure:two"]}]}
    return root, store, query, runtime, engine, kind, sources


@pytest.mark.parametrize("domain", ["decay", "index", "salience"])
def test_real_replay_prospective_promotion_and_live_reload(tmp_path, domain):
    root, store, query, runtime, engine, kind, sources = setup(tmp_path, domain)
    router = MemoryRouter(root)
    before = router.recall(query, limit=5, now=NOW, access_scope="PRIVATE_OWNER")
    before_cost = router.last_recall_metrics["candidate_cost"]
    canonical = store.list_memories(limit=None), store.events()
    summary = next(row for row in engine.sync(sources) if row["candidate_type"] == kind)
    state = engine._load(domain)
    assert summary["status"] == "WAITING_PROSPECTIVE_START", state
    assert state["replay"]["passed"] is True
    assert load_policies(root)[domain] == DEFAULTS[domain]
    assert (store.list_memories(limit=None), store.events()) == canonical
    runtime.clock.advance(180)
    for index in range(4):
        settled(root, query, index + 10, NOW + timedelta(seconds=90 + index * 5))
    # Restore Runtime and adapter to exercise durable pending experiments.
    runtime = AgentRuntime.restore(tmp_path / "runtime", clock=runtime.clock)
    engine = MemoryDomainEvolution(runtime=runtime, state_root=root)
    summary = next(row for row in engine.sync(sources) if row["candidate_type"] == kind)
    state = engine._load(domain)
    assert summary["status"] == "PROMOTED", state
    assert summary["prospective"]["baseline_samples"] == 4
    assert summary["prospective"]["candidate_samples"] == 4
    assert load_policies(root)[domain] == state["challenger"]
    after = router.recall(query, limit=5, now=NOW, access_scope="PRIVATE_OWNER")
    if domain == "index":
        assert router.last_recall_metrics["candidate_cost"] < before_cost
    else:
        assert [row["memory_id"] for row in after].index("target") < [row["memory_id"] for row in before].index("target")
    engine.sync(sources)
    experiment = runtime.evolution_experiment_manager.get(state["experiment_id"])
    assert len(experiment.samples) == 8
    assert experiment.stage.value == "CLOSED"
    assert len(runtime.evolution_capability_activations) == 1
    # Real negative outcomes bind to the activated policy, with no mocked evaluator.
    runtime.clock.advance(120)
    settled(root, query, 30, NOW + timedelta(seconds=200), helped=False)
    engine.sync(sources)
    assert engine._load(domain)["status"] == "PROMOTED"
    observations = engine.observatory.rows(limit=10000)
    first_harmful_use = next(
        row["memory_use_id"] for row in observations
        if row.get("decision_ref") == "decision:30"
    )
    contradict(
        root,
        first_harmful_use,
        "second-outcome-same-use",
        NOW + timedelta(seconds=205),
    )
    engine.sync(sources)
    assert engine._load(domain)["status"] == "PROMOTED"
    settled(root, query, 31, NOW + timedelta(seconds=210), helped=False)
    engine.sync(sources)
    assert engine._load(domain)["status"] == "ROLLED_BACK"
    assert len(engine._load(domain)["rollback_failure_use_ids"]) == 2
    assert load_policies(root)[domain] == DEFAULTS[domain]
    assert len(runtime.evolution_capability_rollbacks) == 1
    engine.sync(sources)
    assert len(runtime.evolution_capability_rollbacks) == 1


@pytest.mark.parametrize("domain", ["decay", "index", "salience"])
def test_evidence_dedupe_expiry_and_no_future_leakage(tmp_path, domain):
    root, _, query, runtime, engine, kind, sources = setup(tmp_path, domain)
    engine.sync(sources)
    state = engine._load(domain)
    assert state["status"] == "WAITING_PROSPECTIVE_START", state
    runtime.clock.advance(180)
    # Repeated settlement of one outcome cannot fill an arm's threshold.
    for index in range(4):
        settled(root, query, index + 10, NOW + timedelta(seconds=90 + index), outcome="one-outcome")
    engine.sync(sources)
    assert engine._load(domain)["prospective"]["baseline_samples"] == 1
    runtime.clock.advance(8 * 86400)
    engine.sync(sources)
    assert engine._load(domain)["status"] == "EXPIRED_INSUFFICIENT_EVIDENCE"
    assert load_policies(root)[domain] == DEFAULTS[domain]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 100, True, "1"])
def test_policy_bounds_fail_closed(bad):
    with pytest.raises(ValueError):
        validate_policy("decay", {"age_multiplier": bad})


def test_unsettled_and_ambiguous_uses_never_become_labels(tmp_path):
    runtime = AgentRuntime.bootstrap(tmp_path / "runtime", clock=FrozenClock(NOW))
    engine = MemoryDomainEvolution(runtime=runtime, state_root=tmp_path / "state")
    obs = engine.observatory
    use = obs.record_retrieval(query="quartz", retrieved_memory_ids=["target"], recall_latency_ms=1, source="owner")
    obs.settle(use["memory_use_id"], outcome_ref="outcome", outcome_success=True,
               memory_helped=True, evidence_ref="world")
    assert engine.cases(NOW + timedelta(days=1)) == []
    result = engine.sync({})
    assert len(result) == 3
    assert all(row["status"] == "INSUFFICIENT_DOMAIN_EVIDENCE" for row in result)
    assert not runtime.evolution_candidates


def test_bridge_routes_existing_stale_index_and_salience_signals(tmp_path):
    runtime = AgentRuntime.bootstrap(tmp_path / "runtime", clock=FrozenClock(NOW))
    root = tmp_path / "state"
    memory_engine = MemoryEvolutionEngine(root)
    for signal in ("STALE_MEMORY", "INDEX_COST", "SALIENCE_MISMATCH"):
        for index in range(2):
            memory_engine.observe_signal(signal, evidence_ref=f"failure:{signal}:{index}", detail="measured failure")
    report = MemoryRuntimeEvolutionBridge(runtime=runtime, state_root=root).sync()
    rows = [row for row in report["candidates"] if row["candidate_type"] in KINDS]
    assert [row["candidate_type"] for row in rows] == KINDS
    assert all(row["evaluator"] and row["unique_failure_evidence"] == 2 for row in rows)
    assert all(row["status"] == "INSUFFICIENT_DOMAIN_EVIDENCE" for row in rows)


@pytest.mark.parametrize("domain", ["decay", "index", "salience"])
def test_runtime_activation_and_rollback_survive_materialization_crash(tmp_path, monkeypatch, domain):
    root, _, query, runtime, engine, kind, sources = setup(tmp_path, domain)
    engine.sync(sources)
    runtime.clock.advance(180)
    for index in range(4):
        settled(root, query, index + 10, NOW + timedelta(seconds=90 + index * 5))
    def crash(*args, **kwargs):
        raise OSError("simulated disk interruption")
    monkeypatch.setattr(engine, "_materialize", crash)
    with pytest.raises(OSError):
        engine.sync(sources)
    assert len(runtime.evolution_capability_activations) == 1
    runtime = AgentRuntime.restore(tmp_path / "runtime", clock=runtime.clock)
    engine = MemoryDomainEvolution(runtime=runtime, state_root=root)
    engine.sync(sources)
    state = engine._load(domain)
    assert state["status"] == "PROMOTED"
    assert load_policies(root)[domain] == state["challenger"]
    runtime.clock.advance(120)
    settled(root, query, 30, NOW + timedelta(seconds=200), helped=False)
    settled(root, query, 31, NOW + timedelta(seconds=210), helped=False)
    real_materialize = engine._materialize
    def crash_rollback(name, parameters, capability, status):
        if status == "ROLLED_BACK":
            raise OSError("simulated rollback disk interruption")
        real_materialize(name, parameters, capability, status)
    monkeypatch.setattr(engine, "_materialize", crash_rollback)
    with pytest.raises(OSError):
        engine.sync(sources)
    runtime = AgentRuntime.restore(tmp_path / "runtime", clock=runtime.clock)
    engine = MemoryDomainEvolution(runtime=runtime, state_root=root)
    engine.sync(sources)
    assert engine._load(domain)["status"] == "ROLLED_BACK"
    assert load_policies(root)[domain] == DEFAULTS[domain]
    assert len(runtime.evolution_capability_rollbacks) == 1


def test_other_policy_drift_invalidates_and_refreezes_without_activation(tmp_path):
    root, _, _, runtime, engine, kind, sources = setup(tmp_path, "decay")
    engine.sync(sources)
    old = engine._load("decay")["experiment_id"]
    (root / "application/memory_system/active_recall_policy.json").write_text(
        json.dumps({"weights": {"lexical_weight": 1.1}}), encoding="utf-8")
    runtime.clock.advance(180)
    engine.sync(sources)
    assert engine._load("decay")["status"] == "INVALIDATED_POLICY_DRIFT"
    assert runtime.evolution_experiment_manager.get(old).stage.value == "CLOSED"
    assert not runtime.evolution_capability_activations
    engine.sync(sources)
    state = engine._load("decay")
    assert state["experiment_id"] != old
    assert state["status"] == "WAITING_PROSPECTIVE_START"
    assert list((engine.root / "history").glob("*.json"))


def test_repeated_use_and_conflicting_settlement_do_not_inflate_labels(tmp_path):
    root, _, query, runtime, engine, _, _ = setup(tmp_path, "salience")
    obs = engine.observatory
    first = next(row for row in obs.rows() if row["event_type"] == "DECISION_USE")
    obs.settle(first["memory_use_id"], outcome_ref="another-outcome", outcome_success=True,
               memory_helped=True, evidence_ref="world:new")
    assert len(engine.cases(NOW + timedelta(days=1))) == 4
    obs.settle(first["memory_use_id"], outcome_ref="outcome:0", outcome_success=False,
               memory_helped=False, evidence_ref="world:correction")
    assert len(engine.cases(NOW + timedelta(days=1))) == 3


@pytest.mark.parametrize("domain", ["decay", "index", "salience"])
def test_counterevidence_for_supporting_prospective_sample_rolls_back_after_promotion(tmp_path, domain):
    root, _, query, runtime, engine, kind, sources = setup(tmp_path, domain)
    engine.sync(sources)
    runtime.clock.advance(180)
    for index in range(4):
        settled(root, query, index + 10, NOW + timedelta(seconds=90 + index * 5))
    engine.sync(sources)
    state = engine._load(domain)
    assert state["status"] == "PROMOTED"

    observations = engine.observatory.rows(limit=10000)
    sampled_use = next(
        row["memory_use_id"] for row in observations
        if row.get("decision_ref") == "decision:10"
    )
    runtime.clock.advance(120)
    contradict(
        root,
        sampled_use,
        "outcome:10",
        NOW + timedelta(seconds=220),
    )
    engine.sync(sources)

    state = engine._load(domain)
    assert state["status"] == "ROLLED_BACK"
    assert sampled_use in state["rollback_failure_refs"] or "outcome:10" in state["rollback_failure_refs"]
    assert load_policies(root)[domain] == DEFAULTS[domain]
    assert len(runtime.evolution_capability_rollbacks) == 1


@pytest.mark.parametrize("crash_point", ["candidate", "experiment"])
def test_starting_intent_recovers_runtime_objects_without_orphans(tmp_path, monkeypatch, crash_point):
    root, _, _, runtime, engine, _, sources = setup(tmp_path, "decay")
    original = (
        runtime.propose_evolution_candidate
        if crash_point == "candidate"
        else runtime.create_evolution_experiment
    )
    captured = {}

    def create_then_crash(*args, **kwargs):
        item = original(*args, **kwargs)
        captured["id"] = (
            item.candidate_id
            if crash_point == "candidate"
            else item.spec.experiment_id
        )
        raise OSError("simulated interruption after durable Runtime create")

    monkeypatch.setattr(
        runtime,
        "propose_evolution_candidate" if crash_point == "candidate" else "create_evolution_experiment",
        create_then_crash,
    )
    with pytest.raises(OSError):
        engine.sync(sources)

    state = engine._load("decay")
    assert state["status"] == "STARTING"
    if crash_point == "candidate":
        assert "candidate_id" not in state
        assert len(runtime.evolution_candidate_registry.items) == 1
    else:
        assert state["candidate_id"]
        assert "experiment_id" not in state
        assert len(runtime.evolution_experiment_manager.items) == 1

    if crash_point == "candidate":
        runtime.clock.advance(120)

    runtime = AgentRuntime.restore(tmp_path / "runtime", clock=runtime.clock)
    engine = MemoryDomainEvolution(runtime=runtime, state_root=root)
    engine.sync(sources)
    state = engine._load("decay")
    assert state["status"] == "WAITING_PROSPECTIVE_START"
    assert len(runtime.evolution_candidate_registry.items) == 1
    assert len(runtime.evolution_experiment_manager.items) == 1
    if crash_point == "candidate":
        assert state["candidate_id"] == captured["id"]
    else:
        assert state["experiment_id"] == captured["id"]
    assert aware(state["prospective_start_at"]) >= aware(runtime.clock.now())


def test_terminal_state_reconciles_runtime_abort_after_persist_crash(tmp_path, monkeypatch):
    root, _, _, runtime, engine, _, sources = setup(tmp_path, "decay")
    engine.sync(sources)
    old = engine._load("decay")["experiment_id"]
    assert runtime.evolution_experiment_manager.get(old).stage.value == "SHADOW"
    (root / "application/memory_system/active_recall_policy.json").write_text(
        json.dumps({"weights": {"lexical_weight": 1.1}}), encoding="utf-8")
    runtime.clock.advance(180)

    def crash_abort(*args, **kwargs):
        raise OSError("simulated interruption before Runtime abort")

    monkeypatch.setattr(runtime, "abort_evolution_experiment", crash_abort)
    with pytest.raises(OSError):
        engine.sync(sources)
    assert engine._load("decay")["status"] == "INVALIDATED_POLICY_DRIFT"
    assert runtime.evolution_experiment_manager.get(old).stage.value == "SHADOW"

    runtime = AgentRuntime.restore(tmp_path / "runtime", clock=runtime.clock)
    engine = MemoryDomainEvolution(runtime=runtime, state_root=root)
    engine.sync(sources)
    assert runtime.evolution_experiment_manager.get(old).stage.value == "CLOSED"
    state = engine._load("decay")
    assert state["experiment_id"] != old
    assert state["status"] == "WAITING_PROSPECTIVE_START"
