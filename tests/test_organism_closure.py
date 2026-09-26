from datetime import datetime, timedelta, timezone

import pytest
from types import SimpleNamespace

from wechat_traffic_agent.autonomous_life import AutonomousLifeLoop
from wechat_traffic_agent.developmental_history import DevelopmentalHistory
from wechat_traffic_agent.drive_system import DriveEngine
from wechat_traffic_agent.evolution_ledger import EvolutionLedger
from wechat_traffic_agent.lineage_experiments import LineageExperimentLedger
from wechat_traffic_agent.organism_closure import _due_plan_count, refresh_organism_closure
from wechat_traffic_agent.organism_state import OrganismStateService
from wechat_traffic_agent.self_model import SelfModel
from wechat_traffic_agent.world_learning import WorldLearningLedger


NOW = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)


def _neural(now=NOW):
    return {
        "updated_at": now.isoformat(),
        "body": {
            "observed_at": now.isoformat(),
            "cpu_pressure": 0.1,
            "memory_pressure": 0.2,
            "disk_pressure": 0.3,
        },
        "drives": {"survival": 0.15, "reproduction": 0.2, "evolution": 0.45},
        "affect": {
            "arousal": 0.25,
            "curiosity": 0.65,
            "frustration": 0.15,
            "confidence": 0.7,
            "boredom": 0.55,
            "social_need": 0.25,
            "resource_hunger": 0.1,
        },
        "neural_features": {
            "opportunity": 0.55,
            "threat_risk": 0.1,
            "network_error_ratio": 0.0,
        },
    }


def _personality(now=NOW):
    return {
        "updated_at": now.isoformat(),
        "traits": {
            "novelty_seeking": 0.7,
            "persistence": 0.65,
            "sociality": 0.4,
            "caution": 0.35,
            "independence": 0.6,
            "playfulness": 0.7,
        },
    }


def _policies(root, observed_at=NOW):
    for rel, pressure in (
        ("application/survival_resources/policy.json", "NORMAL"),
        ("application/llm_resource_pool/policy.json", "NORMAL"),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"observed_at":"%s","pressure":"%s"}' % (observed_at.isoformat(), pressure),
            encoding="utf-8",
        )
    llm_resources = root / "application/llm_resource_pool/resources.json"
    llm_resources.write_text(
        (
            '{"observed_at":"%s","resources":['
            '{"quality_score":1.0,"success_rate":1.0,"pricing_class":"FREE"}]}'
        ) % observed_at.isoformat(),
        encoding="utf-8",
    )
    survival_resources = root / "application/survival_resources/resources.json"
    survival_resources.write_text(
        (
            '{"observed_at":"%s","resources":['
            '{"domain":"SERVICE","kind":"PRIMARY_STATEFUL","status":"ACTIVE"},'
            '{"domain":"REMOTE_MACHINE","status":"ACTIVE","receiver_status":"VERIFIED"}]}'
        ) % observed_at.isoformat(),
        encoding="utf-8",
    )


class _Snap:
    pending_plans = ()
    last_cognition_at = NOW - timedelta(hours=2)
    last_action_at = NOW - timedelta(hours=3)


def test_future_calendar_plans_do_not_count_as_current_workload():
    snapshot = SimpleNamespace(
        pending_plans=(
            SimpleNamespace(at=NOW - timedelta(minutes=1), task="DUE"),
            SimpleNamespace(at=NOW, task="DUE_NOW"),
            SimpleNamespace(at=NOW + timedelta(minutes=1), task="FUTURE"),
            SimpleNamespace(at=NOW + timedelta(days=365), task="FAR_FUTURE"),
        )
    )

    assert _due_plan_count(snapshot, NOW) == 2



def test_organism_state_unknown_never_becomes_healthy(tmp_path):
    service = OrganismStateService(tmp_path)
    result = service.refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW,
        last_action_at=NOW,
    )
    assert result["summary"]["status"] == "UNKNOWN"
    assert result["summary"]["unknown_signal_count"] >= 2
    assert result["unknown_is_healthy"] is False

    _policies(tmp_path)
    result = service.refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW,
        last_action_at=NOW,
    )
    assert result["summary"]["status"] == "NORMAL"
    assert result["summary"]["unknown_signal_count"] == 0
    assert result["summary"]["vitality"] > 0.7
    assert result["summary"]["model_availability"] == 1.0
    assert result["summary"]["service_health"] == 1.0
    assert result["summary"]["network_health"] == 1.0
    assert result["signals"]["monetary_pressure"]["freshness"] == "FRESH"
    assert result["signals"]["token_pressure"]["freshness"] == "FRESH"
    assert result["signals"]["energy"]["value"] > 0.5


def test_free_verified_models_prevent_paid_budget_pressure_from_self_locking_cognition(tmp_path):
    _policies(tmp_path)
    policy = tmp_path / "application/llm_resource_pool/policy.json"
    policy.write_text(
        (
            '{"observed_at":"%s","pressure":"CRITICAL",'
            '"paid_available_funds":0.0,"paid_reserve_floor":20.0}'
        ) % NOW.isoformat(),
        encoding="utf-8",
    )

    result = OrganismStateService(tmp_path).refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW,
        last_action_at=NOW,
    )

    assert result["signals"]["monetary_pressure"]["value"] == 1.0
    assert result["signals"]["model_resource_pressure"]["value"] == 0.0
    assert result["signals"]["token_pressure"]["value"] == 0.0
    assert result["summary"]["free_model_availability"] == 1.0
    assert result["summary"]["status"] == "NORMAL"


def test_drive_engine_is_neural_and_homeostatic_but_cannot_bypass_admission(tmp_path):
    _policies(tmp_path)
    organism = OrganismStateService(tmp_path).refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW - timedelta(hours=2),
        last_action_at=NOW - timedelta(hours=3),
    )
    result = DriveEngine(tmp_path).refresh(
        now=NOW,
        organism=organism,
        neural_life=_neural(),
        personality=_personality(),
    )
    assert set(result["drives"]) == {
        "survival", "resource", "curiosity", "mastery",
        "social", "creation", "rest", "propagation",
    }
    assert result["last_decision"]["authority"] == "HOMEOSTATIC_DRIVE_ARBITRATION"
    assert result["last_decision"]["bypasses_admission"] is False
    assert result["model_generated_desire"] is False


def test_drive_no_effect_outcome_creates_attempt_fatigue(tmp_path):
    _policies(tmp_path)
    organism = OrganismStateService(tmp_path).refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW,
        last_action_at=NOW,
    )
    engine = DriveEngine(tmp_path)
    engine.refresh(
        now=NOW,
        organism=organism,
        neural_life=_neural(),
        personality=_personality(),
    )
    settled = engine.settle(
        drive="resource",
        task="RESOURCE_REVIEW",
        status="EXECUTED",
        utility=0.0,
        evidence_ref="task-result:no-effect",
        now=NOW,
    )
    assert settled["no_effect_streak"] == 1
    assert settled["failure_streak"] == 0
    assert settled["cooldown_until"] is not None

    refreshed = engine.refresh(
        now=NOW + timedelta(minutes=1),
        organism=organism,
        neural_life=_neural(NOW + timedelta(minutes=1)),
        personality=_personality(NOW + timedelta(minutes=1)),
    )
    candidate = next(
        row for row in refreshed["last_decision"]["candidates"]
        if row["drive"] == "resource"
    )
    assert candidate["feedback_penalty"] >= 0.14
    assert (
        candidate["arbitration_score"]
        < candidate["pre_feedback_arbitration_score"]
    )


def test_positive_world_utility_satisfies_and_cools_drive(tmp_path):
    _policies(tmp_path)
    organism = OrganismStateService(tmp_path).refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW,
        last_action_at=NOW,
    )
    engine = DriveEngine(tmp_path)
    engine.refresh(
        now=NOW,
        organism=organism,
        neural_life=_neural(),
        personality=_personality(),
    )
    settled = engine.settle(
        drive="curiosity",
        task="OBSERVE_WORLD",
        status="SATISFIED",
        utility=0.6,
        evidence_ref="world:evidence",
        now=NOW,
    )
    assert settled["last_satisfied_at"] == NOW.isoformat()
    assert settled["outcome_satisfaction"] >= 0.6
    assert settled["no_effect_streak"] == 0

    refreshed = engine.refresh(
        now=NOW + timedelta(minutes=1),
        organism=organism,
        neural_life=_neural(NOW + timedelta(minutes=1)),
        personality=_personality(NOW + timedelta(minutes=1)),
    )
    curiosity = refreshed["drives"]["curiosity"]
    assert curiosity["outcome_satisfaction"] > 0.0
    assert curiosity["urgency"] < curiosity["intensity"]


def test_resource_emergency_caps_feedback_backoff(tmp_path):
    _policies(tmp_path)
    organism = OrganismStateService(tmp_path).refresh(
        now=NOW,
        neural_life=_neural(),
        resource_pressure="NORMAL",
        pending_plan_count=0,
        last_cognition_at=NOW,
        last_action_at=NOW,
    )
    organism["summary"]["status"] = "CRITICAL"
    organism["signals"]["host_resource_pressure"]["value"] = 0.95
    engine = DriveEngine(tmp_path)
    engine.refresh(
        now=NOW,
        organism=organism,
        neural_life=_neural(),
        personality=_personality(),
    )
    engine.settle(
        drive="resource",
        task="RESOURCE_REVIEW",
        status="FAILED",
        utility=-0.4,
        evidence_ref="resource:failed",
        now=NOW,
    )
    refreshed = engine.refresh(
        now=NOW + timedelta(minutes=1),
        organism=organism,
        neural_life=_neural(NOW + timedelta(minutes=1)),
        personality=_personality(NOW + timedelta(minutes=1)),
    )
    candidate = next(
        row for row in refreshed["last_decision"]["candidates"]
        if row["drive"] == "resource"
    )
    assert candidate["feedback_penalty"] <= 0.04


def test_development_history_is_path_dependent_and_opens_mastery_need(tmp_path):
    history = DevelopmentalHistory(tmp_path)
    for index in range(3):
        history.record(
            kind="TASK_FAILURE",
            domain="provider_recovery",
            outcome=-1,
            evidence_refs=(f"failure:{index}",),
            now=NOW + timedelta(minutes=index),
        )
    projection = history.projection()
    assert projection["domains"]["provider_recovery"]["failures"] == 3
    assert projection["open_developmental_needs"][0]["kind"] == "MASTERY_NEED"
    assert projection["path_dependent"] is True


def test_world_learning_requires_world_evidence_and_settles_delayed_windows(tmp_path):
    ledger = WorldLearningLedger(tmp_path)
    action = ledger.commit(
        action_kind="CONTENT_EXPERIMENT",
        prediction="new title improves useful reads",
        expected_utility=0.5,
        confidence=0.8,
        evidence_refs=("plan:42",),
        now=NOW,
        windows=(("10m", timedelta(minutes=10)),),
    )
    assert ledger.due(NOW + timedelta(minutes=9)) == []
    assert ledger.due(NOW + timedelta(minutes=10))[0]["action_id"] == action["action_id"]
    with pytest.raises(ValueError):
        ledger.settle(
            action_id=action["action_id"], window="10m",
            actual_utility=-0.2, evidence_refs=(), now=NOW + timedelta(minutes=10),
        )
    settlement = ledger.settle(
        action_id=action["action_id"], window="10m",
        actual_utility=-0.2, evidence_refs=("metrics:42",),
        counterfactual="a control title was not exposed in this trial",
        causal_factors={"topic": -0.4, "timing": 0.1},
        now=NOW + timedelta(minutes=10),
    )
    assert settlement["status"] == "SETTLED"
    assert settlement["prediction_error"] < 0
    assert ledger.projection()["function_success_is_reward"] is False


def test_self_model_is_falsifiable_and_counter_evidence_reduces_confidence(tmp_path):
    model = SelfModel(tmp_path)
    first = model.observe_capability(
        capability="provider_recovery", success=True,
        evidence_ref="recovery:1", now=NOW,
    )
    second = model.observe_capability(
        capability="provider_recovery", success=False,
        evidence_ref="recovery:2", now=NOW + timedelta(minutes=1),
    )
    assert second["counter_evidence"] == ["recovery:2"]
    assert second["confidence"] != first["confidence"]
    assert model.projection()["prompt_claims_are_evidence"] is False


def test_autonomous_life_creates_bounded_goal_with_provenance(tmp_path):
    loop = AutonomousLifeLoop(tmp_path)
    result = loop.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:42",
                "winner": {
                    "drive": "mastery",
                    "task": "REFLECT",
                    "reason": "repeated errors",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal = result["goal"]
    assert result["status"] == "GOAL_SCHEDULED"
    assert goal["origin"] == "DEVELOPMENT"
    assert goal["resource_budget"]["max_llm_calls"] == 1
    assert goal["bypasses_admission"] is False
    assert loop.projection()["unbounded_self_generated_work"] is False


def test_lineage_is_sandbox_only_and_cannot_inherit_security_authority(tmp_path):
    lineage = LineageExperimentLedger(tmp_path)
    with pytest.raises(ValueError):
        lineage.propose(
            parent_organism_id="ferro:1", generation=1,
            inherited_traits={"credentials": 1.0}, inherited_capabilities={},
            neural_priors={}, mutations={}, purpose="bad",
        )
    spec = lineage.propose(
        parent_organism_id="ferro:1", generation=1,
        inherited_traits={"curiosity": 0.6},
        inherited_capabilities={"research": 0.5},
        neural_priors={"exploration": 0.55},
        mutations={"exploration": 0.1},
        purpose="compare exploration prior",
        now=NOW,
    )
    assert spec["offspring_organism_id"] != spec["parent_organism_id"]
    assert spec["resource_budget"]["max_external_writes"] == 0
    assert spec["self_replication_allowed"] is False
    assert lineage.projection()["autonomous_replication"] is False


def test_evolution_ledger_has_multidimensional_fitness_and_immutable_boundaries(tmp_path):
    ledger = EvolutionLedger(tmp_path)
    with pytest.raises(ValueError):
        ledger.propose(
            domain="planner", limitation="x", hypothesis="y",
            mutation={"secret_access_boundary": "relax"},
            evidence_ref="evidence:1", now=NOW,
        )
    variant = ledger.propose(
        domain="planner", limitation="overreacts to novelty",
        hypothesis="lower novelty prior improves reliability",
        mutation={"novelty_prior": -0.1},
        evidence_ref="evidence:2", now=NOW,
    )
    evaluated = ledger.evaluate(
        variant_id=variant["variant_id"],
        fitness={"reliability": 0.8, "adaptability": 0.4},
        evidence_ref="trial:2", now=NOW + timedelta(hours=1),
    )
    assert evaluated["scalar_fitness"] is None
    assert evaluated["automatic_promotion"] is False
    assert "security_sandbox" in ledger.projection()["protected_boundaries"]


def test_full_closure_composes_body_drive_goal_and_self_model(tmp_path):
    _policies(tmp_path)
    services = {
        "organism_state": OrganismStateService(tmp_path),
        "drive_system": DriveEngine(tmp_path),
        "developmental_history": DevelopmentalHistory(tmp_path),
        "autonomous_life": AutonomousLifeLoop(tmp_path),
        "self_model": SelfModel(tmp_path),
        "world_learning": WorldLearningLedger(tmp_path),
    }
    result = refresh_organism_closure(
        now=NOW,
        snapshot=_Snap(),
        neural_life=_neural(),
        personality=_personality(),
        cortex={"organism_id": "ferro:test"},
        continuity={"organism_id": "ferro:test"},
        resource_pressure="NORMAL",
        **services,
    )
    assert result["organism_state"]["summary"]["status"] == "NORMAL"
    assert result["drives"]["last_decision"]["decision_id"]
    assert result["self_model"]["core_identity"]["organism_id"] == "ferro:test"
    if result["goal"] is not None:
        assert services["developmental_history"].projection()["milestones"]["FIRST_INDEPENDENT_GOAL"]
