from datetime import datetime, timedelta, timezone
import hashlib
import json

from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.content_action_prediction import (
    bind_action_prediction,
    forecast_strategy_candidates,
    settle_action_predictions,
)
from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.content_strategy import PROFILES, choose_content_strategy
from wechat_traffic_agent.traffic_learning import TopicTrafficSignal

NOW = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
ACCOUNT = "https://example.com"


def _signal(topic="crypto-market"):
    return TopicTrafficSignal(
        topic=topic, article_count=3, orphan_article_count=0,
        search_rows=0, impressions=0, clicks=0, ctr=None, average_position=None,
        pageview_samples_24h=0, mean_pageviews_24h=None, trend_hits=0,
        demand_score=0.0, priority_score=0.0, exposure_status="UNKNOWN",
    )


def _strategy_id(topic, profile):
    payload = json.dumps({
        "schema": "website-content-strategy.v1", "topic": topic,
        "profile": profile.model_dump(mode="json"), "opening_mode": None,
    }, sort_keys=True, separators=(",", ":"))
    return "strategy:" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _publication(state, *, cycle, revision, topic, profile, published_at):
    root = state / "content/continuous/cycles" / cycle
    root.mkdir(parents=True, exist_ok=True)
    strategy_id = _strategy_id(topic, profile)
    payload = {
        "revision": revision,
        "artifact_id": "artifact-" + cycle,
        "outcome_attribution": {
            "published_at": published_at.isoformat(), "topic": topic,
            "published_content_sha256": "a" * 64, "agent_candidate_sha256": "a" * 64,
            "content_strategy": {
                "strategy_id": strategy_id, "topic": topic,
                "profile": profile.model_dump(mode="json"),
            },
        },
    }
    (root / "publication.json").write_text(json.dumps(payload), encoding="utf-8")
    return root, strategy_id


def _evidence(state, *, revision, topic, window_hours, value, observed_at):
    row = ChannelEvidence(
        channel="website", account=ACCOUNT, publication_id=revision,
        artifact_id="artifact", published_content_sha256="a" * 64,
        agent_candidate_sha256="a" * 64, topic=topic,
        title_version="title:v1", structure_version="structure:v1", strategy_version="strategy:v1",
        objective="website_pageviews", distribution_cohort="public-web-http-requests:v1",
        metric="http_pageviews", metric_definition="test pageviews", unit="count", value=float(value),
        published_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=window_hours),
        observed_at=observed_at, revision=1, source="nginx-access-log:v1",
        evidence_refs=(f"evidence:{revision}:{window_hours}h",),
    )
    ChannelEvidenceStore(state / "content/channel_evidence").record(row)


def _non_explore_cycle(topic):
    for index in range(1000):
        cycle = f"v2-predict-{index}"
        seed = int(hashlib.sha256(f"{cycle}|{topic}".encode()).hexdigest()[:8], 16)
        if seed % 100 >= 25:
            return cycle
    raise AssertionError("could not find deterministic exploit cycle")


def test_virtualbrain_belief_changes_candidate_prediction_and_next_strategy(tmp_path):
    state = tmp_path / "state"
    topic = "crypto-market"
    rewards = {profile.name: 0.2 for profile in PROFILES}
    rewards["evidence-brief"] = 0.9
    mind_context = {
        "topic": topic,
        "samples_by_profile": {profile.name: 2 for profile in PROFILES},
        "mean_relative_reward_by_profile": rewards,
        "evidence_refs": ["mind:history"],
    }
    forecasts = forecast_strategy_candidates(
        state_root=state, account=ACCOUNT, topic=topic, as_of=NOW,
        mind_strategy_context=mind_context,
    )
    assert forecasts["evidence-brief"]["expected_utility"] > forecasts["practical-guide"]["expected_utility"]
    binding = choose_content_strategy(
        state_root=state, account=ACCOUNT, cycle=_non_explore_cycle(topic), topic=topic,
        traffic_signal=_signal(topic), as_of=NOW, mind_strategy_context=mind_context,
        action_forecasts=forecasts,
    )
    assert binding.selection_mode == "PREDICTIVE_EXPLOIT"
    assert binding.profile.name == "evidence-brief"


def test_action_prediction_settles_early_feedback_and_opens_learning_inquiry(tmp_path):
    state = tmp_path / "state"
    topic = "crypto-market"
    profile = next(item for item in PROFILES if item.name == "evidence-brief")
    cycle = "v2-predict-live"
    cycle_root, strategy_id = _publication(
        state, cycle=cycle, revision="b" * 64, topic=topic, profile=profile,
        published_at=NOW + timedelta(minutes=1),
    )
    forecasts = forecast_strategy_candidates(state_root=state, account=ACCOUNT, topic=topic, as_of=NOW)
    prediction = bind_action_prediction(
        cycle_root / "action_prediction.json", cycle=cycle, topic=topic,
        strategy_id=strategy_id, selected_profile_name=profile.name,
        created_at=NOW, candidate_forecasts=forecasts,
    )
    expected_1h = prediction.selected.metrics[0].expected_pageviews
    _evidence(
        state, revision="b" * 64, topic=topic, window_hours=1, value=12,
        observed_at=NOW + timedelta(hours=1, minutes=5),
    )
    mind = ContentMind(root=state, subject_id="content-agent")
    first = settle_action_predictions(
        state_root=state, account=ACCOUNT, as_of=NOW + timedelta(hours=2), mind=mind,
    )
    assert first["status"] == "SETTLED"
    assert first["recorded"][0]["window"] == "1h"
    assert first["recorded"][0]["actual_pageviews"] == 12
    assert first["recorded"][0]["prediction_error"] > 0.55
    assert first["mind"]["inquiries_opened"]
    question = first["mind"]["inquiries_opened"][0]["question"]
    assert "Which assumption was wrong" in question
    assert f"{expected_1h:.2f}" in question
    restarted = ContentMind(root=state, subject_id="content-agent")
    plan = restarted.plan_for_cycle(
        cycle="v2-after-action-error", available_topics=(topic,),
    )
    assert plan["required_topic"] == topic
    assert plan["goal"]["origin"] == "INQUIRY"

    replay = settle_action_predictions(
        state_root=state, account=ACCOUNT, as_of=NOW + timedelta(hours=2), mind=mind,
    )
    assert replay["status"] == "NO_NEW_OUTCOMES"
    assert replay["mind"]["status"] == "NO_NEW_OUTCOMES"
    assert replay["mind"]["duplicates"] == 1


def test_multi_horizon_feedback_stays_separate_and_24h_is_mature(tmp_path):
    state = tmp_path / "state"
    topic = "model-evaluation"
    profile = next(item for item in PROFILES if item.name == "practical-guide")
    cycle = "v2-predict-horizons"
    cycle_root, strategy_id = _publication(
        state, cycle=cycle, revision="c" * 64, topic=topic, profile=profile,
        published_at=NOW + timedelta(minutes=1),
    )
    forecasts = forecast_strategy_candidates(state_root=state, account=ACCOUNT, topic=topic, as_of=NOW)
    bind_action_prediction(
        cycle_root / "action_prediction.json", cycle=cycle, topic=topic,
        strategy_id=strategy_id, selected_profile_name=profile.name,
        created_at=NOW, candidate_forecasts=forecasts,
    )
    _evidence(state, revision="c" * 64, topic=topic, window_hours=1, value=1,
              observed_at=NOW + timedelta(hours=1, minutes=5))
    _evidence(state, revision="c" * 64, topic=topic, window_hours=4, value=3,
              observed_at=NOW + timedelta(hours=4, minutes=5))
    _evidence(state, revision="c" * 64, topic=topic, window_hours=24, value=9,
              observed_at=NOW + timedelta(hours=24, minutes=5))
    settled = settle_action_predictions(
        state_root=state, account=ACCOUNT, as_of=NOW + timedelta(hours=25), mind=None,
    )
    by_window = {row["window"]: row for row in settled["recorded"]}
    assert set(by_window) == {"1h", "4h", "24h"}
    assert by_window["1h"]["maturity"] == "EARLY"
    assert by_window["4h"]["maturity"] == "PROVISIONAL"
    assert by_window["24h"]["maturity"] == "MATURE"
    assert by_window["1h"]["reliability"] < by_window["4h"]["reliability"] < by_window["24h"]["reliability"]
