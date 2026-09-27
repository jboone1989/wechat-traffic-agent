import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
from test_web_editorial import site

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
COUNTER_QUOTE = "costs and regime changes can erase an apparent edge"
LONG_PARAGRAPH = "Counter-evidence: " + " ".join(
    ["live results can diverge from a backtest when assumptions fail"] * 45
)


class Runtime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")

    def __init__(self):
        self.calls = []

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        if task.startswith("LEARN_WRITING:"):
            return json.dumps({
                "techniques": [{"name": "State limits", "instruction": "State a concrete limitation.",
                    "before": "This works.", "after": "This may fail under changed assumptions.",
                    "check": "Locate an explicit limitation."}],
                "adaptation": "Keep the limitation tied to evidence.",
            })
        if task.startswith("SELECT_EVIDENCE:"):
            return json.dumps({
                "title": "Why this Bitcoin strategy may outperform",
                "evidence_id": "primary-1",
                "topic": "rare",
                "related_ids": ["a0"],
                "reader_question": "Could this Bitcoin strategy really outperform after the observed change?",
                "gap": "Explain the apparent edge while preserving its failure conditions.",
            })
        if task.startswith("CHECK_ARTICLE:"):
            return json.dumps({
                "grounded": True, "useful": True, "no_private_data": True,
                "no_investment_advice": True,
                "applications": [{"technique_index": 0, "article_quote": "Counter-evidence:",
                    "explanation": "The article states a concrete limitation before drawing a conclusion."}],
                "support": [{"section_index": 0, "source_id": "web:counter-1",
                    "source_quote": COUNTER_QUOTE}],
                "next_practice": "Compare another failure case against the same mechanism.",
                "rationale": "The draft explicitly handles contrary evidence and uncertainty.",
            })
        return json.dumps({
            "title": "Why this Bitcoin strategy may outperform",
            "summary": "A bounded explanation of an apparent strategy edge and its limitations.",
            "sections": [{"heading": "Analysis", "paragraphs": [LONG_PARAGRAPH]}],
            "topic": "Why this Bitcoin strategy may outperform", "source_urls": [],
            "disclosure": None,
        })

    def persist(self):
        pass


PRIMARY = {
    "evidence_id": "primary-1", "source_kind": "web-search", "source": "runtime-web-search",
    "title": "Bitcoin strategy reports an apparent edge",
    "summary": "A backtest reports stronger returns under the tested assumptions.",
    "url": "https://example.org/primary", "observed_at": NOW.isoformat(),
    "source_revision": "primary-r1", "evidence_refs": ["runtime-receipt:primary-1"],
}
COUNTER = {
    "evidence_id": "web:counter-1", "source_kind": "web-search", "source": "runtime-web-search",
    "title": "Why backtest edges can fail live",
    "summary": "Trading costs and regime changes can erase an apparent edge in live deployment.",
    "url": "https://example.org/counter", "observed_at": NOW.isoformat(),
    "source_revision": "counter-r1", "evidence_refs": ["runtime-receipt:counter-1"],
    "search_query": "strategy limitations", "search_rank": 1,
}


class Explorer:
    def __init__(self, *, counter=True):
        self.counter = counter

    async def collect(self, *, now, active):
        return {"items": [PRIMARY], "failures": [], "observed_at": now.isoformat()}

    async def filter_for_goal(self, *, runtime, goal_topic, topic_context, candidates):
        return {"items": list(candidates), "matches": [], "candidate_count": len(candidates)}

    async def search_counter_evidence(self, *, subject, now):
        return {"items": [COUNTER] if self.counter else [], "failures": [], "query": "strategy limitations"}

    async def filter_counter_evidence(self, *, runtime, primary, candidates):
        if not candidates:
            return {"items": [], "matches": [], "candidate_count": 0}
        return {"items": [COUNTER], "matches": [{
            "evidence_id": "web:counter-1", "evidence_quote": COUNTER_QUOTE,
            "reason": "It states a concrete live-deployment failure mode for the apparent edge.",
        }], "candidate_count": 1}


def worker(tmp_path, *, counter=True):
    artifacts, web = site(tmp_path)
    curriculum = tmp_path / "practice.md"
    curriculum.write_text("Practice stating bounded limitations.")
    runtime = Runtime()
    loop = ContinuousContentLoop(
        runtime=runtime, state_root=tmp_path / "state", artifacts_path=artifacts,
        site_root=web, base_url="https://example.com", curriculum_path=curriculum,
        feeds=["https://example.org/feed"], explorer=Explorer(counter=counter), publisher=None,
    )
    return runtime, loop


def test_goal_loop_high_risk_content_requires_and_uses_counter_evidence(tmp_path):
    runtime, loop = worker(tmp_path, counter=True)
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "DRAFT_CREATED"
    assert result["evidence_floor"]["status"] == "PASS"
    assert result["evidence_floor"]["required"] is True
    assert result["evidence_floor"]["independence_checked"] is False
    assert any("counter_evidence" in call for call in runtime.calls if not call.startswith("LEARN_WRITING:"))


def test_goal_loop_missing_counter_does_not_kill_production_before_drafting(tmp_path):
    runtime, loop = worker(tmp_path, counter=False)
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] in {"DRAFT_CREATED", "HELD_BY_CHECKS"}
    assert result["status"] != "ERROR"
    assert any(call.startswith("CHECK_ARTICLE:") for call in runtime.calls)
    assert len(loop.inbox.list_records()) == 1
    attempt = json.loads((tmp_path / "state/content/continuous/cycles" / result["cycle"] / "evidence_floor_attempt.json").read_text())
    assert attempt["status"] == "MISSING"
    assert attempt["required"] is True


def test_real_search_demand_controls_topic_when_virtualbrain_has_no_goal(tmp_path):
    from datetime import timedelta
    from wechat_traffic_agent.traffic_learning import SearchDemandObservation, SearchDemandStore

    runtime, loop = worker(tmp_path, counter=True)
    store = SearchDemandStore(tmp_path / "state/content/search_demand")
    store.record(SearchDemandObservation(
        site="https://example.com", query="common topic", page="https://example.com/articles/article-1/",
        clicks=12, impressions=120, ctr=0.1, position=5.0,
        window_start=NOW - timedelta(days=8), window_end=NOW - timedelta(days=1),
        observed_at=NOW, revision=1, evidence_ref="gsc:common-topic:1",
    ))
    original = runtime.cognitive_probe
    selections = 0

    async def traffic_selected(task, max_tokens, output_schema=None):
        nonlocal selections
        raw = await original(task, max_tokens)
        if task.startswith("SELECT_EVIDENCE:"):
            selections += 1
            row = json.loads(raw)
            if selections > 1:
                row["topic"] = "common"
                row["related_ids"] = ["a1"]
            return json.dumps(row)
        return raw

    runtime.cognitive_probe = traffic_selected
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "DRAFT_CREATED"
    assert selections == 2
    assert result["traffic_context"]["selected_topic"] == "common"
    assert result["traffic_context"]["selection_authority"] == "TRAFFIC_OPPORTUNITY"
    assert result["content_strategy"]["topic"] == "common"
