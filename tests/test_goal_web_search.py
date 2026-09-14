import asyncio
import json
from pathlib import Path

from wechat_traffic_agent.content_explorer import GoalEvidenceExplorer
from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
from wechat_traffic_agent.sources.web_search import (
    DeepSeekJinaWebSearch, parse_deepseek_search, parse_jina_reader,
)
from test_content_loop import NOW, Runtime, setup


def web_candidate():
    return {
        "evidence_id": "web:revision-1", "source_kind": "web-search",
        "source": "web-search:deepseek+jina-reader", "title": "Model evaluation reliability",
        "summary": "Researchers compare model evaluation methods under distribution shift.",
        "url": "https://example.org/model-evaluation", "observed_at": NOW.isoformat(),
        "source_revision": "revision-1", "evidence_refs": ["web-search:deepseek:result-1", "web-reader:jina:content-1"],
        "publisher_feed": None, "published_at": None,
        "search_query": "model evaluation AI trading research evidence", "search_rank": 1,
    }


def test_server_search_and_reader_are_bounded_and_preserve_provenance():
    payload = {"content": [
        {"type": "web_search_tool_result", "content": [
            {"type": "web_search_result", "url": "https://example.org/a",
             "title": "Useful result", "encrypted_content": "opaque"},
            {"type": "web_search_result", "url": "http://example.org/b",
             "title": "Unsafe transport", "encrypted_content": "opaque"},
        ]},
        {"type": "web_search_tool_result",
         "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
    ]}
    results = parse_deepseek_search(payload)
    assert len(results) == 1 and results[0]["url"] == "https://example.org/a"
    reader = {"data": {
        "url": "https://example.org/a", "title": "Useful result",
        "content": "Abstract. This source directly studies model evaluation reliability under distribution shift. " * 6,
    }}
    row = parse_jina_reader(reader, result=results[0],
                            query="model evaluation AI trading research evidence", observed_at=NOW)
    assert "model evaluation reliability" in row["summary"]
    assert row["source"] == "web-search:deepseek+jina-reader"
    assert row["evidence_refs"][0].startswith("web-search:deepseek:")
    assert row["evidence_refs"][1].startswith("web-reader:jina:")


def test_reader_rejects_access_challenge_page():
    result = parse_deepseek_search({"content": [{"type": "web_search_tool_result", "content": [
        {"type": "web_search_result", "url": "https://example.org/a",
         "title": "Useful result", "encrypted_content": "opaque"}
    ]}]})[0]
    payload = {"data": {"url": result["url"], "title": "Just a moment...",
        "content": "Performing security verification. " * 30}}
    import pytest
    with pytest.raises(ValueError, match="access-challenge"):
        parse_jina_reader(payload, result=result, query="model evaluation", observed_at=NOW)


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
    async def cognitive_probe(self, task, max_tokens):
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
            "evidence_id": "rss-irrelevant", "source_kind": "rss", "source": "public-news-feed",
            "title": "Unrelated event", "summary": "Nothing about model evaluation.",
            "url": "https://example.org/unrelated", "observed_at": now.isoformat(),
            "source_revision": "rss-irrelevant", "evidence_refs": ["news:rss-irrelevant"],
            "publisher_feed": "https://example.org/feed", "published_at": now.isoformat(),
            "search_query": None, "search_rank": None,
        }], "failures": [], "observed_at": now.isoformat()}
    async def filter_for_goal(self, **kwargs):
        self.filter_calls += 1
        if self.filter_calls == 1:
            return {"items": [], "matches": [], "candidate_count": len(kwargs["candidates"])}
        assert kwargs["candidates"][0]["source_kind"] == "web-search"
        return {"items": list(kwargs["candidates"]), "matches": [{"evidence_id": "web:revision-1"}],
                "candidate_count": len(kwargs["candidates"])}

    async def search_web(self, *, goal_topic, now):
        self.search_calls += 1
        assert goal_topic == "rare"
        return {"items": [web_candidate()], "failures": [], "query": "rare AI trading research evidence"}


def test_goal_falls_back_to_web_search_then_publishes_selected_evidence(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, explorer, mind = V2Runtime(), FallbackExplorer(), GoalMind()
    worker = ContinuousContentLoop(
        runtime=runtime, state_root=legacy.state, artifacts_path=legacy.artifacts_path,
        site_root=legacy.site_root, base_url=legacy.base_url, curriculum_path=legacy.curriculum_path,
        feeds=legacy.feeds, interval_hours=legacy.interval_hours, fetcher=legacy.fetcher,
        publisher=publisher, mind=mind, explorer=explorer,
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
    assert final["initial_candidate_count"] == 1 and final["web_candidate_count"] == 1
