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

class BeliefRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("FORM_WORLDVIEW:"):
            self.calls.append(task)
            return json.dumps({
                "action": "COMMIT",
                "proposition": "Better validation evidence reduces uncertainty about model reliability.",
                "stance": 0.7,
                "reliability": 0.65,
                "support": 0.75,
                "rationale": "The selected evidence is relevant enough for a bounded working belief, not certainty.",
            })
        if task.startswith("DECIDE_EXPRESSION:"):
            self.calls.append(task)
            return json.dumps({
                "cognition_state": "BELIEF_CHANGED", "worth_saying": True,
                "expression_kind": "LONGFORM",
                "what_changed": "The selected evidence created a bounded new working belief.",
                "what_matters": "The change is useful because it clarifies how evidence should affect reliability judgments.",
                "belief_proposition": "Better validation evidence reduces uncertainty about model reliability.",
                "rationale": "A newly persisted working belief is materially different enough to explain now.",
            })
        if "Create one original, useful web-ready content artifact" in task and "agent_expression_context" in task:
            self.calls.append(task)
            paragraph = " ".join([
                "I think this evidence matters because it changes how I weigh model reliability; Explanation of supplied evidence separates observation from my interpretation."
                for _ in range(40)
            ])
            return json.dumps({
                "title": f"Explaining evidence question {self.number}",
                "summary": "My bounded interpretation of fresh validation evidence.",
                "sections": [{"heading": "My current view", "paragraphs": [paragraph]}],
                "topic": f"Explaining evidence question {self.number}",
                "source_urls": [], "disclosure": None,
            })
        return await super().cognitive_probe(task, max_tokens)


class BeliefMind:
    def __init__(self):
        self.recorded = []

    def plan_for_cycle(self, *, cycle, available_topics):
        return {"cycle": cycle, "goal": {"origin": "INTEREST", "interest_refs": ["rare"]},
                "required_topic": "rare"}

    def public_projection(self):
        return {"beliefs": []}

    def record_research_belief(self, **kwargs):
        self.recorded.append(kwargs)
        if not kwargs["commit"]:
            return {"status": "DEFERRED", "topic": kwargs["topic"],
                    "proposition": kwargs["proposition"]}
        return {"status": "FORMED", "topic": kwargs["topic"],
                "proposition": kwargs["proposition"], "stance": 0.07, "confidence": 0.065}

    def observe_process_experience(self, **kwargs):
        return {"status": "OBSERVED", "topic": kwargs["topic"]}


def test_selected_evidence_updates_persistent_mind_before_expression(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, mind, evidence_explorer = BeliefRuntime(), BeliefMind(), Explorer()
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=evidence_explorer)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert result["worldview_update"]["status"] == "FORMED"
    assert len(mind.recorded) == 1
    recorded = mind.recorded[0]
    assert recorded["topic"] == "rare"
    assert recorded["source_evidence"]["evidence_id"] == "news-1"
    assert recorded["commit"] is True
    cycle = Path(legacy.state) / "content/continuous/cycles" / result["cycle"]
    decision = json.loads((cycle / "worldview_decision.json").read_text())
    update = json.loads((cycle / "worldview_update.json").read_text())
    assert decision["action"] == "COMMIT"
    assert update["proposition"] == decision["proposition"]
    form_call = next(call for call in runtime.calls if call.startswith("FORM_WORLDVIEW:"))
    compose_call = next(call for call in runtime.calls if 'Create one original, useful web-ready content artifact' in call)
    assert runtime.calls.index(form_call) < runtime.calls.index(compose_call)


class QuietBeliefRuntime(BeliefRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("FORM_WORLDVIEW:"):
            self.calls.append(task)
            return json.dumps({
                "action": "DEFER",
                "proposition": "Better validation evidence reduces uncertainty about model reliability.",
                "stance": 0.2, "reliability": 0.4, "support": 0.45,
                "rationale": "One observation is worth retaining in the inquiry but not enough for a durable conclusion.",
            })
        if task.startswith("DECIDE_EXPRESSION:"):
            self.calls.append(task)
            return json.dumps({
                "cognition_state": "INVESTIGATING", "worth_saying": False,
                "expression_kind": "NONE",
                "what_changed": "The inquiry gained evidence but no durable worldview claim was formed.",
                "what_matters": "More evidence is needed before turning the observation into a public interpretation.",
                "belief_proposition": None,
                "rationale": "The agent learned something without having enough new understanding to justify publication.",
            })
        return await V2Runtime.cognitive_probe(self, task, max_tokens)


def test_cognition_can_finish_without_forcing_publication(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, mind = QuietBeliefRuntime(), BeliefMind()
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=Explorer())
    before = len(json.loads(publisher.inventory_path().read_text()))
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "COGNITION_ONLY"
    assert result["worldview_update"]["status"] == "DEFERRED"
    assert result["cognition"]["expression_kind"] == "NONE"
    assert len(json.loads(publisher.inventory_path().read_text())) == before
    assert worker.inbox.list_records() == ()
    assert not any("Create one original, useful web-ready content artifact" in call for call in runtime.calls)
    assert not any(call.startswith("LEARN_WRITING:") for call in runtime.calls)
    replay = asyncio.run(v2(legacy, QuietBeliefRuntime(), publisher, mind=mind, explorer=Explorer()).run_once(now=NOW))
    assert replay["status"] == "ALREADY_PROCESSED"
    assert replay["previous_status"] == "COGNITION_ONLY"


def test_expression_decision_precedes_writing_learning_and_composition(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = BeliefRuntime()
    result = asyncio.run(v2(legacy, runtime, publisher, mind=BeliefMind(), explorer=Explorer()).run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    decide = next(call for call in runtime.calls if call.startswith("DECIDE_EXPRESSION:"))
    learn = next(call for call in runtime.calls if call.startswith("LEARN_WRITING:"))
    compose = next(call for call in runtime.calls if "Create one original, useful web-ready content artifact" in call)
    assert runtime.calls.index(decide) < runtime.calls.index(learn) < runtime.calls.index(compose)


def test_published_expression_is_grounded_in_persisted_agent_perspective(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = BeliefRuntime()
    result = asyncio.run(v2(legacy, runtime, publisher, mind=BeliefMind(), explorer=Explorer()).run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    compose = next(call for call in runtime.calls if "Create one original, useful web-ready content artifact" in call)
    assert 'agent_expression_context' in compose
    assert 'persistent Content Agent' in compose
    assert 'Better validation evidence reduces uncertainty about model reliability.' in compose
    assert 'Use first person' in compose
    assert 'not an academic paper' in compose
    assert 'do not invent stronger conviction' in compose


class NeutralExpressionRuntime(BeliefRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if "Create one original, useful web-ready content artifact" in task and "agent_expression_context" in task:
            self.calls.append(task)
            paragraph = " ".join([
                "Explanation of supplied evidence separates the observed validation result from interpretation while preserving uncertainty."
                for _ in range(40)
            ])
            return json.dumps({
                "title": f"Explaining evidence question {self.number}",
                "summary": "A bounded interpretation of fresh validation evidence.",
                "sections": [{"heading": "Current interpretation", "paragraphs": [paragraph]}],
                "topic": f"Explaining evidence question {self.number}",
                "source_urls": [], "disclosure": None,
            })
        return await super().cognitive_probe(task, max_tokens)


def test_agent_perspective_expression_is_required_for_publication(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = NeutralExpressionRuntime()
    before = len(json.loads(publisher.inventory_path().read_text()))
    result = asyncio.run(v2(legacy, runtime, publisher, mind=BeliefMind(), explorer=Explorer()).run_once(now=NOW))
    assert result["status"] == "HELD_BY_CHECKS"
    assert result["perspective_expression"] is False
    assert len(json.loads(publisher.inventory_path().read_text())) == before


class PredictiveBeliefRuntime(BeliefRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("PROPOSE_DATED_PREDICTION:"):
            self.calls.append(task)
            return json.dumps({
                "action": "RECORD",
                "statement": "Within 24 hours, an additional validation result will reduce uncertainty about the same reliability question.",
                "horizon_hours": 24,
                "confidence": 0.62,
                "verification_query": "latest model validation reliability evidence",
                "falsifier": "No additional validation result appears, or new evidence increases rather than reduces uncertainty.",
                "rationale": "The durable belief has a bounded near-term implication that later public evidence can test.",
            })
        return await super().cognitive_probe(task, max_tokens)


def test_worldview_change_can_create_restart_stable_dated_prediction_before_expression(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = PredictiveBeliefRuntime()
    worker = v2(legacy, runtime, publisher, mind=BeliefMind(), explorer=Explorer())
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    prediction = result["prediction"]
    assert prediction["status"] == "OPEN"
    assert prediction["statement"].startswith("Within 24 hours")
    assert prediction["proposition"] == result["worldview_update"]["proposition"]
    cycle = Path(legacy.state) / "content/continuous/cycles" / result["cycle"]
    assert json.loads((cycle / "prediction.json").read_text())["prediction_id"] == prediction["prediction_id"]
    from wechat_traffic_agent.prospective_predictions import ProspectivePredictionLedger
    restarted = ProspectivePredictionLedger(legacy.state)
    assert restarted.open()[0].prediction_id == prediction["prediction_id"]
    form = next(call for call in runtime.calls if call.startswith("FORM_WORLDVIEW:"))
    predict = next(call for call in runtime.calls if call.startswith("PROPOSE_DATED_PREDICTION:"))
    decide = next(call for call in runtime.calls if call.startswith("DECIDE_EXPRESSION:"))
    assert runtime.calls.index(form) < runtime.calls.index(predict) < runtime.calls.index(decide)
    compose = next(call for call in runtime.calls if "Create one original, useful web-ready content artifact" in call)
    assert prediction["statement"] in compose
