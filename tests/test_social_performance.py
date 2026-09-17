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
