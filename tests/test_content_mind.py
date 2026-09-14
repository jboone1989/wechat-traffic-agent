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
