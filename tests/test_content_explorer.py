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
