from __future__ import annotations

from pathlib import Path

from wechat_traffic_agent.content_mind import ContentMind


def test_process_experience_causes_persistent_endogenous_topic_goal(tmp_path: Path):
    root = tmp_path / "state"
    mind = ContentMind(root=root, subject_id="content-agent")

    initial = mind.plan_for_cycle(cycle="v2-100", available_topics=("common", "rare"))
    assert initial["goal"] is None
    assert initial["required_topic"] is None

    observed = mind.observe_process_experience(
        cycle="v2-100",
        topic="common",
        passed=True,
        model_calls=4,
        evidence_refs=("cycle:v2-100", "draft-sha256:abc"),
    )
    assert observed["status"] == "OBSERVED"
    assert observed["outcome_kind"] == "PROCESS_ONLY"
    assert observed["audience_outcome_used"] is False
    assert observed["interest"]["name"] == "common"

    plan = mind.plan_for_cycle(cycle="v2-200", available_topics=("common", "rare"))
    assert plan["goal"]["origin"] == "INTEREST"
    assert plan["goal"]["interest_refs"] == ["common"]
    assert plan["required_topic"] == "common"


def test_restart_preserves_goal_and_duplicate_experience_is_idempotent(tmp_path: Path):
    root = tmp_path / "state"
    first = ContentMind(root=root, subject_id="content-agent")
    first.observe_process_experience(
        cycle="v2-100", topic="rare", passed=True, model_calls=2,
        evidence_refs=("cycle:v2-100",),
    )
    planned = first.plan_for_cycle(cycle="v2-200", available_topics=("common", "rare"))

    restarted = ContentMind(root=root, subject_id="content-agent")
    replay = restarted.plan_for_cycle(cycle="v2-200", available_topics=("common", "rare"))
    assert replay == planned
    assert replay["required_topic"] == "rare"

    duplicate = restarted.observe_process_experience(
        cycle="v2-100", topic="rare", passed=True, model_calls=2,
        evidence_refs=("cycle:v2-100",),
    )
    assert duplicate["status"] == "ALREADY_OBSERVED"
    projection = restarted.public_projection()
    assert projection["interests"][0]["name"] == "rare"
    assert "mature Website audience outcomes are connected" in projection["learning_boundary"]


def test_authority_key_is_private(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    path = mind.root / "authority.key"
    assert path.exists()
    assert path.stat().st_mode & 0o077 == 0


def test_world_observation_can_create_inquiry_and_bind_endogenous_goal(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    observation = {
        "evidence_id": "web:fed-1",
        "topic": "monetary-policy",
        "question": "Does the renewed rate path materially change crypto liquidity conditions?",
        "importance": 0.95,
        "novelty": 0.9,
        "why_now": "A fresh policy signal could alter the agent's prior market assumptions.",
    }
    plan = mind.plan_for_cycle(
        cycle="v2-100", available_topics=("model-evaluation",),
        world_observations=(observation,),
    )
    assert "monetary-policy" in plan["available_topics"]
    assert plan["goal"] is not None
    assert plan["goal"]["origin"] == "INQUIRY"
    assert plan["required_topic"] == "monetary-policy"
    projection = mind.public_projection()
    assert any(row["question"] == observation["question"] for row in projection["open_inquiries"])


def test_repeated_world_question_does_not_open_duplicate_inquiry(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    observation = {
        "evidence_id": "web:btc-1", "topic": "bitcoin-liquidity",
        "question": "Is the latest Bitcoin move being driven by liquidity rather than leverage?",
        "importance": 0.9, "novelty": 0.8, "why_now": "The move is fresh and market-relevant.",
    }
    mind.plan_for_cycle(cycle="v2-100", available_topics=(), world_observations=(observation,))
    first_count = len(mind.public_projection()["open_inquiries"])
    mind.plan_for_cycle(cycle="v2-200", available_topics=(), world_observations=(observation,))
    assert len(mind.public_projection()["open_inquiries"]) == first_count


def test_world_attention_forms_neutral_competing_interests(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    observations = (
        {
            "evidence_id": "web:liquidity-1", "topic": "macro-liquidity",
            "question": "Is global liquidity becoming the dominant driver of crypto risk appetite?",
            "importance": 0.94, "novelty": 0.96,
            "why_now": "A fresh cross-asset move makes the liquidity explanation newly testable.",
            "evidence_refs": ("web:liquidity-1", "fetch:liquidity-1"),
        },
        {
            "evidence_id": "web:stablecoin-1", "topic": "stablecoin-flows",
            "question": "Are stablecoin flows changing enough to alter near-term crypto liquidity?",
            "importance": 0.88, "novelty": 0.72,
            "why_now": "Fresh issuance and exchange-flow evidence creates a competing explanation.",
            "evidence_refs": ("web:stablecoin-1", "fetch:stablecoin-1"),
        },
    )
    first = mind.plan_for_cycle(cycle="v2-100", available_topics=(), world_observations=observations)
    updates = {row["topic"]: row for row in first["world_interest_updates"]}
    assert set(updates) == {"macro-liquidity", "stablecoin-flows"}
    assert updates["macro-liquidity"]["interest"]["preference"] == 0.5
    assert updates["stablecoin-flows"]["interest"]["preference"] == 0.5
    assert updates["macro-liquidity"]["interest"]["salience"] == 0.96
    assert updates["stablecoin-flows"]["interest"]["salience"] == 0.72

    for inquiry_id, state in mind.session.inquiry.inquiries.items():
        if state["status"] == "OPEN":
            mind.session.inquiry.resolve(inquiry_id=inquiry_id)
    second = mind.plan_for_cycle(cycle="v2-200", available_topics=())
    assert second["goal"]["origin"] == "INTEREST"
    assert second["required_topic"] == "macro-liquidity"


def test_replayed_world_attention_does_not_reinforce_interest_twice(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    observation = {
        "evidence_id": "web:etf-1", "topic": "etf-flows",
        "question": "Are spot ETF flows becoming more informative than leverage for this move?",
        "importance": 0.9, "novelty": 0.85, "why_now": "Fresh flow data can test the current explanation.",
        "evidence_refs": ("web:etf-1", "fetch:etf-1"),
    }
    first = mind.plan_for_cycle(cycle="v2-100", available_topics=(), world_observations=(observation,))
    state = first["world_interest_updates"][0]["interest"]
    second = mind.plan_for_cycle(cycle="v2-200", available_topics=(), world_observations=(observation,))
    assert second["world_interest_updates"][0]["status"] == "ALREADY_OBSERVED"
    assert second["world_interest_updates"][0]["interest"] == state


def test_low_importance_world_attention_stays_short_term(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    observation = {
        "evidence_id": "web:peripheral-1", "topic": "peripheral-macro-signal",
        "question": "Could this peripheral macro signal become relevant if it repeats?",
        "importance": 0.55, "novelty": 0.9,
        "why_now": "It is novel but not important enough yet to shape a long-term interest.",
        "evidence_refs": ("web:peripheral-1",),
    }
    plan = mind.plan_for_cycle(cycle="v2-100", available_topics=(), world_observations=(observation,))
    assert plan["world_interest_updates"] == [{
        "topic": "peripheral-macro-signal", "status": "SHORT_TERM_ONLY",
        "interest": None, "importance": 0.55,
    }]
    assert mind.session.interests.get("peripheral-macro-signal") is None
    assert plan["goal"] is None


def _research_source(evidence_id="web:rates-1", revision="rev-1"):
    return {
        "evidence_id": evidence_id, "title": "Treasury yields move above a prior regime range",
        "summary": "The observed source reports a material move in long-duration borrowing costs.",
        "source_revision": revision, "source": "runtime:web.fetch:test",
        "evidence_refs": (evidence_id, "fetch:" + revision),
    }


def test_research_conclusion_becomes_evidence_backed_worldview_and_survives_restart(tmp_path: Path):
    root = tmp_path / "state"
    mind = ContentMind(root=root, subject_id="content-agent")
    proposition = "Persistently higher long-term yields tighten crypto financial conditions."
    formed = mind.record_research_belief(
        cycle="v2-100", topic="rates-regime",
        question="Do higher long-term yields materially tighten crypto financial conditions?",
        source_evidence=_research_source(), proposition=proposition,
        stance=0.8, reliability=0.75, support=0.8, commit=True,
    )
    assert formed["status"] == "FORMED"
    assert formed["confidence"] > 0
    evidence = mind.session.epistemic.evidence[formed["evidence_id"]]
    assert evidence.provenance_kind == "OBSERVED_TRUSTED"
    assert evidence.claim.startswith("Observed source material:")
    projection = mind.public_projection()
    assert projection["beliefs"][0]["proposition"] == proposition
    assert projection["beliefs"][0]["revision_count"] == 1

    restarted = ContentMind(root=root, subject_id="content-agent")
    assert restarted.public_projection()["beliefs"] == projection["beliefs"]
    duplicate = restarted.record_research_belief(
        cycle="v2-100", topic="rates-regime",
        question="Do higher long-term yields materially tighten crypto financial conditions?",
        source_evidence=_research_source(), proposition=proposition,
        stance=0.8, reliability=0.75, support=0.8, commit=True,
    )
    assert duplicate["status"] == "ALREADY_RECORDED"
    assert restarted.public_projection()["beliefs"][0]["revision_count"] == 1


def test_new_evidence_revises_same_worldview_claim_without_rewriting_history(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    proposition = "Persistently higher long-term yields tighten crypto financial conditions."
    first = mind.record_research_belief(
        cycle="v2-100", topic="rates-regime", question="What does the first regime signal imply?",
        source_evidence=_research_source(), proposition=proposition,
        stance=0.8, reliability=0.7, support=0.8, commit=True,
    )
    second = mind.record_research_belief(
        cycle="v2-200", topic="rates-regime", question="Does later evidence weaken the thesis?",
        source_evidence=_research_source("web:rates-2", "rev-2"), proposition=proposition,
        stance=-0.6, reliability=0.9, support=0.7, commit=True,
    )
    assert first["status"] == "FORMED"
    assert second["status"] == "REVISED"
    assert second["previous"]["stance"] == first["stance"]
    projection = mind.public_projection()
    belief = next(row for row in projection["beliefs"] if row["proposition"] == proposition)
    assert belief["revision_count"] == 2
    changes = [row for row in projection["recent_belief_changes"] if row["proposition"] == proposition]
    assert len(changes) == 2
    assert changes[0]["after"] == changes[1]["before"]


def test_deferred_research_adds_evidence_but_does_not_invent_belief(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    proposition = "ETF flows currently dominate leverage as the marginal Bitcoin driver."
    result = mind.record_research_belief(
        cycle="v2-100", topic="etf-flows", question="Which flow is marginal?",
        source_evidence=_research_source("web:etf-1", "etf-rev-1"), proposition=proposition,
        stance=0.2, reliability=0.3, support=0.55, commit=False,
    )
    assert result["status"] == "DEFERRED"
    assert result["evidence_id"] in mind.session.epistemic.evidence
    assert mind.session.inquiry.inquiries[result["inquiry_id"]]["status"] == "OPEN"
    assert mind.session.worldview.claim(proposition) is None
    assert mind.public_projection()["beliefs"] == []


def test_prediction_outcome_becomes_restart_stable_virtualbrain_experience(tmp_path: Path):
    root = tmp_path / "state"
    mind = ContentMind(root=root, subject_id="content-agent")
    result = mind.observe_prediction_outcome(
        prediction_id="prediction:abc", topic="rates-regime",
        statement="Funding conditions will remain tighter through the horizon.",
        status="REFUTED", confidence=0.72, prediction_error=0.72, reliability=0.9,
        evidence_refs=("prediction:abc", "web:fresh-outcome"),
    )
    assert result["status"] == "OBSERVED"
    snapshot = mind.session.snapshot()
    event = next(row for row in snapshot["autobiography"]["events"] if row["event_id"] == "prediction-outcome:prediction:abc")
    assert event["source"] == "session"
    assert event["provenance_kind"] == "OBSERVED_TRUSTED"
    assert event["outcome"] == 0.0
    reflection = next(row for row in snapshot["development_reflections"] if row["event_id"] == event["event_id"])
    assert reflection["factors"]["prediction_error"] == 0.72
    restarted = ContentMind(root=root, subject_id="content-agent")
    duplicate = restarted.observe_prediction_outcome(
        prediction_id="prediction:abc", topic="rates-regime",
        statement="Funding conditions will remain tighter through the horizon.",
        status="REFUTED", confidence=0.72, prediction_error=0.72, reliability=0.9,
        evidence_refs=("prediction:abc", "web:fresh-outcome"),
    )
    assert duplicate["status"] == "ALREADY_OBSERVED"



def test_method_discovery_can_raise_salience_without_rewriting_preference(tmp_path: Path):
    mind = ContentMind(root=tmp_path / "state", subject_id="content-agent")
    observation = {
        "evidence_id": "web:x-method-1", "topic": "continuous-eval-loop",
        "question": "Would this practitioner evaluation loop improve my own research discipline?",
        "importance": 0.86, "novelty": 0.94,
        "why_now": "The method is concrete enough to investigate but has not been tested by this agent.",
        "learning_kind": "METHOD", "evidence_refs": ("web:x-method-1", "fetch:x-method-1"),
    }
    plan = mind.plan_for_cycle(cycle="v2-method-1", available_topics=("model-evaluation",), world_observations=(observation,))
    update = next(row for row in plan["world_interest_updates"] if row["topic"] == "continuous-eval-loop")
    assert update["learning_kind"] == "METHOD"
    assert update["interest"]["preference"] == 0.5
    assert update["interest"]["familiarity"] > 0.0
    assert plan["world_observations"][0]["learning_kind"] == "METHOD"
    # Discovery may create curiosity/inquiry, but it is not proof that the method works or has been adopted.
    assert all(row["proposition"] != "continuous-eval-loop" for row in mind.public_projection()["beliefs"])

def test_mature_website_outcomes_become_persistent_virtualbrain_strategy_experience(tmp_path: Path):
    root = tmp_path / "state"
    mind = ContentMind(root=root, subject_id="content-agent")
    result = mind.observe_strategy_outcomes([
        {"outcome_id": "o1", "publication_id": "1", "topic": "crypto-market", "profile_name": "evidence-brief",
         "pageviews_24h": 100, "observed_at": "2026-09-17T00:00:00+00:00", "evidence_refs": ["metric:1"]},
        {"outcome_id": "o2", "publication_id": "2", "topic": "crypto-market", "profile_name": "practical-guide",
         "pageviews_24h": 20, "observed_at": "2026-09-17T01:00:00+00:00", "evidence_refs": ["metric:2"]},
        {"outcome_id": "o3", "publication_id": "3", "topic": "crypto-market", "profile_name": "evidence-brief",
         "pageviews_24h": 120, "observed_at": "2026-09-17T02:00:00+00:00", "evidence_refs": ["metric:3"]},
    ])
    assert result["status"] == "OBSERVED"
    assert result["causal_claim"] is False
    context = mind.strategy_context(topic="crypto-market")
    assert context["samples_by_profile"]["evidence-brief"] == 2
    assert context["samples_by_profile"]["practical-guide"] == 1
    assert context["mean_relative_reward_by_profile"]["evidence-brief"] is not None
    restarted = ContentMind(root=root, subject_id="content-agent")
    assert restarted.strategy_context(topic="crypto-market") == context
    duplicate = restarted.observe_strategy_outcomes([
        {"outcome_id": "o1", "publication_id": "1", "topic": "crypto-market", "profile_name": "evidence-brief",
         "pageviews_24h": 100, "observed_at": "2026-09-17T00:00:00+00:00", "evidence_refs": ["metric:1"]},
    ])
    assert duplicate["status"] == "NO_NEW_OUTCOMES"
    assert duplicate["duplicates"] == 1
