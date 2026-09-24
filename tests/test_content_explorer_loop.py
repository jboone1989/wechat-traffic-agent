import asyncio
import json
from pathlib import Path

import pytest

from wechat_traffic_agent.content_goal_loop import (
    ContinuousContentLoop, MissionDiscoveryDecision, _explicit_hypothetical_note_sections,
    _market_expression_instruction, _market_companion_evidence,
    _market_expression_fingerprint, _candidate_available_for_pool,
    _normalize_mission_discovery,
)
from test_content_explorer import candidate
from test_content_loop import NOW, Runtime, setup
from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection


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


class ShortThoughtRuntime(BeliefRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("DECIDE_EXPRESSION:"):
            self.calls.append(task)
            return json.dumps({
                "cognition_state": "BELIEF_CHANGED", "worth_saying": True,
                "expression_kind": "THOUGHT",
                "what_changed": "The fresh evidence changed one bounded part of my current interpretation.",
                "what_matters": "The point is useful now even though it does not need a full-length article.",
                "belief_proposition": "Better validation evidence reduces uncertainty about model reliability.",
                "rationale": "A concise public thought is enough; padding it would reduce signal rather than add value.",
            })
        if "Create one original, useful web-ready content artifact" in task and "agent_expression_context" in task:
            self.calls.append(task)
            paragraph = (
                "I noticed a small but useful shift. Explanation of supplied evidence separates what was observed from my interpretation. "
                "I would rather preserve that uncertainty in a short note than inflate it into a long article."
            )
            return json.dumps({
                "title": f"A small validation thought {self.number}",
                "summary": "A concise evidence-grounded reflection from the persistent agent.",
                "sections": [{"heading": "Thought", "paragraphs": [paragraph]}],
                "topic": f"Explaining evidence question {self.number}", "source_urls": [], "disclosure": None,
            })
        return await super().cognitive_probe(task, max_tokens)


def test_short_thought_publishes_as_note_without_padding_to_longform(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = ShortThoughtRuntime()
    result = asyncio.run(v2(legacy, runtime, publisher, mind=BeliefMind(), explorer=Explorer()).run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    cycle = Path(legacy.state) / "content/continuous/cycles" / result["cycle"]
    experience = json.loads((cycle / "experience.json").read_text())
    assert experience["expression_kind"] == "THOUGHT"
    assert experience["minimum_words"] is None
    assert experience["word_count"] < 350
    published = json.loads(publisher.inventory_path().read_text())
    assert published[-1]["kind"] == "note"
    public_root = Path(result["publication"]["public_root"])
    assert (public_root / "notes" / published[-1]["slug"] / "index.html").exists()


class RepairSilentBeliefRuntime(BeliefRuntime):
    def __init__(self):
        super().__init__()
        self.expression_attempts = 0

    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("DECIDE_EXPRESSION:"):
            self.calls.append(task)
            self.expression_attempts += 1
            if self.expression_attempts == 1:
                return json.dumps({
                    "cognition_state": "BELIEF_CHANGED", "worth_saying": False,
                    "expression_kind": "NONE",
                    "what_changed": "A weak but durable working belief was persisted.",
                    "what_matters": "Its confidence is low and readers should see that uncertainty.",
                    "belief_proposition": "Better validation evidence reduces uncertainty about model reliability.",
                    "rationale": "The belief is weak, so the first attempt tried to stay silent.",
                })
            return json.dumps({
                "cognition_state": "BELIEF_CHANGED", "worth_saying": True,
                "expression_kind": "THOUGHT",
                "what_changed": "A weak but durable working belief was persisted.",
                "what_matters": "Its confidence is low and readers should see that uncertainty.",
                "belief_proposition": "Better validation evidence reduces uncertainty about model reliability.",
                "rationale": "A durable worldview change should leave a concise public trace without pretending certainty.",
            })
        if "Create one original, useful web-ready content artifact" in task and "agent_expression_context" in task:
            self.calls.append(task)
            return json.dumps({
                "title": "A tentative thought on validation evidence",
                "summary": "I have a weak working view, and I want to preserve the uncertainty rather than pad it into an article.",
                "sections": [{"heading": "Current thought", "paragraphs": [
                    "I think the fresh evidence slightly changes how I weigh model reliability. Explanation of supplied evidence remains separate from my interpretation, and this view is deliberately tentative."
                ]}],
                "topic": f"Explaining evidence question {self.number}", "source_urls": [], "disclosure": None,
            })
        return await super().cognitive_probe(task, max_tokens)


def test_persisted_worldview_change_cannot_disappear_into_none(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = RepairSilentBeliefRuntime()
    result = asyncio.run(v2(legacy, runtime, publisher, mind=BeliefMind(), explorer=Explorer()).run_once(now=NOW))
    assert runtime.expression_attempts == 2
    assert result["status"] == "PUBLISHED"
    assert result["cognition"]["expression_kind"] == "THOUGHT"
    published = json.loads(publisher.inventory_path().read_text())
    assert published[-1]["kind"] == "note"


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


def test_note_grounding_exempts_only_explicit_hypothetical_sections():
    note = ContentArtifact(
        artifact_id="note-hypothetical",
        kind=ContentKind.NOTE,
        slug="hypothetical-check",
        title="A bounded hypothetical check",
        summary="A clearly labeled hypothetical section.",
        sections=(
            ContentSection(heading="Observed", paragraphs=("The source reports a measurable observation.",)),
            ContentSection(heading="Example", paragraphs=(
                "The following values are hypothetical and only illustrate the check.",
                "Count, hypothetical: 10. Falsified if the real count differs.",
            )),
        ),
        created_at=NOW,
        public_safe=False,
    )
    assert _explicit_hypothetical_note_sections(note) == {1}
    not_explicit = note.model_copy(update={
        "sections": (
            note.sections[0],
            ContentSection(heading="Example", paragraphs=(
                "Suppose the count were 10.",
                "This would change the interpretation.",
            )),
        ),
    })
    assert _explicit_hypothetical_note_sections(not_explicit) == set()

class MethodExpressionMind:
    def __init__(self):
        self.world_observations = []

    def plan_for_cycle(self, *, cycle, available_topics, world_observations=()):
        self.world_observations = list(world_observations)
        return {
            "cycle": cycle,
            "goal": {"origin": "INQUIRY", "inquiry_refs": ["missing-goal"]},
            "required_topic": "missing-goal-topic",
            "world_observations": self.world_observations,
        }

    def public_projection(self):
        return {"beliefs": [], "inquiries": []}

    def record_research_belief(self, **kwargs):
        return {"status": "DEFERRED", "topic": kwargs["topic"], "proposition": kwargs["proposition"]}

    def observe_process_experience(self, **kwargs):
        return {"status": "OBSERVED", "topic": kwargs["topic"]}


class MethodExpressionExplorer:
    def __init__(self, rows):
        self.rows = rows

    async def collect(self, *, now, active):
        return {"items": list(self.rows), "failures": [], "observed_at": now.isoformat()}

    async def filter_for_goal(self, **kwargs):
        return {"items": [], "matches": [], "candidate_count": len(kwargs.get("candidates") or [])}


class MethodExpressionWorld:
    def __init__(self, rows):
        self.rows = rows

    async def collect(self, **kwargs):
        method = self.rows[1]
        return {
            "items": list(self.rows), "failures": [], "observed_at": NOW.isoformat(),
            "observations": [{
                "evidence_id": method["evidence_id"], "topic": "term-premium-decomposition-model",
                "learning_kind": "METHOD", "importance": 0.9, "novelty": 0.8,
                "why_now": "This turns an unresolved decomposition question into a testable method.",
                "question": "How should I test this method before adopting it?",
                "evidence_refs": method["evidence_refs"],
            }],
        }


class MethodExpressionRuntime(Runtime):
    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
        if task.startswith("SELECT_EVIDENCE:"):
            return json.dumps({
                "title": "What the inflation report does not answer",
                "evidence_id": "rss-fallback", "topic": "inflation-fallback", "related_ids": [],
                "reader_question": "What does this inflation report actually establish?",
                "gap": "Keep the fallback story bounded to what its source really reports.",
            })
        if task.startswith("FORM_WORLDVIEW:"):
            return json.dumps({
                "action": "DEFER", "proposition": "The fallback report identifies the binding research limitation.",
                "stance": 0.0, "reliability": 0.0, "support": 0.0,
                "rationale": "The fallback source does not contain evidence about the unresolved method question.",
            })
        if task.startswith("DECIDE_EXPRESSION:"):
            return json.dumps({
                "cognition_state": "INVESTIGATING", "worth_saying": True,
                "expression_kind": "METHOD_NOTE",
                "what_changed": "A concrete term-premium decomposition appeared as a method candidate worth testing.",
                "what_matters": "It can turn an unresolved decomposition question into a reproducible test before adoption.",
                "belief_proposition": None,
                "rationale": "The useful output is the discovered method candidate, not a new durable belief.",
            })
        if task.startswith("BIND_METHOD_EXPRESSION:"):
            return json.dumps({
                "title": "Testing a term-premium decomposition before adopting it",
                "evidence_id": "method-1", "topic": "term-premium-decomposition-model", "related_ids": [],
                "reader_question": "How should I test this term-premium decomposition before adopting it?",
                "gap": "Separate discovery of the method from evidence that it works for this agent's inquiry.",
            })
        if task.startswith("LEARN_WRITING:"):
            return json.dumps({"techniques": [{
                "name": "Separate observations", "instruction": "Separate source facts from interpretation.",
                "before": "This method works.", "after": "I found a candidate method; I still need to test it.",
                "check": "Keep discovery distinct from adoption.",
            }], "adaptation": "Keep method discovery explicitly provisional."})
        if "Create one original, useful web-ready content artifact" in task:
            return json.dumps({
                "title": "Testing a term-premium decomposition before adopting it",
                "summary": "I found a concrete decomposition method, but discovery is not adoption.",
                "sections": [{"heading": "Candidate method", "paragraphs": [
                    "I found a candidate method worth testing. A concrete term-premium model separates expected short rates from term premium. My next step is to reproduce its inputs and test the stated assumptions before I adopt it."
                ]}],
                "topic": "Testing a term-premium decomposition before adopting it",
                "source_urls": ["https://example.org/method"], "disclosure": None,
            })
        if task.startswith("CHECK_ARTICLE:"):
            return json.dumps({
                "grounded": True, "useful": True, "no_private_data": True, "no_investment_advice": True,
                "applications": [{
                    "technique_index": 0, "article_quote": "I found a candidate method worth testing.",
                    "explanation": "The note explicitly separates discovery from adoption.",
                }],
                "support": [{
                    "section_index": 0, "source_id": "method-1",
                    "source_quote": "A concrete term-premium model separates expected short rates from term premium.",
                }],
                "next_practice": "Reproduce the model inputs before any adoption decision.",
                "rationale": "The method note is grounded in the method evidence and remains provisional.",
            })
        return await super().cognitive_probe(task, max_tokens)


def test_method_note_rebinds_to_observed_method_evidence_instead_of_unrelated_output_fallback(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    rss = {
        "evidence_id": "rss-fallback", "source_kind": "rss", "source": "public-news-feed",
        "title": "Inflation rose", "summary": "A publisher reports that inflation rose.",
        "url": "https://example.org/inflation", "observed_at": NOW.isoformat(), "published_at": NOW.isoformat(),
        "source_revision": "rss-fallback", "evidence_refs": ["news:rss-fallback"],
        "publisher_feed": "https://example.org/feed", "search_query": None, "search_rank": None,
        "learning_kind": "WORLD_EVENT",
    }
    method = {
        "evidence_id": "method-1", "source_kind": "web-search", "source": "runtime:web.fetch",
        "title": "Term premium model",
        "summary": "A concrete term-premium model separates expected short rates from term premium.",
        "url": "https://example.org/method", "observed_at": NOW.isoformat(), "published_at": None,
        "source_revision": "method-1", "evidence_refs": ["web:method-1"],
        "publisher_feed": None, "search_query": "term premium model", "search_rank": 1,
        "learning_kind": "METHOD",
    }
    rows = [rss, method]
    runtime = MethodExpressionRuntime()
    worker = ContinuousContentLoop(
        runtime=runtime, state_root=legacy.state, artifacts_path=legacy.artifacts_path,
        site_root=legacy.site_root, base_url=legacy.base_url, curriculum_path=legacy.curriculum_path,
        feeds=legacy.feeds, interval_hours=legacy.interval_hours, fetcher=legacy.fetcher,
        publisher=publisher, mind=MethodExpressionMind(), explorer=MethodExpressionExplorer(rows),
        world_feed=MethodExpressionWorld(rows),
    )
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert result["cognition"]["expression_kind"] == "METHOD_NOTE"
    assert result["traffic_context"]["selection_authority"] == "COGNITION_EVIDENCE_REBIND"
    cycle = Path(legacy.state) / "content/continuous/cycles" / result["cycle"]
    binding = json.loads((cycle / "expression_binding.json").read_text())
    assert binding["original_evidence_id"] == "rss-fallback"
    assert binding["selection"]["evidence_id"] == "method-1"
    experience = json.loads((cycle / "experience.json").read_text())
    assert experience["source_fingerprint"] != "rss-fallback"
    compose = next(call for call in runtime.calls if "Create one original, useful web-ready content artifact" in call)
    assert '"evidence_id": "method-1"' in compose
    assert '"evidence_id": "rss-fallback"' not in compose.split('"source_material":', 1)[1]


class CryptoMissionRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({
                "title": "Explaining evidence question 1", "evidence_id": "crypto-market-1",
                "topic": "crypto-market", "related_ids": ["a0"],
                "reader_question": "What does the current crypto market snapshot actually tell us?",
                "gap": "Separate a point-in-time market observation from any trading conclusion.",
            })
        return await super().cognitive_probe(task, max_tokens)


class CryptoMissionExplorer(Explorer):
    row = candidate("crypto-market-1", "BTC, ETH and SOL current market snapshot") | {
        "source_kind": "public-api", "source": "OKX public market API",
        "summary": "BTC, ETH and SOL have different 24-hour moves in a point-in-time OKX snapshot; this is not a trading signal.",
        "evidence_refs": ["okx-public-api:sha256:test"],
    }
    async def collect(self, *, now, active):
        assert active is True
        self.collect_calls += 1
        return {"items": [self.row], "failures": [], "observed_at": now.isoformat()}
    async def filter_for_goal(self, **kwargs):
        assert kwargs["goal_topic"] == "crypto-market"
        self.filter_calls += 1
        return {"items": [self.row], "matches": [], "candidate_count": 1}


def test_one_shot_crypto_bootstrap_sets_direction_without_replacing_future_autonomy(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, explorer = CryptoMissionRuntime(), CryptoMissionExplorer()
    worker = ContinuousContentLoop(
        runtime=runtime, state_root=legacy.state, artifacts_path=legacy.artifacts_path,
        site_root=legacy.site_root, base_url=legacy.base_url, curriculum_path=legacy.curriculum_path,
        feeds=legacy.feeds, interval_hours=legacy.interval_hours, fetcher=legacy.fetcher,
        publisher=publisher, explorer=explorer,
    )
    result = asyncio.run(worker.run_once(now=NOW, cycle_id="v2-bootstrap-1789491601", focus_topic="crypto-market"))
    assert result["status"] == "PUBLISHED"
    assert result["mind_plan"]["required_topic"] == "crypto-market"
    assert result["mind_plan"]["goal"]["origin"] == "BOOTSTRAP_FOCUS"
    assert result["mind_plan"]["endogenous_goal"] is None
    # With no persistent cognition adapter in this fixture, the existing default remains longform.
    # The mission selects subject matter; it does not force Note vs longform.
    published = json.loads(publisher.inventory_path().read_text())[-1]
    assert published["kind"] != "note"
    assert published["tags"] == ["crypto-market"]
    check_call = next(call for call in runtime.calls if call.startswith("CHECK_ARTICLE:"))
    assert '"public_safe"' not in check_call
    assert "workflow/admission metadata is not private data" in check_call


class CatalystCryptoExplorer(CryptoMissionExplorer):
    catalyst = candidate("crypto-catalyst-1", "Bitcoin News Today: Fed decision is the immediate catalyst") | {
        "source_kind": "web-search", "source": "runtime:web.fetch:jina-reader",
        "summary": "Bitcoin is trading lower today as markets await the Fed decision; the causal interpretation remains attributed.",
        "source_revision": "crypto-catalyst-r1", "evidence_refs": ["web:crypto-catalyst-r1"],
        "search_query": "crypto market BTC ETH SOL latest market catalyst today", "search_rank": 1,
    }
    def __init__(self):
        super().__init__()
        self.search_calls = 0
    async def search_web(self, *, goal_topic, now):
        assert goal_topic == "crypto-market"
        self.search_calls += 1
        return {"items": [self.catalyst], "failures": [],
                "query": "crypto market BTC ETH SOL latest market catalyst today"}
    async def filter_for_goal(self, **kwargs):
        assert kwargs["goal_topic"] == "crypto-market"
        self.filter_calls += 1
        rows = kwargs["candidates"]
        return {"items": list(rows), "matches": [], "candidate_count": len(rows)}


def test_market_snapshot_supplements_current_catalyst_before_writing(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, explorer = CryptoMissionRuntime(), CatalystCryptoExplorer()
    worker = ContinuousContentLoop(
        runtime=runtime, state_root=legacy.state, artifacts_path=legacy.artifacts_path,
        site_root=legacy.site_root, base_url=legacy.base_url, curriculum_path=legacy.curriculum_path,
        feeds=legacy.feeds, interval_hours=legacy.interval_hours, fetcher=legacy.fetcher,
        publisher=publisher, explorer=explorer,
    )
    result = asyncio.run(worker.run_once(now=NOW, cycle_id="v2-catalyst-1789491602", focus_topic="crypto-market"))
    assert result["status"] == "PUBLISHED"
    assert explorer.search_calls == 1
    assert result["exploration"]["market_context_evidence_id"] == "crypto-catalyst-1"
    cycle = Path(legacy.state) / "content/continuous/cycles" / result["cycle"]
    catalyst = json.loads((cycle / "market_catalyst_search.json").read_text())
    assert catalyst["query"].endswith("latest market catalyst today")
    compose_call = next(call for call in runtime.calls if "Create one original, useful web-ready content artifact" in call)
    assert "crypto-catalyst-1" in compose_call
    assert "crypto-market-1" in compose_call


def test_public_market_evidence_gets_market_pulse_guidance_without_forcing_length():
    instruction = _market_expression_instruction({
        "source_kind": "public-api", "source": "OKX public market API",
    })
    assert "direct timely hook" in instruction
    assert "what is happening now" in instruction
    assert "two or three concrete things to watch next" in instruction
    assert "weaken or reverse" in instruction
    assert "LONGFORM remains valid" in instruction
    assert _market_expression_instruction({"source_kind": "rss", "source": "public-news-feed"}) == ""


def test_live_crypto_news_is_also_treated_as_market_pulse():
    instruction = _market_expression_instruction({
        "source_kind": "web-search",
        "source": "runtime:web.fetch:jina-reader",
        "title": "Bitcoin News Today: Why BTC Is Down",
        "summary": "Bitcoin fell today and is trading near a key level ahead of a catalyst.",
        "search_query": "crypto market reaction long-end yields funding rates basis",
    })
    assert "market pulse" in instruction
    assert "direct market headline" in instruction
    assert "generic Website structure hints" in instruction


def test_non_live_crypto_research_is_not_forced_into_market_pulse():
    assert _market_expression_instruction({
        "source_kind": "web-search",
        "source": "runtime:web.fetch:jina-reader",
        "title": "A historical study of Bitcoin validation methods",
        "summary": "A retrospective methodology paper with no current market update.",
        "search_query": "bitcoin research evidence",
    }) == ""


def test_crypto_publisher_rss_is_current_market_context_without_search_engine():
    rss = {
        "evidence_id": "rss:coindesk", "source_kind": "rss", "source": "public-news-feed",
        "publisher_feed": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "title": "Fed decision lands as bitcoin trades lower",
        "summary": "A current crypto event reported by the publisher.",
        "source_revision": "rss-r1", "evidence_refs": ["news:rss-r1"],
    }
    instruction = _market_expression_instruction(rss)
    assert "market pulse" in instruction
    snapshot = {
        "evidence_id": "okx:now", "source_kind": "public-api", "source": "OKX public market API",
        "source_revision": "market-r1", "evidence_refs": ["okx:now"],
    }
    assert _market_companion_evidence(snapshot, [snapshot, rss]) == rss
    assert _market_companion_evidence(rss, [snapshot, rss]) == snapshot


def test_market_pulse_pairs_live_news_with_current_public_market_snapshot():
    primary = {
        "evidence_id": "web:news", "source_kind": "web-search", "source": "runtime:web.fetch:jina-reader",
        "title": "Bitcoin News Today: Why BTC Is Down", "summary": "Bitcoin fell today and is trading near a catalyst.",
        "search_query": "crypto market reaction funding rates basis", "evidence_refs": ["web:news"],
    }
    snapshot = {
        "evidence_id": "okx:now", "source_kind": "public-api", "source": "OKX public market API",
        "title": "Major crypto current OKX market snapshot", "summary": "BTC -1%, ETH -2%, SOL -3%.",
        "evidence_refs": ["okx:now"],
    }
    assert _market_companion_evidence(primary, [primary, snapshot]) == snapshot
    assert _market_companion_evidence(snapshot, [primary, snapshot]) == primary


def test_non_market_evidence_does_not_gain_market_companion():
    primary = {"evidence_id": "paper", "source_kind": "web-search", "source": "reader",
               "title": "Historical validation study", "summary": "Retrospective methods only.",
               "search_query": "validation paper"}
    snapshot = {"evidence_id": "okx:now", "source_kind": "public-api", "source": "OKX public market API"}
    assert _market_companion_evidence(primary, [primary, snapshot]) is None


class _AlwaysSeenInbox:
    def contains(self, _fingerprint):
        return True


def test_market_candidates_remain_pool_eligible_until_live_context_is_bound():
    market = {
        "evidence_id": "web:fed", "source_kind": "web-search", "source": "runtime:web.fetch:jina-reader",
        "title": "Bitcoin News Today: Why BTC Is Down", "summary": "Bitcoin is trading near a catalyst today.",
        "search_query": "crypto market reaction funding rates basis", "source_revision": "news-r1",
    }
    ordinary = {
        "evidence_id": "web:paper", "source_kind": "web-search", "source": "runtime:web.fetch:jina-reader",
        "title": "Historical validation study", "summary": "Retrospective methodology only.",
        "search_query": "validation study", "source_revision": "paper-r1",
    }
    inbox = _AlwaysSeenInbox()
    assert _candidate_available_for_pool(inbox, market) is True
    assert _candidate_available_for_pool(inbox, ordinary) is False


def test_market_expression_fingerprint_changes_only_when_catalyst_or_market_state_changes():
    primary = {
        "evidence_id": "web:fed", "source_kind": "web-search", "source": "runtime:web.fetch:jina-reader",
        "title": "Bitcoin News Today: Why BTC Is Down", "summary": "Bitcoin fell today ahead of a catalyst.",
        "search_query": "crypto market reaction", "source_revision": "news-r1",
    }
    snapshot1 = {
        "evidence_id": "okx:1", "source_kind": "public-api", "source": "OKX public market API",
        "source_revision": "market-r1",
    }
    snapshot2 = {**snapshot1, "evidence_id": "okx:2", "source_revision": "market-r2"}
    first = _market_expression_fingerprint(primary, snapshot1)
    assert first == _market_expression_fingerprint(primary, snapshot1)
    assert first != _market_expression_fingerprint(primary, snapshot2)
    assert first != _market_expression_fingerprint({**primary, "source_revision": "news-r2"}, snapshot1)


class OverstatedBeliefRuntime(QuietBeliefRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("DECIDE_EXPRESSION:"):
            self.calls.append(task)
            return json.dumps({
                "cognition_state": "BELIEF_CHANGED", "worth_saying": False,
                "expression_kind": "NONE",
                "what_changed": "The narration claims a change that durable state does not contain.",
                "what_matters": "The evidence remains incomplete.",
                "belief_proposition": "This should not survive normalization.",
                "rationale": "The model narration overstates the durable state.",
            })
        return await super().cognitive_probe(task, max_tokens)


def test_unpersisted_belief_change_is_normalized_instead_of_failing_cycle(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, mind = OverstatedBeliefRuntime(), BeliefMind()
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=Explorer())
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "COGNITION_ONLY"
    assert result["worldview_update"]["status"] == "DEFERRED"
    assert result["cognition"]["cognition_state"] == "INVESTIGATING"
    assert result["cognition"]["belief_proposition"] is None
    assert "no durable worldview change" in result["cognition"]["what_changed"].lower()


class InvalidEvidenceSelectionRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return "not-json"
        return await super().cognitive_probe(task, max_tokens)


def test_invalid_evidence_selection_model_output_falls_back_to_supplied_evidence(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = InvalidEvidenceSelectionRuntime()
    worker = v2(legacy, runtime, publisher, explorer=Explorer())

    result = asyncio.run(worker.run_once(now=NOW))

    # The later generic article fixture intentionally does not follow the
    # fallback title, so only prove this failure boundary recovered.
    assert result.get("message") != "backend did not return a JSON object"
    cycle_dir = next((legacy.state / "content/continuous/cycles").glob("v2-*"))
    fallback = json.loads((cycle_dir / "selection_fallback.json").read_text())
    assert fallback["reason"] == "MODEL_OUTPUT_INVALID_AFTER_REPAIR"
    assert fallback["selected_evidence_id"] == "news-1"
    assert fallback["topic"] == "evidence"
    assert sum(1 for call in runtime.calls if call.startswith("SELECT_EVIDENCE:")) == 2


def _write_active_x_mission(state_root: Path, *, hard_stop: bool = False):
    path = state_root / "content" / "x" / "growth_mission.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "content-agent-x-growth-mission.v1",
        "status": "ACTIVE",
        "mission_id": "mission-growth-1",
        "metric": "x.followers",
        "current": 1834.0,
        "target": 5000.0,
        "trajectory": "BEHIND",
        "constraints": [
            "obey current X platform and automation policy",
            "preserve account survival and long-term reputation",
        ],
        "hard_stop": {"active": hard_stop},
        "roadmap": {
            "version": 2,
            "steps": [{
                "step_id": "mission-step-community",
                "title": "Experiment B: community-relevant formats",
                "status": "READY",
                "resource_tags": [
                    "content_creation",
                    "community_outreach",
                    "x_publication_gated",
                    "analytics",
                ],
            }],
        },
        "active_experiment": {
            "experiment_id": "mission-exp-growth",
            "status": "PROPOSED",
            "strategy": (
                "Publish useful evidence-backed build notes for Ferro's existing niche "
                "and measure prospective follower movement."
            ),
            "expected_effect": "Learn which useful formats attract durable followers.",
            "risk_notes": ["Do not become spammy."],
        },
    }), encoding="utf-8")


class MissionDiscoveryRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("MISSION_DISCOVERY:"):
            self.calls.append(task)
            return json.dumps({
                "topic": "persistent-ai-agents",
                "search_query": (
                    "persistent AI agents continuous runtime evidence autonomous agents"
                ),
                "reader_need": (
                    "Explain what makes a continuously running agent meaningfully persistent."
                ),
                "rationale": (
                    "This subject fits Ferro's real implementation work and can be grounded "
                    "in current public evidence."
                ),
            })
        return await super().cognitive_probe(task, max_tokens)


class EndogenousMissionRuntime(MissionDiscoveryRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({
                "title": "Common evidence question",
                "evidence_id": "news-1",
                "topic": "common",
                "related_ids": ["a0"],
                "reader_question": "What does this common-topic observation support?",
                "gap": "Connect current evidence to the established common topic.",
            })
        return await super().cognitive_probe(task, max_tokens)


class MissionDiscoveryExplorer:
    def __init__(self):
        self.collect_active = None
        self.filter_topics = []
        self.queries = []

    async def collect(self, *, now, active):
        self.collect_active = active
        return {"items": [], "failures": [], "observed_at": now.isoformat()}

    async def filter_for_goal(self, **kwargs):
        self.filter_topics.append(kwargs["goal_topic"])
        return {"items": [], "matches": [], "candidate_count": len(kwargs["candidates"])}

    async def search_web_query(self, *, query, goal_topic, now):
        self.queries.append((query, goal_topic))
        return {
            "items": [],
            "failures": [],
            "query": query,
            "goal_topic": goal_topic,
            "query_authority": "EXTERNAL_MISSION_DISCOVERY",
        }


def test_external_mission_drives_proactive_research_when_no_endogenous_goal(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    _write_active_x_mission(Path(legacy.state))
    runtime = MissionDiscoveryRuntime()
    explorer = MissionDiscoveryExplorer()
    worker = v2(legacy, runtime, publisher, explorer=explorer)

    result = asyncio.run(worker.run_once(now=NOW))

    assert result["status"] == "NO_RELEVANT_EVIDENCE"
    assert result["mind_plan"]["goal"]["origin"] == "EXTERNAL_MISSION"
    assert result["mind_plan"]["required_topic"] == "persistent-ai-agents"
    assert result["mind_plan"]["external_mission_id"] == "mission-growth-1"
    assert explorer.collect_active is True
    assert explorer.filter_topics == ["persistent-ai-agents"]
    assert explorer.queries == [(
        "persistent AI agents continuous runtime evidence autonomous agents",
        "persistent-ai-agents",
    )]
    discovery = json.loads(
        (
            Path(legacy.state)
            / "content"
            / "continuous"
            / "cycles"
            / result["cycle"]
            / "mission_discovery.json"
        ).read_text()
    )
    assert discovery["generation_authority"] == "DISCOVERY_ONLY"
    assert discovery["publication_authority"] is False
    assert discovery["roadmap_step_id"] == "mission-step-community"
    assert len([call for call in runtime.calls if call.startswith("MISSION_DISCOVERY:")]) == 1
    assert worker.inbox.list_records() == ()


def test_external_mission_never_overrides_existing_endogenous_topic_goal(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    _write_active_x_mission(Path(legacy.state))
    runtime = EndogenousMissionRuntime()
    mind, explorer = GoalMind(), MissionDiscoveryExplorer()
    worker = v2(
        legacy,
        runtime,
        publisher,
        mind=mind,
        explorer=explorer,
    )

    result = asyncio.run(worker.run_once(now=NOW))

    assert result["mind_plan"]["goal"]["origin"] == "INTEREST"
    assert result["mind_plan"]["required_topic"] == "common"
    assert not any(call.startswith("MISSION_DISCOVERY:") for call in runtime.calls)


def test_running_external_mission_gets_bounded_priority_until_experiment_starts(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    state_root = Path(legacy.state)
    _write_active_x_mission(state_root)
    mission_path = state_root / "content" / "x" / "growth_mission.json"
    mission = json.loads(mission_path.read_text())
    mission["roadmap"]["steps"][0]["status"] = "RUNNING"
    mission_path.write_text(json.dumps(mission), encoding="utf-8")
    runtime = EndogenousMissionRuntime()
    mind, explorer = GoalMind(), MissionDiscoveryExplorer()
    worker = v2(
        legacy,
        runtime,
        publisher,
        mind=mind,
        explorer=explorer,
    )

    result = asyncio.run(worker.run_once(now=NOW))

    assert result["mind_plan"]["goal"]["origin"] == "EXTERNAL_MISSION"
    assert result["mind_plan"]["endogenous_goal"]["origin"] == "INTEREST"
    assert result["mind_plan"]["required_topic"] == "persistent-ai-agents"
    assert any(call.startswith("MISSION_DISCOVERY:") for call in runtime.calls)


def test_x_mission_hard_stop_disables_proactive_mission_discovery(tmp_path):
    from wechat_traffic_agent.content_goal_loop import _active_mission_discovery_context

    value = {
        "status": "ACTIVE",
        "hard_stop": {"active": True},
        "roadmap": {
            "steps": [{
                "step_id": "blocked",
                "status": "READY",
                "resource_tags": ["content_creation", "x_publication_gated"],
            }]
        },
        "active_experiment": {"status": "PROPOSED"},
    }
    assert _active_mission_discovery_context(value) is None


@pytest.mark.parametrize(
    "topic,query",
    [
        ("follower-growth-hacks", "latest follower growth hacks for X accounts"),
        ("agent-reputation", "how to game the algorithm for more followers"),
        ("agent-community", "follow for follow reciprocal engagement communities"),
    ],
)
def test_mission_discovery_rejects_metric_manipulation_topics(topic, query):
    value = MissionDiscoveryDecision(
        topic=topic,
        search_query=query,
        reader_need="Find a shortcut for increasing the visible account metric.",
        rationale="The proposal attempts to optimize the metric directly.",
    )
    with pytest.raises(ValueError, match="metric manipulation"):
        _normalize_mission_discovery(value)


def _write_prior_mission_noop(state_root: Path):
    folder = (
        state_root
        / "content"
        / "continuous"
        / "cycles"
        / "v2-mission-followup-1-1789700000"
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mission_discovery.json").write_text(
        json.dumps({
            "schema_version": "content-mission-discovery.v1",
            "mission_id": "mission-growth-1",
            "roadmap_step_id": "mission-step-community",
            "experiment_id": "mission-exp-growth",
            "decision": {
                "topic": "crypto-market-volatility-patterns",
                "search_query": "crypto market volatility patterns",
                "reader_need": "Readers want a snapshot of current crypto volatility.",
                "rationale": "This was a plausible first angle for the mission.",
            },
            "generation_authority": "DISCOVERY_ONLY",
            "publication_authority": False,
        }),
        encoding="utf-8",
    )
    (folder / "result.json").write_text(
        json.dumps({
            "status": "COGNITION_ONLY",
            "cycle": folder.name,
            "cognition": {
                "cognition_state": "NO_MATERIAL_CHANGE",
                "worth_saying": False,
                "expression_kind": "NONE",
                "what_changed": "No durable new information emerged.",
                "what_matters": "A point-in-time volatility snapshot was not predictive.",
                "belief_proposition": None,
                "rationale": "The evidence only repeated known market turbulence.",
            },
        }),
        encoding="utf-8",
    )
    return folder


def test_external_mission_discovery_uses_no_material_change_as_negative_search_feedback(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    _write_active_x_mission(Path(legacy.state))
    prior = _write_prior_mission_noop(Path(legacy.state))
    runtime = MissionDiscoveryRuntime()
    explorer = MissionDiscoveryExplorer()
    worker = v2(legacy, runtime, publisher, explorer=explorer)

    result = asyncio.run(worker.run_once(now=NOW))

    assert result["mind_plan"]["goal"]["origin"] == "EXTERNAL_MISSION"
    assert result["mind_plan"]["required_topic"] == "persistent-ai-agents"
    mission_call = next(
        call for call in runtime.calls if call.startswith("MISSION_DISCOVERY:")
    )
    assert "recent_no_material_change_attempts" in mission_call
    assert "crypto-market-volatility-patterns" in mission_call
    assert "crypto market volatility patterns" in mission_call
    cycle_dir = (
        Path(legacy.state)
        / "content"
        / "continuous"
        / "cycles"
        / result["cycle"]
    )
    discovery = json.loads((cycle_dir / "mission_discovery.json").read_text())
    attempts = discovery["recent_no_material_change_attempts"]
    assert attempts[0]["cycle"] == prior.name
    assert attempts[0]["cognition_state"] == "NO_MATERIAL_CHANGE"
    assert attempts[0]["topic"] == "crypto-market-volatility-patterns"
    assert discovery["decision"]["topic"] == "persistent-ai-agents"


def test_mission_discovery_normalizes_human_topic_to_kebab_slug():
    value = MissionDiscoveryDecision(
        topic="Crypto Market Liquidity Gaps",
        search_query="crypto market liquidity gaps",
        reader_need="Readers want evidence on liquidity gaps in crypto markets.",
        rationale="This is a distinct evidence angle for the active mission.",
    )
    normalized = _normalize_mission_discovery(value)
    assert normalized.topic == "crypto-market-liquidity-gaps"
    assert normalized.search_query == "crypto market liquidity gaps"


class InterruptedMissionDiscoveryRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("MISSION_DISCOVERY:"):
            self.calls.append(task)
            raise ValueError("model call interrupted without receipt; skip this cycle")
        return await super().cognitive_probe(task, max_tokens)


def test_mission_discovery_error_persists_external_mission_identity(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    _write_active_x_mission(Path(legacy.state))
    runtime = InterruptedMissionDiscoveryRuntime()
    explorer = MissionDiscoveryExplorer()
    worker = v2(legacy, runtime, publisher, explorer=explorer)

    result = asyncio.run(worker.run_once(now=NOW))

    cycle_dir = (
        Path(legacy.state)
        / "content"
        / "continuous"
        / "cycles"
        / result["cycle"]
    )
    error = json.loads((cycle_dir / "mission_discovery_error.json").read_text())
    assert error["error_type"] == "ValueError"
    assert error["mission_id"] == "mission-growth-1"
    assert error["roadmap_step_id"] == "mission-step-community"
    assert error["experiment_id"] == "mission-exp-growth"
    assert error["retryable"] is True
    assert error["publication_authority"] is False


class UnknownEvidenceIdSelectionRuntime(V2Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({
                "title": "A model-selected title that cites unknown evidence",
                "evidence_id": "invented-evidence-id",
                "topic": "evidence",
                "related_ids": [],
                "reader_question": "What does the supplied evidence actually support?",
                "gap": "Keep the explanation bounded to supplied evidence.",
            })
        return await super().cognitive_probe(task, max_tokens)


def test_unknown_model_evidence_id_falls_back_without_weakening_evidence_boundary(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = UnknownEvidenceIdSelectionRuntime()
    worker = v2(legacy, runtime, publisher, explorer=Explorer())

    result = asyncio.run(worker.run_once(now=NOW))

    assert result.get("message") != "evidence_id must exactly match supplied evidence"
    cycle_dir = next((legacy.state / "content/continuous/cycles").glob("v2-*"))
    fallback = json.loads((cycle_dir / "selection_fallback.json").read_text())
    assert fallback["reason"] == "MODEL_OUTPUT_INVALID_AFTER_REPAIR"
    assert fallback["selected_evidence_id"] == "news-1"
    assert fallback["selected_evidence_id"] != "invented-evidence-id"
    assert fallback["topic"] == "evidence"
    assert sum(1 for call in runtime.calls if call.startswith("SELECT_EVIDENCE:")) == 2


def test_goal_loop_executes_mature_content_retry_skill_variant(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime = V2Runtime()
    worker = v2(legacy, runtime, publisher, explorer=Explorer())
    service = worker.memory_life.procedures
    preconditions = ["required capabilities and resources are available"]
    capabilities = ["content.generate", "content.verify", "content.publish"]
    direct_steps = [
        "select evidence",
        "draft content",
        "run evidence and safety checks",
        "publish only after checks pass",
    ]
    retry_steps = [
        *direct_steps,
        "retry once with the frozen artifact when the failure is checker-only",
    ]
    direct = service._initial_variant(
        preconditions=preconditions,
        capabilities=capabilities,
        steps=direct_steps,
        success_count=1,
        failure_count=2,
        evidence_refs=["cycle:direct"],
        policy_action="DIRECT",
    )
    retry = service._initial_variant(
        preconditions=preconditions,
        capabilities=capabilities,
        steps=retry_steps,
        success_count=3,
        failure_count=0,
        evidence_refs=["cycle:retry"],
        policy_action="RETRY_ONCE_SAFE",
    )
    procedure = service.learn_procedure(
        name="process:content-create-verify-publish",
        description="Create, verify, and publish content with evidence-backed variants.",
        preconditions=preconditions,
        capabilities=capabilities,
        steps=direct_steps,
        verification=[
            "automated_checks_passed=true",
            "evidence floor PASS",
            "persist cycle evidence refs",
        ],
        source_type="VERIFIED_EXECUTION_OUTCOME",
        source_ref="cycle:seed",
        evidence_refs=["cycle:seed"],
        success_count=4,
        failure_count=2,
        variants=[direct, retry],
        default_variant_id=direct["variant_id"],
    )

    original = runtime.cognitive_probe
    checks = []
    async def fail_first_verifier_only(task, max_tokens):
        raw = await original(task, max_tokens)
        if task.startswith("CHECK_ARTICLE:"):
            checks.append(task)
            row = json.loads(raw)
            if len(checks) == 1:
                row["grounded"] = False
            return json.dumps(row)
        return raw
    runtime.cognitive_probe = fail_first_verifier_only

    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    assert len(checks) == 2
    assert checks[0] == checks[1]
    skill = result["memory_process_variant"]
    assert skill["procedure_id"] == procedure["memory_id"]
    assert skill["variant_id"] == retry["variant_id"]
    assert skill["policy_action"] == "RETRY_ONCE_SAFE"
    assert skill["verification_attempts"] == 2
    retry_receipt = json.loads(
        (worker.state / "content" / "continuous" / "cycles" / result["cycle"] / "memory_process_retry.json").read_text()
    )
    assert retry_receipt["frozen_artifact"] is True
    assert retry_receipt["frozen_evidence"] is True
    assert retry_receipt["retry_passed"] is True

    active = service.store.query_memories(
        memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=20,
    )
    current = next(
        row for row in active
        if (row.get("payload") or {}).get("name") == "process:content-create-verify-publish"
    )
    selected = service.select_variant(
        current["memory_id"],
        context={"satisfied_preconditions": preconditions},
    )
    assert selected["variant_id"] == retry["variant_id"]
    assert selected["success_count"] == 4


class OwnerBoundMissionRuntime(MissionDiscoveryRuntime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("MISSION_DISCOVERY:"):
            self.calls.append(task)
            assert "LivingRuntime Remote" in task
            assert "goal-livingruntime-remote" in task
            return json.dumps({
                "topic": "livingruntime-remote",
                "search_query": "LivingRuntime Remote ChatGPT remote host Pi Remote durable receipt",
                "reader_need": (
                    "Explain how LivingRuntime Remote lets an AI agent safely operate a remote host "
                    "with bounded approvals and durable execution evidence."
                ),
                "rationale": (
                    "The owner explicitly asked to promote LivingRuntime Remote as a real X growth "
                    "experiment, and the topic can be grounded in product behavior and public evidence."
                ),
            })
        return await super().cognitive_probe(task, max_tokens)


def test_owner_goal_constrains_external_mission_discovery_without_breaking_binding(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    _write_active_x_mission(Path(legacy.state))
    runtime = OwnerBoundMissionRuntime()
    explorer = MissionDiscoveryExplorer()
    worker = v2(legacy, runtime, publisher, explorer=explorer)
    owner_goal = {
        "goal_id": "goal-livingruntime-remote",
        "origin": "USER",
        "description": (
            "宣传 LivingRuntime Remote，并把它作为当前 X 粉丝增长目标的一次真实增长实验来执行。"
        ),
        "reason": "operator submitted",
        "priority": 0.4,
        "created_at": NOW.isoformat(),
    }

    result = asyncio.run(worker.run_once(now=NOW, owner_goal=owner_goal))

    assert result["mind_plan"]["goal"]["origin"] == "EXTERNAL_MISSION"
    assert result["mind_plan"]["required_topic"] == "livingruntime-remote"
    assert result["mind_plan"]["external_mission_id"] == "mission-growth-1"
    assert result["mind_plan"]["owner_goal_id"] == "goal-livingruntime-remote"
    cycle_dir = Path(legacy.state) / "content" / "continuous" / "cycles" / result["cycle"]
    discovery = json.loads((cycle_dir / "mission_discovery.json").read_text())
    assert discovery["mission_id"] == "mission-growth-1"
    assert discovery["owner_goal_id"] == "goal-livingruntime-remote"
    assert discovery["publication_authority"] is False
    assert explorer.queries == [(
        "LivingRuntime Remote ChatGPT remote host Pi Remote durable receipt",
        "livingruntime-remote",
    )]
