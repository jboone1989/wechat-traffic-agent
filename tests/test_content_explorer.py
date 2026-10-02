import asyncio
import json

import pytest

from wechat_traffic_agent.content_explorer import (
    GoalEvidenceExplorer, _normalize_mission_search_query, _topic_candidate,
)
from wechat_traffic_agent.models import TopicSignal
from test_content_loop import NOW


def candidate(evidence_id="news-1", title="Candidate evidence"):
    return {"evidence_id": evidence_id, "source_kind": "rss", "source": "public-news-feed",
            "title": title, "summary": "A bounded observation about validation evidence.",
            "url": "https://example.org/evidence", "observed_at": NOW.isoformat(),
            "source_revision": evidence_id, "evidence_refs": ["news:" + evidence_id],
            "publisher_feed": "https://example.org/feed", "published_at": NOW.isoformat()}


class Runtime:
    def __init__(self, *responses):
        self.responses, self.calls = responses, []

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def explorer(tmp_path):
    async def rss(_feeds, now):
        return {"items": [], "failures": [], "observed_at": now.isoformat()}
    return GoalEvidenceExplorer(state_root=tmp_path, feeds=("https://example.org/feed",), rss_fetcher=rss)


def test_required_topic_changes_evidence_selection(tmp_path):
    rows = [candidate("rare-1", "Rare validation evidence"), candidate("common-1", "Common validation evidence")]
    rare = Runtime(json.dumps({"matches": [{"evidence_id": "rare-1", "evidence_quote": "Rare validation evidence", "reason": "Directly relevant."}]}))
    common = Runtime(json.dumps({"matches": [{"evidence_id": "common-1", "evidence_quote": "Common validation evidence", "reason": "Directly relevant."}]}))
    a = asyncio.run(explorer(tmp_path).filter_for_goal(runtime=rare, goal_topic="rare", topic_context=[], candidates=rows))
    b = asyncio.run(explorer(tmp_path).filter_for_goal(runtime=common, goal_topic="common", topic_context=[], candidates=rows))
    assert [x["evidence_id"] for x in a["items"]] == ["rare-1"]
    assert [x["evidence_id"] for x in b["items"]] == ["common-1"]


@pytest.mark.parametrize("payload,message", [
    ({"matches": [{"evidence_id": "invented", "evidence_quote": "Candidate evidence", "reason": "Relevant enough."}]}, "supplied candidate"),
    ({"matches": [{"evidence_id": "news-1", "evidence_quote": "invented quote", "reason": "Relevant enough."}]}, "contiguous source substring"),
])
def test_invented_id_or_quote_is_rejected(tmp_path, payload, message):
    raw = json.dumps(payload)
    runtime = Runtime(raw, raw)
    with pytest.raises(ValueError, match=message):
        asyncio.run(explorer(tmp_path).filter_for_goal(runtime=runtime, goal_topic="rare", topic_context=[], candidates=[candidate()]))
    assert len(runtime.calls) == 2


def test_agent_network_candidate_preserves_provenance():
    topic = TopicSignal(title="Candidate relationship scan", summary="Independent evidence threshold was not met.",
        source="agent-network:trading-agent", source_revision="artifact-1@2",
        evidence_refs=("network-contract:contract-1", "trading:report:scan-1"), observed_at=NOW)
    row = _topic_candidate(topic)
    assert row["source_kind"] == "agent-network"
    assert row["source_revision"] == "artifact-1@2"
    assert row["evidence_refs"] == ["network-contract:contract-1", "trading:report:scan-1"]


def test_web_candidate_preserves_search_publication_time_for_prospective_use():
    from wechat_traffic_agent.content_explorer import _web_candidate
    row = _web_candidate({
        "source_revision": "rev-date", "source": "runtime:web.fetch:test",
        "title": "Fresh dated story", "summary": "Fresh dated evidence.",
        "url": "https://example.org/fresh", "observed_at": "2026-09-16T03:00:00+00:00",
        "published_at": "2026-09-16T02:55:00+00:00",
        "evidence_refs": ["receipt:1"], "search_query": "fresh evidence", "search_rank": 1,
    })
    assert row["published_at"] == "2026-09-16T02:55:00+00:00"


def test_okx_public_market_snapshot_becomes_bounded_crypto_evidence(monkeypatch):
    import io
    import wechat_traffic_agent.content_explorer as module
    payload = {"data": [
        {"instId": "BTC-USDT", "last": "76000", "open24h": "77000", "ts": "1789550000000"},
        {"instId": "ETH-USDT", "last": "2400", "open24h": "2460", "ts": "1789550000001"},
        {"instId": "SOL-USDT", "last": "97", "open24h": "100", "ts": "1789550000002"},
        {"instId": "XRP-USDT", "last": "1.29", "open24h": "1.40", "ts": "1789550000003"},
        {"instId": "BNB-USDT", "last": "713", "open24h": "719", "ts": "1789550000004"},
    ]}
    monkeypatch.setattr(module.urllib.request, "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(json.dumps(payload).encode()))
    rows = asyncio.run(module.collect_okx_public_market(NOW))
    assert len(rows) == 1
    row = rows[0]
    assert row["source_kind"] == "public-api"
    assert row["source"] == "OKX public market API"
    assert all(symbol in row["summary"] for symbol in ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT"))
    assert "not a trading signal" in row["summary"]
    assert row["evidence_refs"][0].startswith("okx-public-api:sha256:")


def test_active_explorer_includes_market_source_without_requiring_trading(tmp_path):
    calls = []
    async def rss(_feeds, now):
        return {"items": [], "failures": [], "observed_at": now.isoformat()}
    async def market(now):
        calls.append(now)
        return [candidate("market-1", "BTC and ETH moved differently") | {"source_kind": "public-api", "source": "OKX public market API"}]
    worker = GoalEvidenceExplorer(state_root=tmp_path, feeds=("https://example.org/feed",), rss_fetcher=rss, market_fetcher=market)
    passive = asyncio.run(worker.collect(now=NOW, active=False))
    active = asyncio.run(worker.collect(now=NOW, active=True))
    assert passive["items"] == [] and calls == [NOW]
    assert active["items"][0]["source_kind"] == "public-api"


def test_crypto_goal_web_search_uses_current_market_catalyst_query(tmp_path):
    calls = []
    async def rss(_feeds, now):
        return {"items": [], "failures": [], "observed_at": now.isoformat()}
    async def web(query, now):
        calls.append((query, now))
        return []
    worker = GoalEvidenceExplorer(state_root=tmp_path, feeds=("https://example.org/feed",), rss_fetcher=rss, web_searcher=web)
    result = asyncio.run(worker.search_web(goal_topic="crypto-market", now=NOW))
    assert result["query"] == "crypto market BTC ETH SOL latest market catalyst today"
    assert calls == [(result["query"], NOW)]


def test_non_crypto_goal_keeps_research_search_shape(tmp_path):
    calls = []
    async def rss(_feeds, now):
        return {"items": [], "failures": [], "observed_at": now.isoformat()}
    async def web(query, now):
        calls.append(query)
        return []
    worker = GoalEvidenceExplorer(state_root=tmp_path, feeds=("https://example.org/feed",), rss_fetcher=rss, web_searcher=web)
    result = asyncio.run(worker.search_web(goal_topic="model-evaluation", now=NOW))
    assert result["query"] == "model evaluation evidence implementation"
    assert calls == [result["query"]]


def test_relevance_filter_fails_closed_after_two_malformed_model_responses(tmp_path):
    runtime = Runtime("not json", "still not json")

    result = asyncio.run(
        explorer(tmp_path).filter_for_goal(
            runtime=runtime,
            goal_topic="rare",
            topic_context=[],
            candidates=[candidate()],
        )
    )

    assert result["items"] == []
    assert result["matches"] == []
    assert result["candidate_count"] == 1
    assert result["classifier_status"] == "MODEL_OUTPUT_INVALID"
    assert result["classifier_error"] == "ValueError"
    assert len(runtime.calls) == 2


def test_mission_web_query_preserves_explicit_research_intent(tmp_path):
    calls = []

    async def searcher(query, now):
        calls.append((query, now))
        return [{
            "source_revision": "mission-search-1",
            "source": "runtime:web.fetch:test",
            "title": "Persistent AI agent runtime evidence",
            "summary": "A public source describes a continuously running agent runtime.",
            "url": "https://example.org/persistent-agent",
            "observed_at": now.isoformat(),
            "published_at": now.isoformat(),
            "evidence_refs": ["web:mission-search-1"],
            "search_query": query,
            "search_rank": 1,
        }]

    async def rss(_feeds, now):
        return {"items": [], "failures": [], "observed_at": now.isoformat()}

    worker = GoalEvidenceExplorer(
        state_root=tmp_path,
        feeds=("https://example.org/feed",),
        rss_fetcher=rss,
        web_searcher=searcher,
    )
    result = asyncio.run(worker.search_web_query(
        query="persistent AI agents continuous runtime evidence",
        goal_topic="persistent-ai-agents",
        now=NOW,
    ))

    assert calls == [("persistent AI agents continuous runtime evidence", NOW)]
    assert result["query_authority"] == "EXTERNAL_MISSION_DISCOVERY"
    assert result["goal_topic"] == "persistent-ai-agents"
    assert result["items"][0]["evidence_id"]


def test_mission_search_query_puts_subject_before_generic_freshness_boilerplate():
    query = _normalize_mission_search_query(
        query=(
            "Recent public case studies on evaluating AI trading model performance "
            "using out-of-sample metrics and statistical validation."
        ),
        goal_topic="model-evaluation",
    )
    assert query.startswith("model evaluation ")
    assert not query.casefold().startswith(("recent ", "latest ", "current ", "public "))
    assert "out-of-sample" in query
    assert len(query) <= 180


def test_mission_web_query_executes_subject_first_query(tmp_path):
    calls = []

    async def searcher(query, now):
        calls.append(query)
        return []

    async def rss(_feeds, now):
        return {"items": [], "failures": [], "observed_at": now.isoformat()}

    worker = GoalEvidenceExplorer(
        state_root=tmp_path,
        feeds=("https://example.org/feed",),
        rss_fetcher=rss,
        web_searcher=searcher,
    )
    result = asyncio.run(worker.search_web_query(
        query=(
            "Recent public case studies on evaluating AI trading model performance "
            "using out-of-sample metrics and statistical validation."
        ),
        goal_topic="model-evaluation",
        now=NOW,
    ))

    assert calls == [result["query"]]
    assert result["query"].startswith("model evaluation ")
    assert result["query_authority"] == "EXTERNAL_MISSION_DISCOVERY"


@pytest.mark.parametrize("counter", [False, True])
def test_multiple_valid_quotes_from_one_source_are_preserved_without_duplicate_sources(tmp_path, counter):
    row = candidate()
    payload = {"matches": [
        {"evidence_id": row["evidence_id"], "evidence_quote": row["title"], "reason": "Relevant source title."},
        {"evidence_id": row["evidence_id"], "evidence_quote": row["summary"], "reason": "Another grounded observation."},
    ]}
    runtime = Runtime(json.dumps(payload))
    worker = explorer(tmp_path)
    operation = (worker.filter_counter_evidence(runtime=runtime, primary=candidate("primary"), candidates=[row])
                 if counter else worker.filter_for_goal(runtime=runtime, goal_topic="validation", topic_context=[], candidates=[row]))
    result = asyncio.run(operation)
    assert result["items"] == [row]
    assert result["matches"] == payload["matches"]
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("counter", [False, True])
def test_repeated_source_cannot_hide_an_invented_second_quote(tmp_path, counter):
    row = candidate()
    payload = {"matches": [
        {"evidence_id": row["evidence_id"], "evidence_quote": row["title"], "reason": "Relevant source title."},
        {"evidence_id": row["evidence_id"], "evidence_quote": "This quote was invented.", "reason": "Another claimed observation."},
    ]}
    runtime = Runtime(json.dumps(payload))
    worker = explorer(tmp_path)
    operation = (worker.filter_counter_evidence(runtime=runtime, primary=candidate("primary"), candidates=[row])
                 if counter else worker.filter_for_goal(runtime=runtime, goal_topic="validation", topic_context=[], candidates=[row]))
    with pytest.raises(ValueError, match="substring"):
        asyncio.run(operation)
    assert len(runtime.calls) == 2
