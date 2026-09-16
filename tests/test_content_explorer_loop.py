import asyncio
import json
from pathlib import Path

from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
from test_content_explorer import candidate
from test_content_loop import NOW, Runtime, setup


class V2Runtime(Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({"title": f"Explaining evidence question {self.number}",
                "evidence_id": f"news-{self.number}", "topic": "rare", "related_ids": ["a0"],
                "reader_question": "What does this observation actually support?",
                "gap": "Connect fresh evidence to existing interpretation."})
        return await super().cognitive_probe(task, max_tokens)


class GoalMind:
    def __init__(self): self.planned = False
    def plan_for_cycle(self, *, cycle, available_topics):
        self.planned = True
        return {"cycle": cycle, "goal": {"origin": "INTEREST", "interest_refs": ["common"]}, "required_topic": "common"}
    def observe_process_experience(self, **kwargs): return {"status": "OBSERVED", "topic": kwargs["topic"]}


class UnboundGoalMind:
    def plan_for_cycle(self, *, cycle, available_topics):
        return {"cycle": cycle, "goal": {"origin": "INTEREST", "interest_refs": ["missing-topic"]},
                "required_topic": None}


class Explorer:
    def __init__(self, mind=None, *, empty=False, fail=False):
        self.mind, self.empty, self.fail = mind, empty, fail
        self.collect_calls = self.filter_calls = 0
    async def collect(self, *, now, active):
        if self.mind: assert self.mind.planned and active is True
        self.collect_calls += 1
        return {"items": [candidate()], "failures": [], "observed_at": now.isoformat()}
    async def filter_for_goal(self, **kwargs):
        self.filter_calls += 1
        if self.fail: raise RuntimeError("simulated explorer failure")
        return {"items": [] if self.empty else [candidate()], "matches": [], "candidate_count": 1}


def v2(worker, runtime, publisher, *, mind=None, explorer=None):
    return ContinuousContentLoop(runtime=runtime, state_root=worker.state, artifacts_path=worker.artifacts_path,
        site_root=worker.site_root, base_url=worker.base_url, curriculum_path=worker.curriculum_path,
        feeds=worker.feeds, interval_hours=worker.interval_hours, fetcher=worker.fetcher,
        publisher=publisher, mind=mind, explorer=explorer)


def test_goal_without_matching_evidence_falls_back_to_viable_output(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = V2Runtime()
    mind, explorer = GoalMind(), Explorer(empty=True); explorer.mind = mind
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=explorer)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert result["traffic_context"]["selection_authority"] == "OUTPUT_FALLBACK"
    assert explorer.collect_calls == explorer.filter_calls == 1
    assert len(worker.inbox.list_records()) == 1


def test_unbound_goal_fails_closed_before_evidence(tmp_path):
    runtime, legacy, publisher = setup(tmp_path)
    explorer = Explorer()
    worker = v2(legacy, runtime, publisher, mind=UnboundGoalMind(), explorer=explorer)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "NO_RELEVANT_EVIDENCE"
    assert result["reason"] == "UNBOUND_GOAL_TOPIC"
    assert explorer.collect_calls == explorer.filter_calls == 0
    assert runtime.calls == []


def test_restart_does_not_repeat_exploration_or_publication(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, explorer = V2Runtime(), Explorer()
    worker = v2(legacy, runtime, publisher, explorer=explorer)
    first = asyncio.run(worker.run_once(now=NOW))
    assert first["status"] == "PUBLISHED"
    count = len(json.loads(publisher.inventory_path().read_text()))
    worker2 = v2(legacy, V2Runtime(), publisher, explorer=explorer)
    replay = asyncio.run(worker2.run_once(now=NOW))
    assert replay["status"] == "ALREADY_PROCESSED" and replay["previous_status"] == "PUBLISHED"
    assert explorer.collect_calls == explorer.filter_calls == 1
    assert len(json.loads(publisher.inventory_path().read_text())) == count


def test_explorer_failure_never_publishes(tmp_path):
    runtime, legacy, publisher = setup(tmp_path)
    before = (publisher.public / "current").resolve()
    mind, explorer = GoalMind(), Explorer(fail=True); explorer.mind = mind
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=explorer)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "ERROR" and result["error_type"] == "RuntimeError"
    assert runtime.calls == [] and worker.inbox.list_records() == ()
    assert (publisher.public / "current").resolve() == before


class NewTopicMind:
    def plan_for_cycle(self, *, cycle, available_topics, **kwargs):
        assert "monetary-policy" not in available_topics
        return {
            "cycle": cycle,
            "goal": {"origin": "INQUIRY", "inquiry_refs": ["fed-question"]},
            "required_topic": "monetary-policy",
        }

    def observe_process_experience(self, **kwargs):
        return {"status": "OBSERVED", "topic": kwargs["topic"]}


class NewTopicExplorer:
    def __init__(self):
        self.row = {
            "evidence_id": "news-new-topic", "source_kind": "rss", "source": "public-news-feed",
            "title": "Central bank policy path changed",
            "summary": "A fresh central-bank policy signal matters for crypto liquidity.",
            "url": "https://example.org/policy", "observed_at": NOW.isoformat(),
            "source_revision": "news-new-topic", "evidence_refs": ["news:news-new-topic"],
            "publisher_feed": "https://example.org/feed", "published_at": NOW.isoformat(),
            "search_query": None, "search_rank": None,
        }

    async def collect(self, *, now, active):
        return {"items": [self.row], "failures": [], "observed_at": now.isoformat()}

    async def filter_for_goal(self, **kwargs):
        return {"items": [self.row], "matches": [], "candidate_count": 1}


class NewTopicRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({
                "title": "What a changed policy path means for crypto liquidity",
                "evidence_id": "news-new-topic", "topic": "monetary-policy", "related_ids": [],
                "reader_question": "How could a changed policy path affect crypto liquidity?",
                "gap": "Explain the mechanism without pretending the market outcome is already known.",
            })
        if task.startswith("CHECK_ARTICLE:"):
            self.calls.append(task)
            return json.dumps({
                "grounded": True, "useful": True, "no_private_data": True,
                "no_investment_advice": True,
                "applications": [{
                    "technique_index": 0,
                    "article_quote": "Explanation of supplied evidence",
                    "explanation": "The article visibly separates evidence from interpretation.",
                }],
                "support": [{
                    "section_index": 0, "source_id": "news-new-topic",
                    "source_quote": "A fresh central-bank policy signal matters for crypto liquidity.",
                }],
                "next_practice": "Track whether later evidence changes the policy-liquidity interpretation.",
                "rationale": "The draft is grounded in the supplied policy observation and stays non-advisory.",
            })
        if task.startswith("LEARN_WRITING:"):
            return await super().cognitive_probe(task, max_tokens)
        self.calls.append(task)
        paragraph = " ".join([
            "Explanation of supplied evidence separates the observed policy signal from interpretation and keeps uncertainty explicit."
            for _ in range(45)
        ])
        return json.dumps({
            "title": "What a changed policy path means for crypto liquidity",
            "summary": "A bounded explanation of how monetary policy can alter crypto liquidity conditions.",
            "sections": [{"heading": "What changed", "paragraphs": [paragraph]}],
            "topic": "What a changed policy path means for crypto liquidity",
            "source_urls": ["https://example.org/policy"],
            "disclosure": None,
        })


def test_virtualbrain_can_create_new_topic_and_website_materializes_it(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    worker = v2(
        legacy, NewTopicRuntime(), publisher,
        mind=NewTopicMind(), explorer=NewTopicExplorer(),
    )
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert result["traffic_context"]["selected_topic"] == "monetary-policy"
    assert result["content_strategy"]["topic"] == "monetary-policy"
    assert result["mind_experience"]["topic"] == "monetary-policy"
    published = json.loads(publisher.inventory_path().read_text())
    assert published[-1]["tags"] == ["monetary-policy"]
    public_root = Path(result["publication"]["public_root"])
    assert (public_root / "topics" / "monetary-policy" / "index.html").exists()
