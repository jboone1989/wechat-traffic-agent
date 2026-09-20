from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_runtime.clock import FrozenClock
from agent_runtime.runtime import AgentRuntime

from wechat_traffic_agent.content_action_prediction import (
    ActionPredictionOutcome,
    bind_action_prediction,
    forecast_strategy_candidates,
)
from wechat_traffic_agent.content_strategy import bind_content_strategy
from wechat_traffic_agent.evolution_bridge import (
    ContentEvolutionBridge,
    PROFILE_IMPL_PREFIX,
    STRATEGY_BASELINE_IMPL,
    STRATEGY_CAPABILITY_ID,
)
from wechat_traffic_agent.traffic_learning import TopicTrafficSignal


NOW = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)


def _traffic(topic: str) -> TopicTrafficSignal:
    return TopicTrafficSignal(
        topic=topic,
        article_count=0,
        orphan_article_count=0,
        search_rows=0,
        impressions=0,
        clicks=0,
        ctr=None,
        average_position=None,
        pageview_samples_24h=0,
        mean_pageviews_24h=None,
        trend_hits=0,
        demand_score=0.0,
        priority_score=0.0,
        exposure_status="UNKNOWN",
    )


def _write_history(
    root: Path,
    *,
    cycle: str,
    profile: str,
    pageviews: float,
    created_at: datetime,
) -> ActionPredictionOutcome:
    folder = root / "content" / "continuous" / "cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    forecasts = forecast_strategy_candidates(
        state_root=root,
        account="https://example.test",
        topic="evolution-test",
        as_of=created_at,
        mind_strategy_context=None,
    )
    strategy = bind_content_strategy(
        folder / "content_strategy.json",
        state_root=root,
        account="https://example.test",
        cycle=cycle,
        topic="evolution-test",
        traffic_signal=_traffic("evolution-test"),
        decided_at=created_at,
        preferred_profile_name=profile,
        action_forecasts=forecasts,
    )
    prediction = bind_action_prediction(
        folder / "action_prediction.json",
        cycle=cycle,
        topic="evolution-test",
        strategy_id=strategy.strategy_id,
        selected_profile_name=profile,
        created_at=created_at,
        candidate_forecasts=forecasts,
    )
    outcome = ActionPredictionOutcome(
        prediction_id=prediction.prediction_id,
        publication_id=f"pub-{cycle}",
        topic="evolution-test",
        profile_name=profile,
        window="24h",
        maturity="MATURE",
        expected_pageviews=8.0,
        actual_pageviews=pageviews,
        prediction_error=abs(pageviews - 8.0) / (pageviews + 9.0),
        reliability=1.0,
        observed_at=created_at + timedelta(hours=24),
        evidence_refs=(f"world:{cycle}", f"website-publication:pub-{cycle}"),
    )
    out = folder / "action_prediction_outcomes" / "24h.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(outcome.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return outcome


def test_historical_real_outcomes_create_shadow_challenger_not_direct_promotion(tmp_path):
    runtime_root = tmp_path / "runtime"
    state_root = tmp_path / "state"
    rt = AgentRuntime.bootstrap(runtime_root, clock=FrozenClock(NOW))
    bridge = ContentEvolutionBridge(
        runtime=rt,
        state_root=state_root,
        account="https://example.test",
    )
    base = NOW - timedelta(days=3)
    _write_history(
        state_root, cycle="base-a", profile="follow-up-update",
        pageviews=5.0, created_at=base,
    )
    _write_history(
        state_root, cycle="base-b", profile="follow-up-update",
        pageviews=6.0, created_at=base + timedelta(minutes=1),
    )
    _write_history(
        state_root, cycle="challenger-a", profile="comparison-guide",
        pageviews=20.0, created_at=base + timedelta(minutes=2),
    )
    _write_history(
        state_root, cycle="challenger-b", profile="comparison-guide",
        pageviews=18.0, created_at=base + timedelta(minutes=3),
    )

    result = bridge.bootstrap_historical_learning(as_of=NOW)

    assert result["status"] == "OK"
    assert result["mature_outcomes"] == 4
    assert result["candidate"]["status"] == "PROSPECTIVE_TRIAL_CREATED"
    assert result["candidate"]["candidate_profile"] == "comparison-guide"
    active = rt.evolution_capability_registry.active(STRATEGY_CAPABILITY_ID)
    assert active.implementation_ref == STRATEGY_BASELINE_IMPL
    assert len(rt.evolution_candidates) == 1
    assert len(rt.evolution_experiments) == 1
    assert rt.evolution_experiments[0].stage.value == "SHADOW"
    assert rt.evolution_capability_activations == []
    assert len(rt.evolution_diagnoses) == 1
    assert rt.evolution_diagnoses[0].status.value == "INSUFFICIENT_EVIDENCE"


def test_trial_assignment_is_restart_stable_and_does_not_mutate_baseline_before_evidence(tmp_path):
    runtime_root = tmp_path / "runtime"
    state_root = tmp_path / "state"
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(runtime_root, clock=clock)
    bridge = ContentEvolutionBridge(
        runtime=rt,
        state_root=state_root,
        account="https://example.test",
    )
    base = NOW - timedelta(days=3)
    for i, (profile, views) in enumerate([
        ("follow-up-update", 5.0),
        ("follow-up-update", 6.0),
        ("comparison-guide", 20.0),
        ("comparison-guide", 18.0),
    ]):
        _write_history(
            state_root,
            cycle=f"h-{i}",
            profile=profile,
            pageviews=views,
            created_at=base + timedelta(minutes=i),
        )
    result = bridge.bootstrap_historical_learning(as_of=NOW)
    exp_id = result["candidate"]["experiment_id"]

    clock.advance(61)
    first = bridge.preferred_profile(cycle="future-cycle-1", now=clock.now())
    second = bridge.preferred_profile(cycle="future-cycle-1", now=clock.now())

    assert first == second
    assert first["mode"] == "PROSPECTIVE_TRIAL"
    assert first["arm"] in {"BASELINE", "CANDIDATE"}
    if first["arm"] == "CANDIDATE":
        assert first["profile_name"] == "comparison-guide"
    else:
        assert first["profile_name"] is None
    assert rt.evolution_experiment_manager.get(exp_id).stage.value == "PROSPECTIVE_TRIAL"
    active = rt.evolution_capability_registry.active(STRATEGY_CAPABILITY_ID)
    assert active.implementation_ref == STRATEGY_BASELINE_IMPL
