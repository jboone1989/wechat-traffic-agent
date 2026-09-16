import asyncio
import json

import pytest

from wechat_traffic_agent.content_explorer import GoalEvidenceExplorer, _topic_candidate
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

    async def cognitive_probe(self, task, max_tokens):
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
    ]}
    monkeypatch.setattr(module.urllib.request, "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(json.dumps(payload).encode()))
    rows = asyncio.run(module.collect_okx_public_market(NOW))
    assert len(rows) == 1
    row = rows[0]
    assert row["source_kind"] == "public-api"
    assert row["source"] == "OKX public market API"
    assert "BTC-USDT" in row["summary"] and "ETH-USDT" in row["summary"] and "SOL-USDT" in row["summary"]
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
