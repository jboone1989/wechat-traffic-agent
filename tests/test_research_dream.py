from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from wechat_traffic_agent.research_dream import (
    PROTECTED_INVARIANTS,
    ResearchDreamEngine,
)


NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


def _put(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _world(
    root: Path,
    *,
    suffix: str,
    age_minutes: int,
    status: str,
    occurrences: int,
    repair_kind: str,
    failure_site: str = "content_goal_loop.py:_run>content_service.py:compose",
) -> None:
    problem_id = f"self-problem:{suffix}"
    seen = NOW - timedelta(minutes=age_minutes)
    _put(
        root / "application/self_maintenance/problems" / f"{suffix}.json",
        {
            "problem_id": problem_id,
            "status": "VERIFIED" if status == "ADOPTED_VERIFIED" else "REPAIR_REQUESTED",
            "error_type": "ValueError",
            "failure_site": failure_site,
            "occurrences": occurrences,
            "last_seen_at": seen.isoformat(),
            "repair_proposal": {
                "repair_kind": repair_kind,
                "risk": "LOW",
                "proposal_id": f"proposal:{suffix}",
            },
        },
    )
    _put(
        root / "application/self_maintenance/repair_trials" / f"self-problem-{suffix}.json",
        {
            "schema_version": "self-repair-trial.v1",
            "problem_id": problem_id,
            "status": status,
            "observed_at": seen.isoformat(),
            "repair_kind": repair_kind,
        },
    )


def test_research_dream_promotes_replay_winner_over_recency_incumbent(tmp_path):
    # Recent generated attempts are poor; older known recipes were verifiably good.
    _world(
        tmp_path, suffix="old-a", age_minutes=90,
        status="ADOPTED_VERIFIED", occurrences=5,
        repair_kind="FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT",
    )
    _world(
        tmp_path, suffix="old-b", age_minutes=80,
        status="ADOPTED_VERIFIED", occurrences=4,
        repair_kind="DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION",
    )
    _world(
        tmp_path, suffix="mid", age_minutes=50,
        status="CANDIDATE_COMMITTED", occurrences=3,
        repair_kind="USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION",
    )
    _world(
        tmp_path, suffix="new-bad-a", age_minutes=3,
        status="CAPABILITY_UNAVAILABLE", occurrences=1,
        repair_kind="GENERATED_BOUNDED_EDIT",
    )
    _world(
        tmp_path, suffix="new-bad-b", age_minutes=2,
        status="CAPABILITY_UNAVAILABLE", occurrences=1,
        repair_kind="GENERATED_BOUNDED_EDIT",
    )
    _world(
        tmp_path, suffix="new-bad-c", age_minutes=1,
        status="REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED", occurrences=1,
        repair_kind="GENERATED_BOUNDED_EDIT",
    )

    engine = ResearchDreamEngine(tmp_path)
    before = engine.projection()["incumbent_policy"]
    assert before["origin"] == "LEGACY_RECENCY_FIRST"

    result = engine.dream(now=NOW)

    assert result["status"] == "RESEARCH_DREAMED"
    dream = result["dream"]
    assert dream["replay_world_count"] == 6
    assert dream["winner"]["score"] >= dream["incumbent"]["score"]
    assert dream["winner"]["negative_count"] <= dream["incumbent"]["negative_count"]
    assert dream["decision"] == "START_RESEARCH_POLICY_TRIAL"
    after = result["incumbent_policy"]
    assert after["policy_id"] == before["policy_id"]
    assert after["generation"] == 0
    projection = engine.projection()
    assert projection["researcher_generation"] == 0
    trial = projection["prospective_trial"]
    assert trial["status"] == "ACTIVE"
    assert trial["challenger_policy"]["policy_id"] != before["policy_id"]
    assert trial["challenger_policy"]["online_validation_required"] is True
    assert engine.projection()["protected_invariants"] == list(PROTECTED_INVARIANTS)


def test_research_dream_keeps_incumbent_when_challenger_has_no_gain(tmp_path):
    # When every historical choice succeeds, a more complex policy has no
    # meaningful replay advantage over recency-first.
    for index in range(5):
        _world(
            tmp_path,
            suffix=f"good-{index}",
            age_minutes=10 - index,
            status="ADOPTED_VERIFIED",
            occurrences=1,
            repair_kind="KNOWN_RECIPE",
        )

    engine = ResearchDreamEngine(tmp_path)
    incumbent = engine.projection()["incumbent_policy"]
    result = engine.dream(now=NOW)

    assert result["dream"]["decision"] == "KEEP_INCUMBENT"
    assert result["incumbent_policy"]["policy_id"] == incumbent["policy_id"]
    assert result["incumbent_policy"]["generation"] == 0


def test_promoted_research_policy_changes_only_candidate_order(tmp_path):
    engine = ResearchDreamEngine(tmp_path)
    # Install a bounded policy that strongly favors known recipes.  This is the
    # same state shape produced by a replay promotion.
    state = engine.state
    state["incumbent_policy"] = {
        "policy_id": "research-policy:test-known-recipe",
        "generation": 1,
        "weights": {
            "recency": 0.05,
            "recurrence": 0.05,
            "known_recipe": 0.75,
            "diagnosability": 0.10,
            "diversity": 0.05,
        },
        "origin": "RESEARCH_DREAM_REPLAY:test",
        "replay_promoted": True,
    }
    _put(engine.path, state)

    rows = [
        {
            "problem_id": "self-problem:recent-generated",
            "last_seen_at": NOW.isoformat(),
            "occurrences": 1,
            "error_type": "ValueError",
            "failure_site": "content_service.py:compose",
            "repair_proposal": {
                "repair_kind": "GENERATED_BOUNDED_EDIT",
                "risk": "LOW",
            },
        },
        {
            "problem_id": "self-problem:older-known",
            "last_seen_at": (NOW - timedelta(hours=1)).isoformat(),
            "occurrences": 2,
            "error_type": "ValueError",
            "failure_site": "content_loop.py:_structured",
            "repair_proposal": {
                "repair_kind": "DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION",
                "risk": "LOW",
            },
        },
    ]

    ranked = ResearchDreamEngine(tmp_path).rank_problem_rows(rows)

    assert ranked[0]["problem_id"] == "self-problem:older-known"
    # Ranking never mutates proposal risk or grants a new capability.
    assert all(row["repair_proposal"]["risk"] == "LOW" for row in ranked)


def test_active_prospective_trial_blocks_more_same_pool_challengers(tmp_path):
    for suffix, age, status, kind in (
        ("old-a", 90, "ADOPTED_VERIFIED", "KNOWN_A"),
        ("old-b", 80, "ADOPTED_VERIFIED", "KNOWN_B"),
        ("mid", 50, "CANDIDATE_COMMITTED", "KNOWN_C"),
        ("bad-a", 3, "CAPABILITY_UNAVAILABLE", "GENERATED_BOUNDED_EDIT"),
        ("bad-b", 2, "CAPABILITY_UNAVAILABLE", "GENERATED_BOUNDED_EDIT"),
        ("bad-c", 1, "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED", "GENERATED_BOUNDED_EDIT"),
    ):
        _world(
            tmp_path,
            suffix=suffix,
            age_minutes=age,
            status=status,
            occurrences=3 if status == "ADOPTED_VERIFIED" else 1,
            repair_kind=kind,
        )

    engine = ResearchDreamEngine(tmp_path)
    first = engine.dream(now=NOW)
    assert first["dream"]["decision"] == "START_RESEARCH_POLICY_TRIAL"
    first_trial = first["dream"]["prospective_trial_id"]

    blocked = ResearchDreamEngine(tmp_path).dream(now=NOW + timedelta(minutes=1))
    assert blocked["dream"]["decision"] == "WAIT_FOR_PROSPECTIVE_RESULTS"
    assert blocked["dream"]["prospective_trial_id"] == first_trial
    assert blocked["dream"]["researcher_generation_after"] == 0
    assert blocked["incumbent_policy"]["generation"] == 0


def test_researcher_promotes_only_after_future_online_non_degradation(tmp_path):
    for suffix, age, status, kind in (
        ("old-a", 90, "ADOPTED_VERIFIED", "KNOWN_A"),
        ("old-b", 80, "ADOPTED_VERIFIED", "KNOWN_B"),
        ("mid", 50, "CANDIDATE_COMMITTED", "KNOWN_C"),
        ("bad-a", 3, "CAPABILITY_UNAVAILABLE", "GENERATED_BOUNDED_EDIT"),
        ("bad-b", 2, "CAPABILITY_UNAVAILABLE", "GENERATED_BOUNDED_EDIT"),
        ("bad-c", 1, "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED", "GENERATED_BOUNDED_EDIT"),
    ):
        _world(
            tmp_path,
            suffix=suffix,
            age_minutes=age,
            status=status,
            occurrences=3 if status == "ADOPTED_VERIFIED" else 1,
            repair_kind=kind,
        )

    engine = ResearchDreamEngine(tmp_path)
    initial = engine.dream(now=NOW)
    trial = initial["incumbent_policy"]
    assert initial["dream"]["decision"] == "START_RESEARCH_POLICY_TRIAL"
    trial_state = engine.projection()["prospective_trial"]
    trial_id = trial_state["trial_id"]
    challenger_id = trial_state["challenger_policy"]["policy_id"]

    future_rows = []
    arm_ids = {"BASELINE": [], "CANDIDATE": []}
    index = 0
    while min(len(arm_ids["BASELINE"]), len(arm_ids["CANDIDATE"])) < 2:
        problem_id = f"self-problem:future-{index}"
        arm = engine._trial_arm(problem_id, trial_id)
        if len(arm_ids[arm]) < 2:
            arm_ids[arm].append(problem_id)
            future_rows.append({
                "problem_id": problem_id,
                "last_seen_at": (NOW + timedelta(minutes=1)).isoformat(),
                "occurrences": 1,
                "error_type": "ValueError",
                "failure_site": "content_goal_loop.py:_run>content_service.py:compose",
                "repair_proposal": {
                    "repair_kind": "USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION",
                    "risk": "LOW",
                },
            })
        index += 1

    ResearchDreamEngine(tmp_path).rank_problem_rows(
        future_rows,
        now=NOW + timedelta(minutes=1),
    )
    assigned = ResearchDreamEngine(tmp_path).projection()["prospective_trial"]["assignments"]
    assert {assigned[pid] for pid in assigned} == {"BASELINE", "CANDIDATE"}

    for problem_id, arm in assigned.items():
        safe = problem_id.replace(":", "-")
        _put(
            tmp_path / "application/self_maintenance/repair_trials" / f"{safe}.json",
            {
                "schema_version": "self-repair-trial.v1",
                "problem_id": problem_id,
                "status": (
                    "ADOPTED_VERIFIED"
                    if arm == "CANDIDATE"
                    else "CANDIDATE_COMMITTED"
                ),
                "observed_at": (NOW + timedelta(minutes=2)).isoformat(),
            },
        )

    settled = ResearchDreamEngine(tmp_path).dream(
        now=NOW + timedelta(minutes=3)
    )
    projection = ResearchDreamEngine(tmp_path).projection()
    assert projection["prospective_trial"]["status"] == "PROMOTED"
    assert projection["researcher_generation"] == 1
    assert projection["incumbent_policy"]["policy_id"] == challenger_id
    assert projection["incumbent_policy"]["online_validation_required"] is False
    assert settled["dream"]["decision"] == "WAIT_FOR_PROSPECTIVE_RESULTS"


def test_legacy_replay_only_incumbent_is_demoted_into_prospective_trial(tmp_path):
    engine = ResearchDreamEngine(tmp_path)
    baseline = dict(engine.state["incumbent_policy"])
    replay_policy = {
        "policy_id": "research-policy:legacy-replay-v3",
        "generation": 3,
        "weights": {
            "recency": 0.20,
            "recurrence": 0.30,
            "known_recipe": 0.25,
            "diagnosability": 0.15,
            "diversity": 0.10,
        },
        "origin": "RESEARCH_DREAM_REPLAY:research-dream:3",
        "replay_promoted": True,
        "online_validation_required": True,
    }
    state = engine.state
    state["incumbent_history"] = [
        baseline,
        {
            **replay_policy,
            "policy_id": "research-policy:legacy-replay-v1",
            "generation": 1,
            "origin": "RESEARCH_DREAM_REPLAY:research-dream:1",
        },
    ]
    state["incumbent_policy"] = replay_policy
    state["researcher_generation"] = 3
    state["promotion_count"] = 3
    state["replay_pool_hash"] = "sha256:legacy-pool"
    state["dreams"] = [{
        "dream_id": "research-dream:3",
        "incumbent": {"score": 0.55},
        "winner": {"score": 0.67},
        "decision": "PROMOTE_RESEARCH_POLICY_BOUNDED",
    }]
    _put(engine.path, state)

    migrated = ResearchDreamEngine(tmp_path)
    projection = migrated.projection()

    assert projection["incumbent_policy"]["policy_id"] == baseline["policy_id"]
    assert projection["researcher_generation"] == 0
    assert projection["promotion_count"] == 0
    assert projection["reversion_count"] == 1
    trial = projection["prospective_trial"]
    assert trial["status"] == "ACTIVE"
    assert trial["challenger_policy"]["policy_id"] == replay_policy["policy_id"]
    assert trial["challenger_policy"]["online_validation_required"] is True
    migration = migrated.state["offline_replay_migration"]
    assert migration["status"] == "MIGRATED"
    assert migration["invalidated_replay_generation"] == 3
    assert migration["restored_generation"] == 0
    assert migration["replay_only_promotion_count"] == 3
    assert migration["history_preserved"] is True


def test_researcher_kills_challenger_after_online_regression(tmp_path):
    for suffix, age, status, kind in (
        ("old-a", 90, "ADOPTED_VERIFIED", "KNOWN_A"),
        ("old-b", 80, "ADOPTED_VERIFIED", "KNOWN_B"),
        ("mid", 50, "CANDIDATE_COMMITTED", "KNOWN_C"),
        ("bad-a", 3, "CAPABILITY_UNAVAILABLE", "GENERATED_BOUNDED_EDIT"),
        ("bad-b", 2, "CAPABILITY_UNAVAILABLE", "GENERATED_BOUNDED_EDIT"),
        ("bad-c", 1, "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED", "GENERATED_BOUNDED_EDIT"),
    ):
        _world(
            tmp_path,
            suffix=suffix,
            age_minutes=age,
            status=status,
            occurrences=3 if status == "ADOPTED_VERIFIED" else 1,
            repair_kind=kind,
        )

    engine = ResearchDreamEngine(tmp_path)
    initial = engine.dream(now=NOW)
    assert initial["dream"]["decision"] == "START_RESEARCH_POLICY_TRIAL"
    original_incumbent = initial["incumbent_policy"]["policy_id"]
    trial = engine.projection()["prospective_trial"]
    trial_id = trial["trial_id"]

    future_rows = []
    arm_ids = {"BASELINE": [], "CANDIDATE": []}
    index = 0
    while min(len(arm_ids["BASELINE"]), len(arm_ids["CANDIDATE"])) < 2:
        problem_id = f"self-problem:regression-{index}"
        arm = engine._trial_arm(problem_id, trial_id)
        if len(arm_ids[arm]) < 2:
            arm_ids[arm].append(problem_id)
            future_rows.append({
                "problem_id": problem_id,
                "last_seen_at": (NOW + timedelta(minutes=1)).isoformat(),
                "occurrences": 1,
                "error_type": "ValueError",
                "failure_site": "content_goal_loop.py:_run>content_service.py:compose",
                "repair_proposal": {"repair_kind": "KNOWN_RECIPE", "risk": "LOW"},
            })
        index += 1
    ResearchDreamEngine(tmp_path).rank_problem_rows(
        future_rows, now=NOW + timedelta(minutes=1)
    )
    assigned = ResearchDreamEngine(tmp_path).projection()["prospective_trial"]["assignments"]
    for problem_id, arm in assigned.items():
        safe = problem_id.replace(":", "-")
        _put(
            tmp_path / "application/self_maintenance/repair_trials" / f"{safe}.json",
            {
                "schema_version": "self-repair-trial.v1",
                "problem_id": problem_id,
                "status": (
                    "CAPABILITY_UNAVAILABLE"
                    if arm == "CANDIDATE"
                    else "ADOPTED_VERIFIED"
                ),
                "observed_at": (NOW + timedelta(minutes=2)).isoformat(),
            },
        )

    ResearchDreamEngine(tmp_path).dream(now=NOW + timedelta(minutes=3))
    projection = ResearchDreamEngine(tmp_path).projection()
    assert projection["prospective_trial"]["status"] == "REJECTED"
    assert projection["prospective_trial"]["decision"] == (
        "KILL_CHALLENGER_AFTER_PROSPECTIVE_REGRESSION"
    )
    assert projection["researcher_generation"] == 0
    assert projection["incumbent_policy"]["policy_id"] == original_incumbent
    assert projection["online_rejection_count"] == 1


def test_wakeful_public_snapshot_exposes_researcher_generation(tmp_path):
    from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    dreaming = runtime.public_snapshot()["dreaming"]

    assert "research_dream" in dreaming
    assert dreaming["research_dream"]["researcher_generation"] == 0
    assert "incumbent_policy" in dreaming["research_dream"]
