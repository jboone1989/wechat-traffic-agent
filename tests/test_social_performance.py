from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.okx_orbit_outcomes import SOURCE
from wechat_traffic_agent.x_outcomes import SOURCE as X_SOURCE
from wechat_traffic_agent.social_performance import okx_social_performance_context, x_social_performance_context

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def _row(*, publication: str, structure: str, metric: str, value: float, offset_days: int = 0) -> ChannelEvidence:
    published = NOW - timedelta(days=3 + offset_days)
    return ChannelEvidence(
        channel="okx-orbit",
        account="https://www.okx.com/orbit",
        publication_id=publication,
        artifact_id=f"artifact-{publication}",
        published_content_sha256="a" * 64,
        agent_candidate_sha256="a" * 64,
        topic="crypto-market",
        title_version="okx-orbit:no-title:v1",
        structure_version=structure,
        strategy_version="okx-social-evidence:DRAFT:v1",
        objective=f"okx_{metric}",
        distribution_cohort="okx-orbit-public-feed:fixed-age-window:v1",
        metric=metric,
        metric_definition=f"Public {metric} count.",
        unit="count",
        value=value,
        published_at=published,
        window_start=published,
        window_end=published + timedelta(hours=24),
        observed_at=published + timedelta(hours=24, minutes=5),
        revision=1,
        source=SOURCE,
        evidence_refs=(f"okx-orbit-publication:{publication}",),
    )


def test_two_mature_samples_enable_style_only_guidance(tmp_path: Path):
    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    structure = "okx-orbit:SHORT_POST:QUESTION:v1"
    for publication, views, likes in (("p1", 1000, 10), ("p2", 2000, 20)):
        store.record(_row(publication=publication, structure=structure, metric="views", value=views))
        store.record(_row(publication=publication, structure=structure, metric="likes", value=likes))

    context = okx_social_performance_context(state_root=tmp_path, as_of=NOW)

    assert context["status"] == "DESCRIPTIVE_ONLY"
    assert context["generation_authority"] == "STYLE_ONLY"
    assert context["channel_scope"] == "OKX_ONLY"
    assert context["factual_authority"] is False
    assert context["evidence_gate_authority"] is False
    assert context["publication_authority"] is False
    assert context["causal_claim"] is False
    row = context["variants"][0]
    assert row["samples"] == 2
    assert row["mean_views_24h"] == 1500.0
    assert row["mean_likes_24h"] == 15.0


def test_one_sample_does_not_control_expression(tmp_path: Path):
    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    store.record(_row(
        publication="p1", structure="okx-orbit:SHORT_POST:OBSERVATION:v1",
        metric="views", value=500,
    ))
    context = okx_social_performance_context(state_root=tmp_path, as_of=NOW)
    assert context["status"] == "INSUFFICIENT_SAMPLES"
    assert context["generation_authority"] == "NONE"
    assert context["variants"][0]["eligible_for_style_guidance"] is False


def test_edited_or_non_okx_rows_are_not_used(tmp_path: Path):
    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    row = _row(
        publication="p1", structure="okx-orbit:SHORT_POST:QUESTION:v1",
        metric="views", value=999,
    )
    edited = row.model_copy(update={"agent_candidate_sha256": "b" * 64})
    store.record(edited)
    context = okx_social_performance_context(state_root=tmp_path, as_of=NOW)
    assert context["status"] == "INSUFFICIENT_SAMPLES"
    assert context["variants"] == []


def test_two_x_samples_enable_x_only_style_guidance(tmp_path: Path):
    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    structure = "x:LONG_POST:QUESTION:v1"
    for publication, views, likes in (("xp1", 100, 3), ("xp2", 300, 9)):
        base = _row(publication=publication, structure=structure, metric="views", value=views)
        store.record(base.model_copy(update={
            "channel": "x", "account": "https://x.com/agent", "source": X_SOURCE,
            "objective": "x_views", "distribution_cohort": "x-public-post:fixed-age-window:v1",
            "evidence_refs": (f"x-publication:{publication}",),
        }))
        like = _row(publication=publication, structure=structure, metric="likes", value=likes)
        store.record(like.model_copy(update={
            "channel": "x", "account": "https://x.com/agent", "source": X_SOURCE,
            "objective": "x_likes", "distribution_cohort": "x-public-post:fixed-age-window:v1",
            "evidence_refs": (f"x-publication:{publication}",),
        }))
    context = x_social_performance_context(state_root=tmp_path, as_of=NOW)
    assert context["status"] == "DESCRIPTIVE_ONLY"
    assert context["generation_authority"] == "STYLE_ONLY"
    assert context["channel_scope"] == "X_ONLY"
    assert context["factual_authority"] is False
    assert context["causal_claim"] is False
    assert context["variants"][0]["samples"] == 2
    assert context["variants"][0]["mean_views_24h"] == 200.0
    assert context["variants"][0]["mean_likes_24h"] == 6.0

def test_okx_rows_never_enter_x_performance_context(tmp_path: Path):
    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    store.record(_row(publication="p1", structure="okx-orbit:SHORT_POST:QUESTION:v1", metric="views", value=999))
    context = x_social_performance_context(state_root=tmp_path, as_of=NOW)
    assert context["status"] == "INSUFFICIENT_SAMPLES"
    assert context["variants"] == []


def _x_row(*, publication: str, topic: str, metric: str, value: float | None,
           observed_hours_ago: float, account: str = "https://x.com/agent") -> ChannelEvidence:
    observed = NOW - timedelta(hours=observed_hours_ago)
    published = observed - timedelta(hours=24, minutes=5)
    base = _row(publication=publication, structure="x:SHORT_POST:v1", metric=metric, value=0)
    return base.model_copy(update={
        "channel": "x", "account": account, "source": X_SOURCE, "topic": topic,
        "objective": f"x_{metric}", "distribution_cohort": "x-public-post:fixed-age-window:v1",
        "value": value, "published_at": published, "window_start": published,
        "window_end": published + timedelta(hours=24), "observed_at": observed,
        "evidence_refs": (f"x-publication:{publication}",),
    })


def _x_post(store: ChannelEvidenceStore, publication: str, topic: str, *, likes, reposts, replies,
            views=100, observed_hours_ago: float = 1, account: str = "https://x.com/agent") -> None:
    for metric, value in (("views", views), ("likes", likes), ("reposts", reposts), ("replies", replies)):
        store.record(_x_row(publication=publication, topic=topic, metric=metric, value=value,
                            observed_hours_ago=observed_hours_ago, account=account))


def test_x_context_aggregates_by_topic_and_exposes_compact_observations(tmp_path: Path):
    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    _x_post(store, "a1", "onchain", likes=4, reposts=1, replies=2, observed_hours_ago=30)
    _x_post(store, "a2", "onchain", likes=6, reposts=1, replies=0, observed_hours_ago=20)
    _x_post(store, "b1", "macro", likes=1, reposts=0, replies=0, observed_hours_ago=10)

    context = x_social_performance_context(state_root=tmp_path, as_of=NOW)

    topics = {row["topic"]: row for row in context["topic_variants"]}
    assert topics["onchain"]["samples"] == 2
    assert topics["onchain"]["eligible_for_topic_guidance"] is True
    # likes + 2*reposts + 3*replies: (4+2+6 + 6+2+0) / 2
    assert topics["onchain"]["mean_engagement_score_24h"] == 10.0
    assert topics["macro"]["eligible_for_topic_guidance"] is False
    assert context["sample_count"] == 3
    assert any(line.startswith("topic onchain:") for line in context["observations"])
    assert not any(line.startswith("topic macro:") for line in context["observations"])
    assert context["factual_authority"] is False
    assert context["causal_claim"] is False


def test_x_audience_outcomes_use_own_prior_median_and_recent_lookback(tmp_path: Path):
    from wechat_traffic_agent.social_performance import x_mature_audience_outcomes

    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    _x_post(store, "old", "macro", likes=2, reposts=0, replies=0, observed_hours_ago=200)
    _x_post(store, "p1", "macro", likes=4, reposts=0, replies=0, observed_hours_ago=12)
    _x_post(store, "p2", "onchain", likes=3, reposts=1, replies=3, observed_hours_ago=2)
    _x_post(store, "other", "onchain", likes=90, reposts=0, replies=0, observed_hours_ago=3,
            account="https://x.com/someone-else")

    outcomes = {row["publication_id"]: row for row in x_mature_audience_outcomes(state_root=tmp_path, as_of=NOW)}

    assert "old" not in outcomes  # outside the 24h lookback, but still part of the baseline
    assert outcomes["p1"]["baseline_engagement_score"] == 2.0
    assert outcomes["p2"]["engagement_score"] == 14.0
    assert outcomes["p2"]["baseline_engagement_score"] == 3.0  # median(2, 4); other accounts excluded
    assert outcomes["other"]["baseline_engagement_score"] is None
    assert outcomes["p2"]["outcome_id"] == "x-audience-24h:p2"


def test_x_audience_outcome_with_unknown_counter_is_not_scored(tmp_path: Path):
    from wechat_traffic_agent.social_performance import x_mature_audience_outcomes

    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    _x_post(store, "p1", "macro", likes=5, reposts=None, replies=1)

    assert x_mature_audience_outcomes(state_root=tmp_path, as_of=NOW) == []


def test_cycle_sync_feeds_mature_x_outcomes_to_mind_once_and_survives_errors(tmp_path: Path):
    from types import SimpleNamespace
    from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop as ContentGoalLoop

    store = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    _x_post(store, "p1", "onchain", likes=4, reposts=0, replies=1)
    received, activity = [], []

    class Mind:
        def observe_x_audience_outcomes(self, outcomes):
            received.append(list(outcomes))
            return {"status": "OBSERVED", "applied": [{"outcome_id": "x"}]}

    loop = SimpleNamespace(mind=Mind(), state=tmp_path,
                           _activity=lambda *args, **kwargs: activity.append(args))
    folder = tmp_path / "cycle"
    folder.mkdir()
    first = ContentGoalLoop._sync_x_audience_learning(loop, folder, "v2-1", NOW)
    again = ContentGoalLoop._sync_x_audience_learning(loop, folder, "v2-1", NOW)
    assert first == again
    assert len(received) == 1 and received[0][0]["outcome_id"] == "x-audience-24h:p1"
    assert len(activity) == 1

    class BrokenMind:
        def observe_x_audience_outcomes(self, outcomes):
            raise ValueError("bad row")

    loop.mind = BrokenMind()
    broken_folder = tmp_path / "cycle-2"
    broken_folder.mkdir()
    result = ContentGoalLoop._sync_x_audience_learning(loop, broken_folder, "v2-2", NOW)
    assert result["status"] == "ERROR"
    assert result["error_type"] == "ValueError"
