from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.fast_decision import FastDecisionResult
from wechat_traffic_agent.cognition_context import (
    build_ferro_cognition_context,
    owner_cognition_scope,
)
from wechat_traffic_agent.memory_self import ProspectiveSelfMemoryService


class _AppRuntime:
    cycle = 2371

    def available_capabilities(self):
        return ("content.status", "web.search", "web.fetch", "social.publish")


class _Inventory:
    def usable_ids(self):
        return ["advisory_reasoning", "structured_reasoning"]


class _DomainRuntime:
    active_goals = [
        SimpleNamespace(
            goal_id="goal-owner",
            description="Ship the real owner experiment",
            status=SimpleNamespace(value="ACTIVE"),
            priority=0.9,
        )
    ]
    external_missions = []
    missions = None
    capability_inventory = _Inventory()
    agent_manifest = SimpleNamespace(capabilities=("topic_discovery", "content_research"))


class _MindSession:
    def snapshot(self):
        return {
            "autobiography": {
                "events": [
                    {
                        "event_id": "experience-1",
                        "tick": 419,
                        "summary": "The previous strategy failed and should not be repeated.",
                        "source": "content-process",
                        "domain": "content-process",
                        "outcome": 0.0,
                        "confidence": 1.0,
                        "evidence_refs": ["receipt-1"],
                    }
                ]
            }
        }


class _Mind:
    session = _MindSession()

    def public_projection(self):
        return {
            "mind_schema": "content-mind:v1",
            "mind_tick": 420,
            "interests": [{"name": "remote-agent-debugging", "salience": 0.9, "preference": 0.8}],
            "open_inquiries": [{"inquiry_id": "inq-1", "question": "What angle converts?", "origin": "MISSION", "status": "OPEN"}],
            "beliefs": [{"proposition": "generic announcements underperform", "stance": 0.8, "confidence": 0.75, "revision_count": 2}],
            "recent_belief_changes": [{"proposition": "generic announcements underperform", "before": 0.2, "after": 0.8, "evidence_refs": ["receipt-1"]}],
            "latest_plan": {
                "cycle": "v2-123",
                "goal": {"goal_id": "goal-owner", "origin": "USER"},
                "external_mission_id": "mission-x",
                "owner_goal_id": "goal-owner",
                "world_inquiry_topics": {"inq-1": "remote-agent-debugging"},
                "irrelevant_bulk": "must not be copied",
            },
            "resource_awareness": {
                "current": {"pressure": "WATCH"},
                "observation_count": 12,
                "decision_count": 3,
                "outcome_count": 2,
                "reflection_count": 2,
            },
            "strategy_learning": {"outcome_count": 2, "topics": ["remote-agent-debugging"]},
            "learning_boundary": "world feedback changes future choices",
        }


class _ContextL1Provider:
    provider_name = "TEST"

    def choose(self, *, decision, state, choices, instructions=None):
        assert decision == "context_route"
        choice = "ACT"
        probabilities = {key: 0.0 for key in choices}
        probabilities[choice] = 1.0
        return FastDecisionResult(
            decision_id="fastdec-context-test",
            provider="TEST",
            model="test",
            choice=choice,
            confidence=1.0,
            probabilities=probabilities,
            latency_ms=0,
            decided_at="2026-09-28T15:00:00+00:00",
        )


class _Wakeful:
    def public_snapshot(self):
        return {
            "state": "THINKING",
            "attention": {"mission": 0.9},
            "resource_pressure": "WATCH",
            "last_cognition_at": "2026-09-25T15:00:00+00:00",
            "last_action_at": "2026-09-25T14:59:00+00:00",
            "pending_plan_count": 2,
            "recent_stimulus_kinds": ["MISSION_OUTCOME"],
            "neural_life": {
                "affect": {"curiosity": 0.7, "frustration": 0.4, "boredom": 0.8, "confidence": 0.76},
                "body": {"disk_pressure": 0.5},
                "drives": {"evolution": 0.8, "survival": 0.4},
                "intention": {"name": "EVOLVE", "strength": 0.8},
                "history": [{"must": "not leak bulk history"}],
            },
            "personality": {
                "traits": {
                    "novelty_seeking": 0.82,
                    "persistence": 0.73,
                    "sociality": 0.61,
                    "caution": 0.45,
                    "independence": 0.72,
                    "playfulness": 0.66,
                },
                "prompt_defined": False,
            },
            "psychodynamics": {
                "id": {"dominant_impulse": "EVOLVE"},
                "superego": {"continuity_pressure": 0.55, "growth_commitment": 0.84},
                "conflict": 0.25,
            },
            "neural_control_signals": [{"kind": "attention", "intent": "mission"}],
            "neural_feedback": [{"outcome_id": "out-1", "pathway": ["prediction", "result"]}],
            "self_model": {
                "capability_beliefs": {
                    "web_search": {
                        "attempts": 7,
                        "successes": 6,
                        "failures": 1,
                        "confidence": 0.82,
                        "last_verified": "2026-09-25T14:58:00+00:00",
                        "source": "direct_evidence",
                    }
                }
            },
            "neural_social": {
                "relationship_count": 1,
                "recent_relationships": [{
                    "actor": "x:user-42",
                    "interactions": 5,
                    "familiarity": 0.6,
                    "valence": 0.4,
                    "approach_bias": 0.73,
                }],
            },
        }


def _application():
    return SimpleNamespace(
        runtime=_AppRuntime(),
        domain_runtime=_DomainRuntime(),
        subject=SimpleNamespace(subject_id="content-agent", tick=2372),
    )


def test_self_memory_uses_application_runtime_available_capabilities():
    packet = ProspectiveSelfMemoryService.snapshot_from_runtime(_application(), _Mind())
    assert "web.search" in packet.current_capabilities
    assert "web.fetch" in packet.current_capabilities
    assert "social.publish" in packet.current_capabilities


def test_cognition_snapshot_unifies_current_harness_state():
    context = build_ferro_cognition_context(
        application=_application(),
        mind=_Mind(),
        wakeful=_Wakeful(),
    )

    assert context["continuity"]["application_cycle"] == 2371
    assert context["continuity"]["subject_tick"] == 2372
    assert context["continuity"]["mind_tick"] == 420
    assert context["mind"]["mind_tick"] == 420

    # V1 keeps the full source pool observable but does not dump every source
    # into the LLM context when it does not answer an information need.
    receipt = context["context_receipt"]
    assert receipt["candidate_counts"]["recent_experience"] == 1
    assert receipt["candidate_counts"]["capabilities"] == 8
    assert receipt["candidate_counts"]["decision_candidates"] >= 1
    assert context["recent_experience"] == []
    assert context["decision_context"]["selected_count"] <= 24
    assert set(context["capabilities"]["available"]).issubset({
        "content.status",
        "web.search",
        "web.fetch",
        "social.publish",
        "advisory_reasoning",
        "structured_reasoning",
        "topic_discovery",
        "content_research",
    })

    assert context["social_context"]["relationship_count"] == 1
    assert "history" not in str(context["neural_state"]["life"])
    assert context["decision_frame"]["decision_type"] == "GENERAL_COGNITION"
    assert receipt["downstream_budget_chars"] == 60_000
    assert (
        receipt["selection_policy"]
        == "information_need_global_retrieval_then_dependency_and_counterevidence; "
        "surface_disclosure_and_runtime_projector_are_final_bounds"
    )
    assert "internal_pressure" in {
        item["need_id"]
        for item in context["decision_context"]["information_needs"]
    }
    assert all(
        "question" not in item
        for item in context["decision_context"]["information_needs"]
    )
    assert (
        context["epistemic_contract"]["model_inference"]
        == "unverified_until_observed"
    )
    assert "irrelevant_bulk" not in str(context["mind"])


def test_owner_episode_context_filters_noise_and_demotes_neural_control():
    mission = SimpleNamespace(
        mission_id="mission-x",
        goal_id="goal-owner",
        description="Grow the real X audience for LivingRuntime Remote",
        metric="followers",
        baseline=1800,
        target=5000,
        direction=SimpleNamespace(value="INCREASE"),
        status=SimpleNamespace(value="ACTIVE"),
        deadline=None,
        constraints=(),
    )

    class Missions:
        experiments = []

        def active(self):
            return [mission]

        def progress(self, _mission_id, _now):
            return SimpleNamespace(
                current_value=1810,
                trajectory="BEHIND",
                pressure=0.8,
            )

    domain = _DomainRuntime()
    domain.missions = Missions()
    domain.external_missions = [mission]
    app = _application()
    app.domain_runtime = domain

    context = build_ferro_cognition_context(
        application=app,
        mind=_Mind(),
        wakeful=_Wakeful(),
        prompt="OWNER_AGENT_EPISODE: grow LivingRuntime Remote on X",
    )

    assert {row["goal_id"] for row in context["current_intent"]} == {"goal-owner"}
    assert {row["kind"] for row in context["current_intent"]} == {"GOAL"}
    assert [row["mission_id"] for row in context["missions"]["active"]] == ["mission-x"]
    assert context["decision_frame"]["decision_type"] == "OWNER_GOAL_ACTION"
    assert context["open_loops"][0]["kind"] == "MISSION"
    assert context["open_loops"][0]["id"] == "mission-x"
    assert any(
        row["kind"] == "PENDING_PLAN_QUEUE"
        for row in context["open_loops"]
    )
    assert len(context["mind"]["interests"]) <= 3
    assert context["mind"]["open_inquiries"] == []
    assert context["mind"]["beliefs"] == []
    assert context["neural_state"]["control_signals"] == []
    assert context["neural_state"]["feedback"] == []
    assert context["neural_state"]["control_authority"] == "ADVISORY_SIGNAL_ONLY"


def test_owner_episode_does_not_use_generic_instruction_words_as_memory_query():
    mission = SimpleNamespace(
        mission_id="mission-x",
        goal_id="goal-owner",
        description="Grow the real X audience for LivingRuntime Remote",
        metric="followers",
        baseline=1800,
        target=5000,
        direction=SimpleNamespace(value="INCREASE"),
        status=SimpleNamespace(value="ACTIVE"),
        deadline=None,
        constraints=(),
    )

    class Missions:
        experiments = []
        def active(self):
            return [mission]
        def progress(self, _mission_id, _now):
            return SimpleNamespace(current_value=1810, trajectory="BEHIND", pressure=0.8)

    class GenericOverlapMind(_Mind):
        def public_projection(self):
            value = super().public_projection()
            value["open_inquiries"] = [{
                "inquiry_id": "rates-noise",
                "question": "What current evidence changes the rates regime?",
                "origin": "curiosity",
                "status": "OPEN",
            }]
            value["beliefs"] = [{
                "proposition": "Current evidence changes the rates regime.",
                "stance": 0.4,
                "confidence": 0.5,
            }]
            value["latest_plan"] = {
                **value["latest_plan"],
                "goal": {"goal_id": "goal-rates", "origin": "INQUIRY"},
                "owner_goal_id": None,
                "external_mission_id": None,
            }
            return value

    domain = _DomainRuntime()
    domain.missions = Missions()
    domain.external_missions = [mission]
    app = _application()
    app.domain_runtime = domain

    context = build_ferro_cognition_context(
        application=app,
        mind=GenericOverlapMind(),
        wakeful=_Wakeful(),
        prompt=(
            "OWNER_AGENT_EPISODE: choose the next useful action for the current "
            "owner goal using evidence and tools."
        ),
    )

    assert context["mind"]["open_inquiries"] == []
    assert context["mind"]["beliefs"] == []
    assert context["mind"]["latest_plan"] is None


def test_owner_scope_filters_downstream_pipeline_prompts_not_only_episode_prompt():
    mission = SimpleNamespace(
        mission_id="mission-x",
        goal_id="goal-owner",
        description="Grow the real X audience for LivingRuntime Remote",
        metric="followers",
        baseline=1800,
        target=5000,
        direction=SimpleNamespace(value="INCREASE"),
        status=SimpleNamespace(value="ACTIVE"),
        deadline=None,
        constraints=(),
    )

    class Missions:
        experiments = []

        def active(self):
            return [mission]

        def progress(self, _mission_id, _now):
            return SimpleNamespace(
                current_value=1810,
                trajectory="BEHIND",
                pressure=0.8,
            )

    domain = _DomainRuntime()
    domain.missions = Missions()
    domain.external_missions = [mission]
    app = _application()
    app.domain_runtime = domain

    with owner_cognition_scope({
        "goal_id": "goal-owner",
        "mission_id": "mission-x",
        "description": "Grow the real X audience for LivingRuntime Remote",
        "topic": "livingruntime-remote-durable-execution",
    }):
        context = build_ferro_cognition_context(
            application=app,
            mind=_Mind(),
            wakeful=_Wakeful(),
            prompt="classify candidate relevance for durable execution",
        )

    assert [row["goal_id"] for row in context["current_intent"]] == ["goal-owner"]
    assert [row["mission_id"] for row in context["missions"]["active"]] == ["mission-x"]
    assert context["mind"]["open_inquiries"] == []
    assert context["mind"]["beliefs"] == []
    assert context["mind"]["latest_plan"]["owner_goal_id"] == "goal-owner"
    assert context["recent_experience"] == []
    assert context["neural_state"]["control_signals"] == []
    assert context["neural_state"]["feedback"] == []


def test_owner_scope_drops_latest_plan_bound_to_another_goal():
    class OtherPlanMind(_Mind):
        def public_projection(self):
            value = super().public_projection()
            value["latest_plan"] = {
                **value["latest_plan"],
                "goal": {"goal_id": "goal-internal", "origin": "INQUIRY"},
                "owner_goal_id": None,
                "external_mission_id": None,
            }
            return value

    app = _application()
    with owner_cognition_scope({
        "goal_id": "goal-owner",
        "description": "Grow LivingRuntime Remote",
        "topic": "livingruntime-remote-durable-execution",
    }):
        context = build_ferro_cognition_context(
            application=app,
            mind=OtherPlanMind(),
            wakeful=_Wakeful(),
            prompt="classify durable execution evidence",
        )

    assert context["mind"]["latest_plan"] is None

def test_owner_scope_relevance_ignores_generic_orchestration_words():
    class NoisySession:
        def snapshot(self):
            return {
                "autobiography": {
                    "events": [
                        {
                            "event_id": "relevant",
                            "tick": 1,
                            "summary": (
                                "LivingRuntime Remote durable execution preserved "
                                "a remote coding session after restart."
                            ),
                            "source": "content-process",
                            "outcome": 1.0,
                            "confidence": 1.0,
                        },
                        {
                            "event_id": "generic-noise",
                            "tick": 2,
                            "summary": (
                                "The current agent action used evidence to choose "
                                "a policy response for crypto market rates."
                            ),
                            "source": "session",
                            "outcome": 0.5,
                            "confidence": 1.0,
                        },
                    ]
                }
            }

    class NoisyMind(_Mind):
        session = NoisySession()

    with owner_cognition_scope({
        "goal_id": "goal-owner",
        "description": "Grow LivingRuntime Remote",
        "topic": "livingruntime-remote-durable-execution",
    }):
        context = build_ferro_cognition_context(
            application=_application(),
            mind=NoisyMind(),
            wakeful=_Wakeful(),
            prompt=(
                "OWNER_AGENT_EPISODE: choose the current agent action using "
                "evidence and tools"
            ),
        )

    assert [row["event_id"] for row in context["recent_experience"]] == [
        "relevant"
    ]


class _LowPressureWakeful(_Wakeful):
    def public_snapshot(self):
        value = super().public_snapshot()
        value["resource_pressure"] = "NORMAL"
        value["neural_life"] = {
            **value["neural_life"],
            "affect": {
                **value["neural_life"]["affect"],
                "boredom": 0.81,
                "confidence": 0.79,
            },
            "drives": {
                **value["neural_life"]["drives"],
                "evolution": 0.83,
            },
        }
        return value


def test_phenotype_prior_is_always_on_even_when_internal_retrieval_is_suppressed():
    prompts = (
        "SOCIAL_EXPRESSION_DRAFT: write the X expression for this supported finding.",
        "PLAN: choose the next bounded experiment.",
        "VERIFY: check whether this claim is supported by the evidence.",
        "Answer the current question directly.",
    )

    contexts = [
        build_ferro_cognition_context(
            application=_application(),
            mind=_Mind(),
            wakeful=_LowPressureWakeful(),
            prompt=prompt,
        )
        for prompt in prompts
    ]

    first_prior = contexts[0]["phenotype_prior"]
    first_traits = first_prior["stable_phenotype"]["personality_traits"]
    assert first_traits["independence"] == 0.72
    assert first_traits["persistence"] == 0.73
    assert first_prior["dynamic_modulation"]["affect"]["boredom"] == 0.81
    assert (
        first_prior["retrieval_policy"]["stable_phenotype"]
        == "ALWAYS_ON_NEVER_RETRIEVAL_CONDITIONED"
    )

    for context in contexts:
        assert context["phenotype_prior"]["stable_phenotype"] == first_prior["stable_phenotype"]
        assert context["behavioral_state"]["personality_traits"] == first_traits
        assert context["behavioral_state"]["affect"]["boredom"] == 0.81
        assert context["decision_context"]["always_on_sources"] == [
            "phenotype_prior",
            "behavioral_state",
            "interoception",
        ]
        fact_ids = {
            str(row.get("context_id") or "")
            for row in context["decision_context"]["fact_index"]
        }
        assert "internal:behavioral" not in fact_ids


def test_behavioral_state_is_not_a_retrieval_candidate_anymore():
    context = build_ferro_cognition_context(
        application=_application(),
        mind=_Mind(),
        wakeful=_LowPressureWakeful(),
        prompt="Answer an unrelated general question.",
    )

    receipt = context["context_receipt"]
    assert receipt["selection_summary"]["always_on_sources"] == [
        "phenotype_prior",
        "behavioral_state",
        "interoception",
    ]
    selected_ids = {
        str(row.get("context_id") or "")
        for row in context["decision_context"]["fact_index"]
    }
    rejected_ids = {
        str(row.get("context_id") or "")
        for row in receipt["selection_summary"]["rejected_candidates"]
    }
    assert "internal:behavioral" not in selected_ids
    assert "internal:behavioral" not in rejected_ids
    assert context["phenotype_prior"]["stable_phenotype"]["personality_traits"]


def test_interoception_is_always_on_cognition_context(tmp_path):
    from datetime import datetime, timezone

    from wechat_traffic_agent.interoceptive_fault import InteroceptiveFaultSystem

    mind = _Mind()
    mind.state_root = tmp_path
    system = InteroceptiveFaultSystem(tmp_path)
    system.observe(
        "CONTENT_COGNITION",
        {
            "status": "DEGRADED",
            "cycle": "sick-cycle",
            "context_builder": {"status": "ERROR"},
        },
        now=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
    )

    context = build_ferro_cognition_context(
        application=_application(),
        mind=mind,
        wakeful=_Wakeful(),
        prompt="choose the next action",
        l1_provider=_ContextL1Provider(),
    )

    assert context["interoception"]["status"] in {"WATCH", "ILL"}
    assert context["interoception"]["distress"] > 0
    assert context["interoception"]["active_episode_ids"]
    assert context["interoception"]["suspected_faults"]
    assert "interoception" in context["decision_context"]["always_on_sources"]
    assert "interoception" in context["context_receipt"]["selection_summary"]["always_on_sources"]


def test_context_contract_violation_enters_self_maintenance(monkeypatch, tmp_path):
    import json

    mind = _Mind()
    mind.state_root = tmp_path

    report = {
        "schema_version": "ferro-capability-contracts.v1",
        "surface": "cognition_context",
        "status": "VIOLATED",
        "observation_id": "capability-contract:forced-context-break",
        "observed_at": "2026-09-28T16:30:00+00:00",
        "duplicate": False,
        "checked_contracts": ["relevant_context_must_reach_cognition"],
        "violations": [{
            "kind": "context_selection_empty",
            "subsystem": "context",
            "severity": 0.72,
            "contract": "relevant_context_must_reach_cognition",
            "expected": "relevant facts reach model cognition",
            "observed": "candidate_count=12, selected_count=0",
            "evidence_refs": ["context-receipt:selection"],
        }],
    }
    monkeypatch.setattr(
        "wechat_traffic_agent.cognition_context.CapabilityContractMonitor.observe_context",
        lambda self, snapshot, *, now: dict(report),
    )

    context = build_ferro_cognition_context(
        application=_application(),
        mind=mind,
        wakeful=_Wakeful(),
        prompt="choose the next useful action",
        now=__import__("datetime").datetime(
            2026, 9, 28, 16, 30,
            tzinfo=__import__("datetime").timezone.utc,
        ),
        l1_provider=_ContextL1Provider(),
    )

    assert context["capability_contracts"]["status"] == "VIOLATED"
    assert context["interoception"]["status"] in {"WATCH", "ILL"}
    assert context["capability_contract_diagnosis"]["status"] in {
        "RECORDED",
        "ALREADY_RECORDED",
    }
    problems = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
    ]
    problem = next(
        row for row in problems
        if row.get("error_type") == "CAPABILITY_CONTRACT_VIOLATION"
    )
    assert problem["failure_site"] == "cognition_context.py:capability_contract"
    assert problem["status"] == "DIAGNOSING"
