import asyncio
import json
from pathlib import Path

from types import SimpleNamespace

from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
from wechat_traffic_agent.sources.runtime_web import RuntimeWebSearch
from test_content_loop import NOW, Runtime, setup


def web_candidate():
    return {
        "evidence_id": "web:revision-1", "source_kind": "web-search",
        "source": "runtime:web.search:searxng", "title": "Model evaluation reliability",
        "summary": "Researchers compare model evaluation methods under distribution shift.",
        "url": "https://example.org/model-evaluation", "observed_at": NOW.isoformat(),
        "source_revision": "revision-1",
        "evidence_refs": ["capability-receipt:v1:test", "web-url-sha256:test"],
        "publisher_feed": None, "published_at": None,
        "search_query": "model evaluation AI trading research evidence", "search_rank": 1,
    }


class CapabilityRuntime:
    def __init__(self):
        self.calls = []
        self.persisted = 0

    def available_capabilities(self):
        return ("web.search", "web.fetch")

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        if capability_id == "web.search":
            return SimpleNamespace(
                receipt_id="capability-receipt:v1:search", provider="searxng",
                payload={"ok": True, "capability_id": "web.search", "results": [{
                    "title": "Useful result", "url": "https://example.org/a",
                    "snippet": "Discovery only.", "engine": "duckduckgo",
                }]},
            )
        assert capability_id == "web.fetch"
        return SimpleNamespace(
            receipt_id="capability-receipt:v1:fetch", provider="crawl4ai",
            payload={"ok": True, "capability_id": "web.fetch",
                     "canonical_capability_id": "web.fetch", "page": {
                "url": "https://example.org/a", "success": True,
                "markdown": "Model evaluation reliability evidence under distribution shift. " * 8,
            }},
        )

    def persist(self):
        self.persisted += 1


def test_runtime_web_search_uses_application_runtime_receipt():
    runtime = CapabilityRuntime()
    rows = asyncio.run(RuntimeWebSearch(runtime).search(
        query="model evaluation AI trading research evidence", now=NOW))

    assert len(rows) == 1
    assert rows[0]["source"] == "runtime:web.fetch:crawl4ai"
    assert rows[0]["evidence_refs"][:2] == [
        "capability-receipt:v1:search", "capability-receipt:v1:fetch"]
    assert runtime.calls[0][0] == "web.search"
    assert runtime.calls[1][0] == "web.fetch"
    assert runtime.calls[0][1]["limit"] == 8
    assert runtime.persisted == 2


class SearchOnlyRuntime(CapabilityRuntime):
    def available_capabilities(self):
        return ("web.search",)


def test_search_snippet_is_never_promoted_without_runtime_fetch():
    from wechat_traffic_agent.sources.runtime_web import RuntimeWebEvidenceError
    runtime = SearchOnlyRuntime()
    try:
        asyncio.run(RuntimeWebSearch(runtime).search(
            query="model evaluation AI trading research evidence", now=NOW))
    except RuntimeWebEvidenceError as exc:
        assert exc.source == "runtime:web.fetch"
    else:
        raise AssertionError("search-only discovery must fail closed")
    assert [call[0] for call in runtime.calls] == ["web.search"]


def test_goal_loop_contains_no_direct_llm_web_provider():
    root = Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent"
    goal_loop = (root / "content_goal_loop.py").read_text()
    assert "DeepSeekJinaWebSearch" not in goal_loop


class GoalMind:
    def plan_for_cycle(self, *, cycle, available_topics):
        assert "rare" in available_topics
        return {
            "cycle": cycle,
            "goal": {"origin": "INTEREST", "interest_refs": ["rare"]},
            "required_topic": "rare",
        }

    def observe_process_experience(self, **kwargs):
        return {"status": "OBSERVED", "topic": kwargs["topic"]}


class V2Runtime(Runtime):
    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({
                "title": "Explaining evidence question 1",
                "evidence_id": "web:revision-1", "topic": "rare", "related_ids": ["a0"],
                "reader_question": "What does this web evidence actually support?",
                "gap": "Connect the searched evidence to existing evaluation interpretation.",
            })
        return await super().cognitive_probe(task, max_tokens)


class FallbackExplorer:
    def __init__(self):
        self.filter_calls = 0
        self.search_calls = 0

    async def collect(self, *, now, active):
        assert active is True
        return {"items": [{
            "evidence_id": "rss-irrelevant", "source_kind": "rss",
            "source": "public-news-feed", "title": "Unrelated event",
            "summary": "Nothing about model evaluation.",
            "url": "https://example.org/unrelated", "observed_at": now.isoformat(),
            "source_revision": "rss-irrelevant",
            "evidence_refs": ["news:rss-irrelevant"],
            "publisher_feed": "https://example.org/feed",
            "published_at": now.isoformat(), "search_query": None, "search_rank": None,
        }], "failures": [], "observed_at": now.isoformat()}

    async def filter_for_goal(self, **kwargs):
        self.filter_calls += 1
        if self.filter_calls == 1:
            return {"items": [], "matches": [],
                    "candidate_count": len(kwargs["candidates"])}
        assert kwargs["candidates"][0]["source_kind"] == "web-search"
        return {"items": list(kwargs["candidates"]),
                "matches": [{"evidence_id": "web:revision-1"}],
                "candidate_count": len(kwargs["candidates"])}

    async def search_web(self, *, goal_topic, now):
        self.search_calls += 1
        assert goal_topic == "rare"
        return {"items": [web_candidate()], "failures": [],
                "query": "rare AI trading research evidence"}


def test_goal_falls_back_to_runtime_web_search_then_publishes(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, explorer, mind = V2Runtime(), FallbackExplorer(), GoalMind()
    worker = ContinuousContentLoop(
        runtime=runtime, state_root=legacy.state,
        artifacts_path=legacy.artifacts_path, site_root=legacy.site_root,
        base_url=legacy.base_url, curriculum_path=legacy.curriculum_path,
        feeds=legacy.feeds, interval_hours=legacy.interval_hours,
        fetcher=legacy.fetcher, publisher=publisher, mind=mind, explorer=explorer,
    )
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert explorer.filter_calls == 2 and explorer.search_calls == 1
    assert result["exploration"]["selected_source_kind"] == "web-search"
    cycle = Path(legacy.state) / "content/continuous/cycles" / result["cycle"]
    search = json.loads((cycle / "web_search.json").read_text())
    final = json.loads((cycle / "exploration.json").read_text())
    assert search["query"] == "rare AI trading research evidence"
    assert final["fallback"] == "web-search"
    assert final["initial_candidate_count"] == 1
    assert final["web_candidate_count"] == 1
