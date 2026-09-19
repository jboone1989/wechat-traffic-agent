from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

from agent_runtime.backends import FixtureAgentBackend
from agent_runtime.clock import FrozenClock
from agent_runtime.goals import MissionStatus
from agent_runtime.runtime import AgentRuntime

from wechat_traffic_agent.x_growth_mission import (
    MISSION_CONSTRAINTS,
    XGrowthMissionCoordinator,
    follower_count_from_user,
    x_growth_mission_context,
)


NOW = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)


def _advice(request):
    if "roadmap" in request.task.lower():
        return json.dumps({
            "rationale": "Measure first, test useful content, then evaluate and adapt.",
            "steps": [
                {
                    "key": "observe",
                    "title": "Measure baseline and audience",
                    "objective": "Keep current follower and audience evidence fresh.",
                    "estimated_hours": 1,
                    "depends_on": [],
                    "resource_tags": ["x-observation"],
                    "exit_criteria": "A dated follower observation exists.",
                },
                {
                    "key": "experiment",
                    "title": "Run bounded growth experiment",
                    "objective": "Test one evidence-compatible content strategy.",
                    "estimated_hours": 24,
                    "depends_on": ["observe"],
                    "resource_tags": ["x-content"],
                    "exit_criteria": "A 24h outcome observation exists.",
                },
            ],
        })
    return json.dumps(
        {
            "statement": (
                "Evidence-rich build notes may convert relevant technical readers "
                "into durable followers better than generic commentary."
            ),
            "strategy": (
                "For the next bounded window, prefer one concrete evidence-backed "
                "build/result angle when the normal content loop has a genuinely "
                "useful candidate, then compare follower movement with required pace."
            ),
            "expected_effect": "Improve qualified follower growth without increasing spam.",
            "risks": [
                "Over-optimizing social framing could reduce information quality",
                "Posting too frequently could damage reputation",
            ],
            "confidence": 0.55,
        }
    )


def _runtime(tmp_path, *, backend=True):
    clock = FrozenClock(NOW)
    model = FixtureAgentBackend(_advice) if backend else None
    runtime = AgentRuntime.bootstrap(
        tmp_path / "domain-runtime",
        clock=clock,
        agent_backend=model,
    )
    return runtime, clock, model


def _user(followers=2007):
    return {
        "id": "me",
        "username": "ferro",
        "name": "Ferro",
        "public_metrics": {
            "followers_count": followers,
            "following_count": 421,
            "tweet_count": 120,
        },
    }
def test_follower_count_requires_real_public_metric():
    assert follower_count_from_user(_user(2007)) == 2007
    assert follower_count_from_user({"public_metrics": {}}) is None
    assert follower_count_from_user({"public_metrics": {"followers_count": True}}) is None
    assert follower_count_from_user({}) is None


def test_first_official_observation_creates_mission_and_proposes_experiment(tmp_path):
    runtime, _clock, backend = _runtime(tmp_path)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        target=5000,
        duration_days=30,
        experiment_hours=24,
        enabled=True,
    )

    result = asyncio.run(
        coordinator.observe_identity(
            user=_user(2007),
            identity_receipt_id="receipt:x.identity:1",
            now=NOW,
        )
    )

    assert result["status"] == "ACTIVE"
    assert result["baseline"] == 2007.0
    assert result["target"] == 5000.0
    assert result["current"] == 2007.0
    assert result["policy_mode"] == "OFFICIAL_X_API_ONLY"
    assert result["publication_authority"] is False
    assert result["causal_claim"] is False
    assert result["roadmap_transition"]["status"] == "PLANNED"
    assert result["roadmap"]["version"] == 1
    assert len(result["roadmap"]["steps"]) == 2
    assert result["experiment_transition"]["status"] == "PROPOSED"
    assert result["active_experiment"]["status"] == "PROPOSED"
    assert result["active_experiment"]["started_at"] is None
    assert result["active_experiment"]["measurement_window_hours"] == 24.0
    assert len(backend.requests) == 2
    assert backend.requests[0].token_budget == 1800

    mission = runtime.external_missions[-1]
    assert mission.status is MissionStatus.ACTIVE
    assert mission.deadline == NOW + timedelta(days=30)
    assert mission.baseline == 2007.0
    assert "obey current X platform and automation policy" in mission.constraints
    assert "do not use artificial account-metric inflation" in mission.constraints
    assert set(MISSION_CONSTRAINTS) <= set(mission.constraints)

    persisted = x_growth_mission_context(tmp_path)
    assert persisted["mission_id"] == mission.mission_id
    assert persisted["generation_authority"] == "MISSION_STRATEGY_ONLY"
def test_missing_official_follower_metric_does_not_invent_baseline(tmp_path):
    runtime, _clock, _backend = _runtime(tmp_path)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        enabled=True,
    )

    result = asyncio.run(
        coordinator.observe_identity(
            user={"id": "me", "username": "ferro"},
            identity_receipt_id="receipt:x.identity:missing-metrics",
            now=NOW,
        )
    )

    assert result["status"] == "FOLLOWER_METRIC_UNAVAILABLE"
    assert runtime.external_missions == []
    assert result["publication_authority"] is False


def test_no_advisory_backend_keeps_mission_active_without_fake_strategy(tmp_path):
    runtime, _clock, _backend = _runtime(tmp_path, backend=False)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        enabled=True,
    )

    result = asyncio.run(
        coordinator.observe_identity(
            user=_user(2007),
            identity_receipt_id="receipt:x.identity:2",
            now=NOW,
        )
    )

    assert result["status"] == "ACTIVE"
    assert result["roadmap_transition"] == {"status": "BACKEND_UNAVAILABLE"}
    assert result["experiment_transition"] == {"status": "BACKEND_UNAVAILABLE"}
    assert result["active_experiment"] is None
    assert runtime.external_missions[-1].status is MissionStatus.ACTIVE
def test_mature_experiment_settles_from_real_follower_change_and_replans(tmp_path):
    runtime, clock, backend = _runtime(tmp_path)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        target=5000,
        duration_days=30,
        experiment_hours=24,
        enabled=True,
    )
    first = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:start",
            now=NOW,
        )
    )
    first_experiment = first["active_experiment"]["experiment_id"]
    started = coordinator.start_experiment_from_publication(
        publication_receipt_id="receipt:x.post:first",
        post_id="post-1",
        now=clock.now(),
    )
    assert started["status"] == "STARTED"
    assert started["experiment_id"] == first_experiment

    clock.advance(timedelta(hours=24).total_seconds())
    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2100),
            identity_receipt_id="receipt:x.identity:day1",
            now=clock.now(),
        )
    )

    assert second["current"] == 2100.0
    assert second["settlement"]["status"] == "SETTLED"
    assert second["settlement"]["experiment_id"] == first_experiment
    assert second["settlement"]["conclusion"] == "SUPPORTED"
    assert second["settlement"]["actual_delta"] == 100.0
    assert second["causal_claim"] is False
    assert second["active_experiment"]["experiment_id"] != first_experiment
    assert second["active_experiment"]["status"] == "PROPOSED"
    assert second["active_experiment"]["started_at"] is None
    assert len(backend.requests) == 3

    settled = next(
        row for row in runtime.missions.experiments
        if row.experiment_id == first_experiment
    )
    hypothesis = next(
        row for row in runtime.learning.hypotheses
        if row.hypothesis_id == settled.hypothesis_id
    )
    assert hypothesis.confidence > 0.55
    assert "observational feedback" in settled.notes


def test_proposed_experiment_does_not_settle_before_real_publication(tmp_path):
    runtime, clock, backend = _runtime(tmp_path)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        target=5000,
        duration_days=30,
        experiment_hours=24,
        enabled=True,
    )
    first = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:proposed",
            now=NOW,
        )
    )
    experiment_id = first["active_experiment"]["experiment_id"]
    clock.advance(timedelta(hours=48).total_seconds())
    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2010),
            identity_receipt_id="receipt:x.identity:still-proposed",
            now=clock.now(),
        )
    )

    assert second["settlement"] is None
    assert second["active_experiment"]["experiment_id"] == experiment_id
    assert second["active_experiment"]["status"] == "PROPOSED"
    experiment = next(
        row for row in runtime.missions.experiments
        if row.experiment_id == experiment_id
    )
    assert experiment.started_at is None
    assert len(backend.requests) == 2


def test_successful_publication_is_the_experiment_start_boundary(tmp_path):
    runtime, clock, _backend = _runtime(tmp_path)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        target=5000,
        enabled=True,
    )
    first = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:publication-boundary",
            now=NOW,
        )
    )
    experiment_id = first["active_experiment"]["experiment_id"]

    started = coordinator.start_experiment_from_publication(
        publication_receipt_id="receipt:x.post:42",
        post_id="42",
        now=clock.now(),
    )

    assert started["status"] == "STARTED"
    assert started["experiment_id"] == experiment_id
    experiment = next(
        row for row in runtime.missions.experiments
        if row.experiment_id == experiment_id
    )
    assert experiment.started_at == NOW
    assert "receipt:x.post:42" in experiment.evidence_refs
    assert "x-post:42" in experiment.evidence_refs
    persisted = x_growth_mission_context(tmp_path)
    assert persisted["active_experiment"]["status"] == "RUNNING"
    assert persisted["active_experiment"]["x_post_id"] == "42"
