from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.traffic_learning import (
    SearchDemandObservation,
    SearchDemandStore,
    TrafficLearningContextBuilder,
)

T = datetime(2026, 9, 1, tzinfo=timezone.utc)
SITE = "https://content.example"


def _website():
    return {
        "articles": [
            {"artifact": {"artifact_id": "a1", "tags": ["ai-agents"]}, "route": "/articles/a1/"},
            {"artifact": {"artifact_id": "a2", "tags": ["ai-agents"]}, "route": "/articles/a2/"},
            {"artifact": {"artifact_id": "b1", "tags": ["trading"]}, "route": "/articles/b1/"},
        ],
        "topics": {"ai-agents": ["a1", "a2"], "trading": ["b1"]},
        "inbound_counts": {"a1": 0, "a2": 2, "b1": 0},
    }


def _search(*, query, page, impressions, clicks, position, revision=1):
    return SearchDemandObservation(
        site=SITE,
        query=query,
        page=page,
        clicks=clicks,
        impressions=impressions,
        ctr=(clicks / impressions if impressions else 0.0),
        position=position,
        window_start=T,
        window_end=T + timedelta(days=7),
        observed_at=T + timedelta(days=8),
        revision=revision,
        evidence_ref=f"gsc:{query}:{revision}",
    )


def test_no_search_data_is_unknown_not_zero(tmp_path):
    context = TrafficLearningContextBuilder(state_root=tmp_path, account=SITE).build(
        website=_website(), as_of=T + timedelta(days=10)
    )
    assert context.search_data_status == "NO_DATA"
    assert {row.topic: row.exposure_status for row in context.topics} == {
        "ai-agents": "UNKNOWN", "trading": "UNKNOWN"
    }


def test_impressions_without_clicks_are_distinct_from_no_exposure(tmp_path):
    store = SearchDemandStore(tmp_path / "content/search_demand")
    store.record(_search(
        query="ai agent memory", page=SITE + "/articles/a1/",
        impressions=120, clicks=0, position=8.0,
    ))
    store.record(_search(
        query="trading agent", page=SITE + "/articles/b1/",
        impressions=0, clicks=0, position=None,
    ))
    context = TrafficLearningContextBuilder(state_root=tmp_path, account=SITE).build(
        website=_website(), as_of=T + timedelta(days=10)
    )
    by_topic = {row.topic: row for row in context.topics}
    assert by_topic["ai-agents"].exposure_status == "NO_CLICKS"
    assert by_topic["ai-agents"].impressions == 120
    assert by_topic["ai-agents"].ctr == 0.0
    assert by_topic["trading"].exposure_status == "NO_IMPRESSIONS"


def test_search_store_uses_latest_revision_as_of_cutoff(tmp_path):
    store = SearchDemandStore(tmp_path / "content/search_demand")
    store.record(_search(query="agent", page=SITE + "/articles/a1/", impressions=10, clicks=1, position=9, revision=1))
    newer = _search(query="agent", page=SITE + "/articles/a1/", impressions=20, clicks=4, position=7, revision=2)
    newer = newer.model_copy(update={"observed_at": T + timedelta(days=9)})
    store.record(newer)
    early = store.as_of(T + timedelta(days=8, hours=12))
    assert len(early) == 1 and early[0].revision == 1
    late = store.as_of(T + timedelta(days=10))
    assert len(late) == 1 and late[0].revision == 2


def test_pageviews_are_summarized_separately_from_search_exposure(tmp_path):
    channel = ChannelEvidenceStore(tmp_path / "content/channel_evidence")
    channel.record(ChannelEvidence(
        channel="website", account=SITE, publication_id="pub-1", artifact_id="a1",
        published_content_sha256="a" * 64, agent_candidate_sha256="a" * 64,
        topic="ai-agents", title_version="title:v1", structure_version="structure:v1",
        strategy_version="strategy:v1", objective="website_pageviews",
        distribution_cohort="public-web-http-requests:v1", metric="http_pageviews",
        metric_definition="eligible requests", unit="count", value=7.0,
        published_at=T, window_start=T, window_end=T + timedelta(hours=24),
        observed_at=T + timedelta(days=2), revision=1, source="nginx-access-log:v1",
        evidence_refs=("nginx:test",),
    ))
    context = TrafficLearningContextBuilder(state_root=tmp_path, account=SITE).build(
        website=_website(), as_of=T + timedelta(days=10)
    )
    by_topic = {row.topic: row for row in context.topics}
    assert context.search_data_status == "NO_DATA"
    assert context.pageview_data_status == "AVAILABLE"
    assert by_topic["ai-agents"].mean_pageviews_24h == 7.0
    assert by_topic["ai-agents"].exposure_status == "UNKNOWN"


def test_priority_combines_search_demand_trends_and_content_gap(tmp_path):
    store = SearchDemandStore(tmp_path / "content/search_demand")
    store.record(_search(
        query="common topic guide", page=SITE + "/articles/a1/",
        impressions=200, clicks=10, position=6.0,
    ))
    context = TrafficLearningContextBuilder(state_root=tmp_path, account=SITE).build(
        website=_website(), as_of=T + timedelta(days=10),
        trend_candidates=[{
            "title": "Trading system update", "summary": "A trading workflow changed today."
        }],
    )
    by_topic = {row.topic: row for row in context.topics}
    assert by_topic["ai-agents"].demand_score == 1.0
    assert by_topic["trading"].trend_hits == 1
    assert by_topic["trading"].priority_score > 0
    assert max(row.priority_score for row in context.topics) > 0


def test_search_demand_cli_imports_normalized_gsc_observation(tmp_path, capsys):
    from wechat_traffic_agent.content_evidence_cli import main

    row = _search(
        query="ai agent search demand", page=SITE + "/articles/a1/",
        impressions=50, clicks=5, position=4.0,
    )
    payload = tmp_path / "gsc.json"
    payload.write_text(row.model_dump_json())
    root = tmp_path / "search-demand"
    assert main(["--root", str(root), "import-search", "--input", str(payload)]) == 0
    output = __import__("json").loads(capsys.readouterr().out)
    assert output["status"] == "RECORDED"
    assert output["source"] == "google-search-console:v1"
    stored = SearchDemandStore(root).as_of(T + timedelta(days=10))
    assert len(stored) == 1 and stored[0].query == "ai agent search demand"
