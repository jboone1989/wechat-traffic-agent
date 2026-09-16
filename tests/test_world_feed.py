from __future__ import annotations

import asyncio
import json
from pathlib import Path

from wechat_traffic_agent.world_feed import WorldFeed
from test_content_explorer import candidate
from test_content_loop import NOW


class Runtime:
    def __init__(self):
        self.calls = []

    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
        if task.startswith("WORLD_SCAN_PLAN:"):
            return json.dumps({
                "queries": ["latest crypto macro liquidity market developments"],
                "rationale": "Look for fresh external events that could change the agent's current market view.",
            })
        if task.startswith("WORLD_ATTENTION:"):
            return json.dumps({
                "observations": [{
                    "evidence_id": "web:revision-fed",
                    "evidence_quote": "Federal Reserve policy shift changes liquidity expectations",
                    "topic": "monetary-policy",
                    "question": "Does the latest policy shift materially change crypto liquidity conditions?",
                    "importance": 0.95,
                    "novelty": 0.9,
                    "why_now": "The policy shift is fresh and could invalidate the agent's prior assumptions.",
                }]
            })
        raise AssertionError(task[:80])


async def discovered_search(query, now):
    assert query == "latest crypto macro liquidity market developments"
    return [{
        "title": "Federal Reserve policy shift changes liquidity expectations",
        "summary": "Crypto markets reassess liquidity and real-rate expectations after a fresh policy signal.",
        "url": "https://new-source.example/research/fed-liquidity",
        "source": "runtime:web.fetch:test",
        "source_revision": "revision-fed",
        "evidence_refs": ["capability-receipt:v1:search", "capability-receipt:v1:fetch"],
        "search_query": query,
        "search_rank": 1,
        "observed_at": now.isoformat(),
    }]


def test_world_feed_agent_discovers_source_and_surfaces_reality_signal(tmp_path: Path):
    feed = WorldFeed(state_root=tmp_path, web_searcher=discovered_search)
    runtime = Runtime()
    result = asyncio.run(feed.collect(
        runtime=runtime, now=NOW, base_items=[candidate("seed-1", "Seed business signal")],
        mind_projection={"interests": [{"name": "model-evaluation"}], "open_inquiries": []},
    ))
    assert result["scan_plan"]["queries"] == ["latest crypto macro liquidity market developments"]
    assert {row["source_kind"] for row in result["items"]} == {"rss", "web-search"}
    assert result["observations"][0]["topic"] == "monetary-policy"
    assert result["observations"][0]["evidence_id"] == "web:revision-fed"
    registry = json.loads(feed.registry_path.read_text())
    assert registry["new-source.example"]["seen_count"] == 1
    assert registry["new-source.example"]["attention_count"] == 1
    assert len(runtime.calls) == 2


def test_world_feed_discovery_failure_keeps_seed_reality(tmp_path: Path):
    async def broken_search(query, now):
        raise RuntimeError("search unavailable")

    class SeedRuntime(Runtime):
        async def cognitive_probe(self, task, max_tokens):
            if task.startswith("WORLD_SCAN_PLAN:"):
                return json.dumps({
                    "queries": ["crypto market news now"],
                    "rationale": "Check whether anything external deserves attention before choosing a topic.",
                })
            if task.startswith("WORLD_ATTENTION:"):
                return json.dumps({"observations": []})
            raise AssertionError(task)

    feed = WorldFeed(state_root=tmp_path, web_searcher=broken_search)
    result = asyncio.run(feed.collect(
        runtime=SeedRuntime(), now=NOW, base_items=[candidate("seed-2", "Seed survives")],
        mind_projection={},
    ))
    assert [row["evidence_id"] for row in result["items"]] == ["seed-2"]
    assert result["failures"][0]["source"] == "agent-discovered-web"
