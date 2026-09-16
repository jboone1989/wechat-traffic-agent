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
    assert "audience outcomes are not connected" in projection["learning_boundary"]


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
