from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from agent_runtime.backends import FixtureAgentBackend
from agent_runtime.clock import FrozenClock
from agent_runtime.goals import MissionExperimentStatus, MissionStatus
from agent_runtime.runtime import AgentRuntime

from wechat_traffic_agent.x_growth_mission import (
    MISSION_CONSTRAINTS,
    MISSION_PLANNING_CONTEXT_REF,
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
            "measurement_window_hours": 24.0,
            "settlement_metric": "x.followers",
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
    assert result["active_experiment"]["settlement_metric"] == "x.followers"
    assert len(backend.requests) == 2
    assert backend.requests[0].token_budget == 1800
    assert backend.requests[0].context["revision_requested"] is False
    assert (
        backend.requests[0].context["application_context"]["measurement"][
            "historical_post_level_follower_attribution"
        ]
        == "UNAVAILABLE"
    )
    assert backend.requests[1].context["revision_requested"] is False
    experiment_measurement = backend.requests[1].context["application_context"][
        "measurement"
    ]
    assert experiment_measurement["post_level_profile_visits"] == "UNAVAILABLE"
    assert experiment_measurement["post_level_follows"] == "UNAVAILABLE"
    assert experiment_measurement["causal_attribution"].startswith("UNAVAILABLE")
    experiment_contract = backend.requests[1].context["application_context"][
        "experiment_contract"
    ]
    assert experiment_contract == {
        "measurement_window_hours": 24.0,
        "settlement_metric": "x.followers",
    }

    mission = runtime.external_missions[-1]
    assert mission.status is MissionStatus.ACTIVE
    assert mission.deadline == NOW + timedelta(days=30)
    assert mission.baseline == 2007.0
    assert "obey current X platform and automation policy" in mission.constraints
    assert "do not use artificial account-metric inflation" in mission.constraints
    assert set(MISSION_CONSTRAINTS) <= set(mission.constraints)
    roadmap = runtime.missions.latest_roadmap(mission.mission_id)
    assert roadmap is not None
    assert MISSION_PLANNING_CONTEXT_REF in roadmap.evidence_refs

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


def test_legacy_roadmap_is_replanned_once_with_real_x_capability_context(tmp_path):
    runtime, _clock, backend = _runtime(tmp_path)
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
            identity_receipt_id="receipt:x.identity:legacy-roadmap",
            now=NOW,
        )
    )
    mission_id = first["mission_id"]
    original = runtime.missions.latest_roadmap(mission_id)
    assert original is not None
    original.evidence_refs = [
        ref for ref in original.evidence_refs
        if ref != MISSION_PLANNING_CONTEXT_REF
    ]
    runtime.persist()

    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:legacy-roadmap-2",
            now=NOW,
        )
    )

    assert second["roadmap_transition"]["status"] == "REPLANNED"
    assert second["roadmap"]["version"] == 2
    revised = runtime.missions.latest_roadmap(mission_id)
    assert revised is not None
    assert revised.version == 2
    assert MISSION_PLANNING_CONTEXT_REF in revised.evidence_refs
    assert len(backend.requests) == 3
    replan_request = backend.requests[-1]
    assert replan_request.context["revision_requested"] is True
    assert replan_request.context["current_roadmap"]["version"] == 1
    measurement = replan_request.context["application_context"]["measurement"]
    assert measurement["historical_post_level_follower_attribution"] == "UNAVAILABLE"
    assert measurement["retrospective_90_day_new_follower_uplift"] == "UNAVAILABLE"

    third = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:legacy-roadmap-3",
            now=NOW,
        )
    )
    assert third["roadmap_transition"]["status"] == "ACTIVE"
    assert third["roadmap"]["version"] == 2
    assert len(backend.requests) == 3


def test_ready_baseline_guardrail_step_completes_with_real_artifact(tmp_path):
    runtime, _clock, _backend = _runtime(tmp_path)
    coordinator = XGrowthMissionCoordinator(
        runtime=runtime,
        state_root=tmp_path,
        target=5000,
        enabled=True,
    )
    first = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:baseline-step-1",
            now=NOW,
        )
    )
    mission_id = first["mission_id"]
    roadmap = runtime.set_mission_roadmap(
        mission_id,
        steps=[{
            "key": "baseline",
            "title": "Baseline, compliance guardrails, and measurement readiness",
            "objective": (
                "Verify the real follower count, ratify protective guardrails, "
                "and enable prospective snapshots."
            ),
            "estimated_hours": 1,
            "depends_on": [],
            "resource_tags": [
                "x_api_oauth_read",
                "analytics",
                "legal_policy_review",
                "brand_review",
                "budget_low",
            ],
            "exit_criteria": (
                "A dated baseline guardrail report exists with prospective follower "
                "snapshots, the 72-hour decline hard stop, prohibited tactics, and "
                "official post public_metrics when available."
            ),
        }],
        evidence_refs=[MISSION_PLANNING_CONTEXT_REF],
    )
    runtime.persist()

    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:baseline-step-2",
            now=NOW,
        )
    )

    execution = second["roadmap_execution"]
    assert execution["status"] == "COMPLETED"
    assert execution["step_id"] == roadmap.steps[0].step_id
    report_path = Path(execution["artifact_path"])
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["baseline"]["value"] == 2000.0
    assert report["measurement"]["prospective_follower_snapshots"] == "ENABLED"
    assert report["measurement"]["causal_attribution"] is False
    assert report["guardrails"]["hard_stop"]["active"] is False
    assert report["guardrails"]["hard_stop"]["decline_threshold"] == 0.02
    assert report["guardrails"]["ratification"]["new_human_approval_event"] is False
    completed = runtime.missions.get_step(roadmap.steps[0].step_id)
    assert completed.completed_at == NOW
    assert execution["artifact_id"] in completed.evidence_refs
    assert second["roadmap"]["steps"][0]["status"] == "COMPLETED"


def test_verified_follower_drop_over_two_percent_activates_publication_hard_stop(tmp_path):
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
            identity_receipt_id="receipt:x.identity:hard-stop-start",
            now=NOW,
        )
    )
    assert first["hard_stop"]["active"] is False

    clock.advance(timedelta(hours=24).total_seconds())
    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(1959),
            identity_receipt_id="receipt:x.identity:hard-stop-drop",
            now=clock.now(),
        )
    )

    assert second["hard_stop"]["active"] is True
    assert second["hard_stop"]["peak"] == 2000.0
    assert second["hard_stop"]["current"] == 1959.0
    assert second["hard_stop"]["decline_ratio"] > 0.02
    guard = coordinator.publication_guardrail()
    assert guard["allowed"] is False
    assert guard["status"] == "HARD_STOP_ACTIVE"


def test_verified_publication_starts_ready_content_step_and_records_progress(tmp_path):
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
            identity_receipt_id="receipt:x.identity:progress-start",
            now=NOW,
        )
    )
    mission_id = first["mission_id"]
    experiment_id = first["active_experiment"]["experiment_id"]
    roadmap = runtime.set_mission_roadmap(
        mission_id,
        steps=[
            {
                "key": "baseline",
                "title": "Baseline, compliance guardrails, and measurement readiness",
                "objective": "Prepare the mission measurement and guardrails.",
                "estimated_hours": 1,
                "depends_on": [],
                "resource_tags": [
                    "x_api_oauth_read",
                    "analytics",
                    "legal_policy_review",
                    "brand_review",
                    "budget_low",
                ],
                "exit_criteria": (
                    "A baseline report exists with prospective follower snapshots "
                    "and approved guardrails."
                ),
            },
            {
                "key": "publish",
                "title": "Experiment A: authority and insight content",
                "objective": "Run bounded content publication under mission constraints.",
                "estimated_hours": 24,
                "depends_on": ["baseline"],
                "resource_tags": [
                    "content_creation",
                    "x_publication_gated",
                    "x_api_oauth_write",
                    "analytics",
                ],
                "exit_criteria": "Publication and prospective outcome evidence are stored.",
            },
        ],
        evidence_refs=[MISSION_PLANNING_CONTEXT_REF],
    )
    runtime.persist()

    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:progress-ready",
            now=NOW,
        )
    )
    assert second["roadmap_execution"]["status"] == "COMPLETED"
    publication_step = roadmap.steps[1]
    schedule = runtime.mission_portfolio_schedule(max_parallelism=3)
    scheduled = next(item for item in schedule.items if item.step_id == publication_step.step_id)
    assert scheduled.status.value == "READY"

    started = coordinator.start_experiment_from_publication(
        publication_receipt_id="receipt:x.post:progress-1",
        post_id="progress-1",
        now=clock.now(),
    )
    assert started["status"] == "STARTED"
    assert started["experiment_id"] == experiment_id
    transition = started["roadmap_step_transition"]
    assert transition["status"] == "STARTED"
    assert transition["step_id"] == publication_step.step_id
    assert transition["publication_count"] == 1

    current_step = runtime.missions.get_step(publication_step.step_id)
    assert current_step.started_at == NOW
    assert current_step.completed_at is None
    assert "receipt:x.post:progress-1" in current_step.evidence_refs
    assert "x-post:progress-1" in current_step.evidence_refs

    repeated = coordinator.start_experiment_from_publication(
        publication_receipt_id="receipt:x.post:progress-1",
        post_id="progress-1",
        now=clock.now(),
    )
    assert repeated["status"] == "ALREADY_RUNNING"
    assert repeated["roadmap_step_transition"]["status"] == "RECORDED"
    assert repeated["roadmap_step_transition"]["publication_count"] == 1

    progress_path = (
        tmp_path
        / "content"
        / "x"
        / "missions"
        / mission_id
        / "roadmap_progress.json"
    )
    progress = json.loads(progress_path.read_text())
    assert progress["roadmap_id"] == roadmap.roadmap_id
    assert progress["steps"][publication_step.step_id]["publication_count"] == 1


def test_legacy_proposed_experiment_is_replanned_with_measurement_context(tmp_path):
    runtime, _clock, backend = _runtime(tmp_path)
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
            identity_receipt_id="receipt:x.identity:legacy-experiment-1",
            now=NOW,
        )
    )
    original_id = first["active_experiment"]["experiment_id"]
    original = next(
        row
        for row in runtime.missions.experiments
        if row.experiment_id == original_id
    )
    original.evidence_refs = [
        ref
        for ref in original.evidence_refs
        if ref != MISSION_PLANNING_CONTEXT_REF
    ]
    original.strategy = (
        "Compare profile visits, post-level follows, and attributed follower "
        "conversion after each post."
    )
    runtime.persist()

    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:legacy-experiment-2",
            now=NOW,
        )
    )

    assert second["experiment_transition"]["status"] == "REPLANNED"
    assert second["active_experiment"]["experiment_id"] != original_id
    assert second["active_experiment"]["status"] == "PROPOSED"
    assert original.status is MissionExperimentStatus.CANCELLED
    replacement = next(
        row
        for row in runtime.missions.experiments
        if row.experiment_id == second["active_experiment"]["experiment_id"]
    )
    assert MISSION_PLANNING_CONTEXT_REF in replacement.evidence_refs
    assert replacement.measurement_window_hours == 24.0
    assert replacement.settlement_metric == "x.followers"
    assert len(backend.requests) == 3
    request = backend.requests[-1]
    assert request.context["revision_requested"] is True
    measurement = request.context["application_context"]["measurement"]
    assert measurement["post_level_profile_visits"] == "UNAVAILABLE"
    assert measurement["post_level_follows"] == "UNAVAILABLE"
    assert measurement["causal_attribution"].startswith("UNAVAILABLE")


def test_running_experiment_is_not_replanned_when_context_marker_is_missing(tmp_path):
    runtime, _clock, backend = _runtime(tmp_path)
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
            identity_receipt_id="receipt:x.identity:running-experiment-1",
            now=NOW,
        )
    )
    experiment_id = first["active_experiment"]["experiment_id"]
    experiment = next(
        row
        for row in runtime.missions.experiments
        if row.experiment_id == experiment_id
    )
    experiment.evidence_refs = [
        ref
        for ref in experiment.evidence_refs
        if ref != MISSION_PLANNING_CONTEXT_REF
    ]
    runtime.start_mission_experiment(experiment_id)
    runtime.persist()

    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:running-experiment-2",
            now=NOW,
        )
    )

    assert second["active_experiment"]["experiment_id"] == experiment_id
    assert second["active_experiment"]["status"] == "RUNNING"
    assert second["experiment_transition"]["status"] == "RUNNING"
    assert experiment.status is MissionExperimentStatus.RUNNING
    assert len(backend.requests) == 2


def test_context_marked_proposed_experiment_is_replanned_when_contract_is_missing(tmp_path):
    runtime, _clock, backend = _runtime(tmp_path)
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
            identity_receipt_id="receipt:x.identity:contract-legacy-1",
            now=NOW,
        )
    )
    original_id = first["active_experiment"]["experiment_id"]
    original = next(
        row for row in runtime.missions.experiments
        if row.experiment_id == original_id
    )
    assert MISSION_PLANNING_CONTEXT_REF in original.evidence_refs
    original.measurement_window_hours = None
    original.settlement_metric = None
    original.strategy = (
        "Run a 14-day posting experiment even though Runtime settles after one day."
    )
    runtime.persist()

    second = asyncio.run(
        coordinator.observe_identity(
            user=_user(2000),
            identity_receipt_id="receipt:x.identity:contract-legacy-2",
            now=NOW,
        )
    )

    assert second["experiment_transition"]["status"] == "REPLANNED"
    assert original.status is MissionExperimentStatus.CANCELLED
    replacement = next(
        row for row in runtime.missions.experiments
        if row.experiment_id == second["active_experiment"]["experiment_id"]
    )
    assert replacement.experiment_id != original_id
    assert replacement.measurement_window_hours == 24.0
    assert replacement.settlement_metric == "x.followers"
    assert second["active_experiment"]["measurement_window_hours"] == 24.0
    assert second["active_experiment"]["settlement_metric"] == "x.followers"
    request = backend.requests[-1]
    assert request.context["revision_requested"] is True
    assert request.context["application_context"]["experiment_contract"] == {
        "measurement_window_hours": 24.0,
        "settlement_metric": "x.followers",
    }
