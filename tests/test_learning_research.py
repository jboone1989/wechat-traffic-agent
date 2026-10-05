import asyncio
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("maintenance_cognition")

from wechat_traffic_agent.learning_research import (
    ResearchFocus, investigate_learning, choose_research_focus,
    read_research_state, research_memory,
)

NOW = datetime(2026, 10, 3, 5, tzinfo=timezone.utc)
KNOWN = 'AI agent reliability requires execution evidence and measured outcomes.'
FOCUS = ResearchFocus(question='How does an agent restore tool state after a worker restart?',
    search_query='agent worker restart checkpoint implementation',
    why_interested='My own restarts make the boundary between chat history and tool state interesting.')
INTRO = {'evidence_id':'intro', 'title':'Crypto agents overview',
         'summary':'Agents should demonstrate execution evidence and measured outcomes.',
         'evidence_refs':['ref-intro'], 'source_revision':'intro-v1',
         'search_query':'crypto agents overview', 'url':'https://example.com/intro'}
DETAIL = {'evidence_id':'implementation', 'title':'Checkpoint implementation',
          'summary':'The worker stores a tool invocation ID before execution and reconciles it after restart.',
          'evidence_refs':['ref-detail'], 'source_revision':'detail-v1',
          'search_query':'agent checkpoint reconcile invocation restart', 'url':'https://example.com/code'}


def step(progress, source, *, related=KNOWN, query='agent checkpoint reconcile invocation restart'):
    return {'progress':progress, 'evidence_id':source['evidence_id'],
        'question_status':'ANSWERED' if progress in {'LEARNED','DEEPENED'} else 'BLOCKED',
        'answer':'The persisted invocation ID lets a restarted worker reconcile an earlier operation before running it again.',
        'inquiry_action':'NEXT_QUESTION' if progress in {'LEARNED','DEEPENED'} else 'CHANGE_METHOD',
        'resume_when':'',
        'reasoning_steps':([{'kind':'INFERENCE', 'fact_indices':[0],
            'explanation':'Because invocation identity survives a restart, a worker can match the earlier operation before executing again; identity alone does not prove its outcome.'}]
            if progress in {'LEARNED','DEEPENED'} else []),
        'learning_delta':('An invocation ID is persisted before execution to reconcile work after restart.'
                          if progress in {'LEARNED','DEEPENED'} else ''),
        'facts':([{'evidence_id':source['evidence_id'], 'quote':source['summary'],
                   'observation':'Invocation identity is retained across the restart boundary.'}]
                 if progress in {'LEARNED','DEEPENED'} else []),
        'related_proposition':related,
        'next_question':'What happens if the worker dies after the external side effect?',
        'next_query':query, 'rationale':'The overview repeats a known idea; implementation detail advances the inquiry.'}


def wire_step_references(data, decision):
    evidence_id = data["primary_source"]["evidence_id"]
    for fact in decision["facts"]:
        quote = fact.get("quote")
        if quote:
            match = next((value for value in data["source_quotes"].values()
                          if quote in value["quote"]), None)
            if match is not None:
                evidence_id = match["evidence_id"]
                break
    related = decision["related_proposition"]
    if related is not None:
        related = next((row["belief_id"] for row in data["memory"]["beliefs"]
                        if row["proposition"] == related), related)
    return {**decision, "evidence_id": evidence_id, "related_proposition": related}


class Runtime:
    def __init__(self, decisions):
        self.decisions=list(decisions)
        self.calls=[]
    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        if task.startswith('PLAN_LEARNING:'):
            return FOCUS.model_dump_json()
        decision = self.decisions.pop(0)
        if task.startswith('ADVANCE_LEARNING:'):
            data = json.JSONDecoder().raw_decode(task.split('\n', 1)[1])[0]
            decision = {**wire_step_references(data, decision), 'facts': [
                {'source_quote_id': next((key for key, value in data['source_quotes'].items()
                    if fact['quote'] in value['quote']),
                    'unknown-source-excerpt'), 'observation': fact['observation']}
                for fact in decision['facts']]}
        return json.dumps(decision)


class Explorer:
    def __init__(self):
        self.queries=[]
    async def search_web_query(self, *, query, goal_topic, now):
        self.queries.append(query)
        return {'items':[DETAIL], 'failures':[], 'query':query}


def run(tmp_path, runtime, explorer, *, cycle='cycle-1', rounds=2, now=NOW):
    return asyncio.run(investigate_learning(runtime=runtime, explorer=explorer,
        folder=tmp_path/'cycles'/cycle, state_root=tmp_path, topic='agent-reliability',
        focus=FOCUS, memory={'beliefs':[{'proposition':KNOWN}], 'recent_shares':[],
                             'research':read_research_state(tmp_path)},
        evidence=[INTRO], selected=INTRO, now=now, search_rounds=rounds))


def test_repeated_commonplace_causes_real_search_then_concrete_learning(tmp_path):
    runtime=Runtime([step('REPEATED', INTRO), step('DEEPENED', DETAIL,
                   query='agent external side effect idempotency restart')])
    explorer=Explorer()
    result=run(tmp_path, runtime, explorer)
    assert explorer.queries == ['agent checkpoint reconcile invocation restart']
    assert result['progressed'] and result['evidence_id']=='implementation'
    assert result['related_proposition']==KNOWN
    assert result['facts'][0]['quote']==DETAIL['summary']
    assert result['sources']==[DETAIL]
    assert read_research_state(tmp_path)['inquiries']['agent-reliability']['question']==result['next_question']

def test_react_retrieval_searches_external_inside_one_reasoning_loop(tmp_path):
    class ReactRuntime(Runtime):
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            self.calls.append(task)
            if not task.startswith('ADVANCE_LEARNING:'):
                return await super().cognitive_probe(task, max_tokens, output_schema)
            data = json.JSONDecoder().raw_decode(task.split('\n', 1)[1])[0]
            if not any(row.get('title') == DETAIL['title'] for row in data['sources']):
                return json.dumps({
                    'action': 'SEARCH_EXTERNAL',
                    'query': 'agent checkpoint reconcile invocation restart',
                    'ref': '',
                    'offset': 0,
                    'working_summary': 'The existing memory states a reliability principle but does not show the restart mechanism.',
                    'missing_information': 'I need a concrete implementation detail showing how work is reconciled after restart.',
                })
            decision = step('DEEPENED', DETAIL,
                            query='agent external side effect idempotency restart')
            decision = wire_step_references(data, decision)
            decision['facts'] = [{
                'source_quote_id': next(
                    key for key, value in data['source_quotes'].items()
                    if DETAIL['summary'] in value['quote']
                ),
                'observation': decision['facts'][0]['observation'],
            }]
            return json.dumps(decision)

    explorer = Explorer()
    result = run(tmp_path, ReactRuntime([]), explorer, rounds=0)
    assert explorer.queries == ['agent checkpoint reconcile invocation restart']
    assert result['progressed']
    assert result['evidence_id'] == DETAIL['evidence_id']
    assert result['search_rounds'] == 1
    assert result['react_search_queries'] == ['agent checkpoint reconcile invocation restart']
    assert (tmp_path/'cycles'/'cycle-1'/'learning_react_search_0.json').exists()
    assert not (tmp_path/'cycles'/'cycle-1'/'learning_search_0.json').exists()


def test_unresolved_inquiry_keeps_next_unknown_across_cycles_and_restart(tmp_path):
    first=run(tmp_path, Runtime([step('REPEATED',INTRO)]), Explorer(), rounds=0)
    assert not first['progressed']
    runtime=Runtime([])
    focus=asyncio.run(choose_research_focus(runtime=runtime,folder=tmp_path/'cycles'/'next',
        topic='agent-reliability',memory={'research':read_research_state(tmp_path)}))
    prompt=json.JSONDecoder().raw_decode(runtime.calls[0].split('\n',1)[1])[0]
    assert prompt['research']['inquiries']['agent-reliability']['search_query']==first['next_query']
    assert focus.question == FOCUS.question
    second=run(tmp_path,Runtime([step('LEARNED',INTRO)]),Explorer(),cycle='cycle-2',rounds=0)
    assert len(read_research_state(tmp_path)['history'])==2
    assert second['progressed']
    # A completed cycle replays exactly, including after a later scheduler time.
    replay=run(tmp_path,Runtime([]),Explorer(),cycle='cycle-2',rounds=0,now=NOW+timedelta(hours=1))
    assert replay==second
    assert len(read_research_state(tmp_path)['history'])==2


def test_unknown_quote_cannot_count_as_learning(tmp_path):
    invalid=step('LEARNED',INTRO)
    invalid['facts'][0]['quote']='The agent achieves a ninety percent improvement.'
    with pytest.raises(ValueError,match='source_quote_id'):
        run(tmp_path,Runtime([invalid,invalid]),Explorer(),rounds=0)
    assert not (tmp_path/'content'/'learning'/'research.json').exists()


def test_semantic_binding_must_reference_an_existing_canonical_claim(tmp_path):
    invalid=step('DEEPENED',INTRO,related='A newly invented equivalent wording is not canonical.')
    with pytest.raises(ValueError,match='copy an existing belief|related_proposition'):
        run(tmp_path,Runtime([invalid,invalid]),Explorer(),rounds=0)


def test_source_errors_are_retained_and_do_not_manufacture_progress(tmp_path):
    class Unavailable(Explorer):
        async def search_web_query(self,**kwargs):
            return {'items':[], 'failures':[{'source':'web','error_type':'NetworkError'}]}
    result=run(tmp_path,Runtime([step('UNRESOLVED',INTRO),
        step('UNRESOLVED',INTRO,query='agent source repository state durability')]),Unavailable(),rounds=1)
    assert not result['progressed']
    assert result['source_failures']==[{'source':'web','error_type':'NetworkError'}]


def test_research_sees_related_low_confidence_claim_outside_public_top_eight(tmp_path):
    worldview=SimpleNamespace(snapshot=lambda:{'claims':{
        **{f'Unrelated high confidence claim {i}':{'confidence':.9} for i in range(60)},
        KNOWN:{'confidence':.05}}})
    mind=SimpleNamespace(public_projection=lambda:{'beliefs':[], 'interests':[{'name':'agent-reliability'}]},
                         session=SimpleNamespace(worldview=worldview))
    memory=research_memory(mind,tmp_path,'agent-reliability')
    assert KNOWN in {row['proposition'] for row in memory['beliefs']}


def test_crash_after_cycle_receipt_repairs_longitudinal_state_on_replay(tmp_path):
    result=run(tmp_path,Runtime([step('LEARNED',INTRO)]),Explorer(),rounds=0)
    (tmp_path/'content'/'learning'/'research.json').unlink()
    replay=run(tmp_path,Runtime([]),Explorer(),rounds=0)
    assert replay==result
    assert read_research_state(tmp_path)['history'][0]['cycle']=='cycle-1'

def test_research_reference_schema_preserves_long_canonical_belief():
    from wechat_traffic_agent.learning_research import _research_step_contract
    belief = "Durable execution evidence must retain the complete canonical proposition. " * 5
    contract = _research_step_contract({belief})
    row = step("LEARNED", DETAIL, related=belief)
    assert contract.model_validate(row).related_proposition == belief
    with pytest.raises(ValueError):
        contract.model_validate({**row, "related_proposition": belief[:150]})
    assert contract.model_validate({**row, "related_proposition": None}).related_proposition is None


def test_citation_id_retrieves_exact_original_text_without_model_copying():
    from wechat_traffic_agent.learning_research import (
        _research_quotes, _research_choice_contract, _bind_research_choice)
    source = {**DETAIL, 'summary': 'Invocation IDs—persisted before execution—survive restart; outcomes remain separately verified.'}
    quotes = _research_quotes({source['evidence_id']: source})
    choice = {**step('LEARNED', source), 'evidence_id': 'source-0', 'related_proposition': 'belief-0',
              'facts': [{'source_quote_id': next(iter(quotes)),
                         'observation': 'Invocation identity survives the restart boundary.'}]}
    registry = {source['evidence_id']: source}
    contract = _research_choice_contract({KNOWN}, quotes, registry)
    bound = _bind_research_choice(contract.model_validate(choice), quotes, registry, {KNOWN})
    assert bound.facts[0].quote == source['summary']
    assert bound.facts[0].evidence_id == source['evidence_id']
    with pytest.raises(ValueError, match='source_quote_id'):
        contract.model_validate({**choice, 'facts': [
            {'source_quote_id': 'invented-source', 'observation': 'An unverified model assertion.'}]})


def test_empty_belief_registry_requires_null_reference():
    from wechat_traffic_agent.learning_research import _research_step_contract
    contract = _research_step_contract(set())
    assert contract.model_validate(step("LEARNED", DETAIL, related=None)).related_proposition is None
    with pytest.raises(ValueError):
        contract.model_validate(step("LEARNED", DETAIL, related="invented belief"))


def test_research_reads_method_passages_beyond_search_summary(tmp_path):
    body = "Background context without the mechanism. " * 70
    mechanism = "The worker stores a tool invocation ID before execution and reconciles it after restart."
    body += mechanism
    source = {**DETAIL, "summary": body[:2000], "evidence_text": body}
    decision = step("LEARNED", source)
    decision["facts"][0]["quote"] = mechanism
    class FullBodyRuntime(Runtime):
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            self.calls.append(task)
            data = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])[0]
            key = next(key for key, value in data["source_quotes"].items()
                       if mechanism in value["quote"])
            return json.dumps({**wire_step_references(data, decision), "facts": [{
                "source_quote_id": key, "observation": "Invocation identity survives a restart."}]})
    result = asyncio.run(investigate_learning(
        runtime=FullBodyRuntime([]), explorer=Explorer(), folder=tmp_path/"cycles"/"full",
        state_root=tmp_path, topic="agent-reliability", focus=FOCUS,
        memory={"beliefs":[{"proposition":KNOWN}], "research":{"history":[]}},
        evidence=[source], selected=source, now=NOW, search_rounds=0))
    assert mechanism in result["facts"][0]["quote"]
    assert result["facts"][0]["quote"] not in source["summary"]
    assert read_research_state(tmp_path)["history"][0]["facts"] == result["facts"]


def test_next_cycle_receives_original_learned_facts_even_without_belief_change(tmp_path):
    result = run(tmp_path, Runtime([step("LEARNED", INTRO)]), Explorer(), rounds=0)
    memory = research_memory(None, tmp_path)
    assert memory["research"]["history"][0]["facts"] == result["facts"]
    assert read_research_state(tmp_path)["inquiries"]["agent-reliability"]["facts"] == result["facts"]


def test_existing_receipts_supply_facts_missing_from_old_history(tmp_path):
    result = run(tmp_path, Runtime([step("LEARNED", INTRO)]), Explorer(), rounds=0)
    state_path = tmp_path/"content"/"learning"/"research.json"
    state = json.loads(state_path.read_text())
    del state["history"][0]["facts"]
    state_path.write_text(json.dumps(state))
    receipt = tmp_path/"content"/"continuous"/"cycles"/result["cycle"]/"learning_result.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(result))
    assert research_memory(None, tmp_path)["research"]["history"][0]["facts"] == result["facts"]

def test_research_publication_memory_uses_public_words_not_private_cognition(tmp_path):
    from wechat_traffic_agent.x_social import XHistoryLedger
    ledger = XHistoryLedger(tmp_path)
    ledger.append({"event_type": "EXPRESSION", "cycle": "published",
                   "text": "A concrete public learning detail.",
                   "cognition": {"private": "execution trace" * 10000},
                   "x_url": "https://x.com/agent/status/1", "recorded_at": NOW.isoformat()})
    memory = research_memory(None, tmp_path)
    assert memory["recent_shares"][0]["text"] == "A concrete public learning detail."
    assert "cognition" not in memory["recent_shares"][0]


def test_research_source_quote_registry_is_authoritative_without_duplicate_body(tmp_path):
    selected = {**INTRO, "evidence_text": INTRO["summary"] + " Other context." * 2000}
    class QuoteRuntime(Runtime):
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            self.calls.append(task)
            data = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])[0]
            value = step("LEARNED", INTRO)
            value["facts"] = [{"source_quote_id": next(iter(data["source_quotes"])),
                               "observation": "The supplied source contains a concrete observation."}]
            return json.dumps(wire_step_references(data, value))
    runtime = QuoteRuntime([])
    result = asyncio.run(investigate_learning(runtime=runtime, explorer=Explorer(),
        folder=tmp_path/"cycles"/"source-once", state_root=tmp_path, topic="agent-reliability",
        focus=FOCUS, memory={"beliefs": [{"proposition": KNOWN}], "recent_shares": [],
            "research": read_research_state(tmp_path)}, evidence=[selected], selected=selected,
        now=NOW, search_rounds=0))
    prompt = json.JSONDecoder().raw_decode(runtime.calls[0].split("\n", 1)[1])[0]
    assert prompt["primary_source"] == {"evidence_id": "source-0"}
    assert result["sources"] == [selected]
    assert result["facts"][0]["quote"] in selected["evidence_text"]


def test_research_source_and_belief_references_bind_exact_canonical_values(tmp_path):
    source = {**DETAIL, "evidence_id": "web:" + "abcdef01" * 8}
    canonical = "A complete canonical belief that must never be recopied by the model. " * 5
    class WireRuntime:
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            data = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])[0]
            quote_id = next(iter(data["source_quotes"]))
            assert data["sources"][0]["evidence_id"] == "source-0"
            assert data["memory"]["beliefs"][0]["belief_id"] == "belief-0"
            return json.dumps({**step("LEARNED", source), "evidence_id": "source-0",
                "related_proposition": "belief-0", "facts": [{
                    "source_quote_id": quote_id,
                    "observation": "The worker persists invocation identity before execution."}]})
    result = asyncio.run(investigate_learning(runtime=WireRuntime(), explorer=Explorer(),
        folder=tmp_path/"cycles"/"short-refs", state_root=tmp_path, topic="agent-reliability",
        focus=FOCUS, memory={"beliefs": [{"proposition": canonical}], "research": {"history": []}},
        evidence=[source], selected=source, now=NOW, search_rounds=0))
    assert result["evidence_id"] == source["evidence_id"]
    assert result["related_proposition"] == canonical
    assert result["facts"][0]["evidence_id"] == source["evidence_id"]
    assert result["facts"][0]["quote"] == source["summary"]


@pytest.mark.parametrize("field,value", [("evidence_id", "source-999"), ("related_proposition", "belief-999")])
def test_unknown_research_reference_is_rejected_before_persistence(tmp_path, field, value):
    class InvalidRuntime:
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            data = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])[0]
            return json.dumps({**step("LEARNED", INTRO), "evidence_id": "source-0",
                "related_proposition": "belief-0", field: value,
                "facts": [{"source_quote_id": next(iter(data["source_quotes"])),
                           "observation": "An observation cannot admit an unknown source or belief."}]})
    with pytest.raises(ValueError, match=field):
        run(tmp_path, InvalidRuntime(), Explorer(), rounds=0)
    assert not (tmp_path/"content"/"learning"/"research.json").exists()


def test_research_projection_retains_pending_inquiry_and_observations_without_copying_archives():
    from copy import deepcopy
    from wechat_traffic_agent.learning_research import _research_prompt_memory
    fact = {"evidence_id": "web:original", "quote": "long original excerpt " * 30,
            "observation": "Persisted invocation identity survives the worker restart."}
    memory = {"beliefs": [{"proposition": KNOWN}],
              "recent_shares": [{"text": str(i)} for i in range(20)],
              "research": {"inquiries": {
                  "agent-reliability": {"question": FOCUS.question, "facts": [fact]},
                  "other-topic": {"question": "Unrelated old investigation"}},
                  "history": [{"cycle": str(i), "topic": "agent-reliability",
                               "facts": [fact], "learning_delta": "Invocation identity is persisted."}
                              for i in range(12)]}}
    original = deepcopy(memory)
    projected = _research_prompt_memory(memory, "agent-reliability")
    assert projected["research"]["inquiries"]["agent-reliability"]["question"] == FOCUS.question
    assert projected["research"]["history"][-1]["facts"] == [{"observation": fact["observation"]}]
    assert "other-topic" not in projected["research"]["inquiries"]
    assert fact["quote"] not in json.dumps(projected)
    assert projected["beliefs"] == memory["beliefs"]
    assert len(projected["recent_shares"]) == 8
    assert projected["projection"]["history_total"] == 12
    assert memory == original


def test_question_relevant_exact_passages_survive_global_excerpt_budget():
    from wechat_traffic_agent.learning_research import _research_quotes
    registry = {}
    needle = "Persist invocation checkpoints before restart to reconcile tool side effects."
    for i in range(8):
        registry[f"web:{i}"] = {**DETAIL,
            "evidence_text": ("Unrelated background. " * 600) + needle + (" More context." * 100)}
    quotes = _research_quotes(registry, "invocation checkpoints restart reconcile tool side effects")
    assert len(quotes) <= 24
    assert {value["evidence_id"] for value in quotes.values()} == set(registry)
    for evidence_id, source in registry.items():
        assert any(needle in value["quote"] for value in quotes.values()
                   if value["evidence_id"] == evidence_id)
        assert all(value["quote"] in source["evidence_text"] for value in quotes.values()
                   if value["evidence_id"] == evidence_id)


def test_pending_external_search_reuses_receipt_and_sources_after_restart(tmp_path, monkeypatch):
    from wechat_traffic_agent.memory_service import MemoryService
    original_ingest = MemoryService.ingest_source
    interrupted = False
    def crash_ingest(self, source, **kwargs):
        nonlocal interrupted
        if source["evidence_id"] == DETAIL["evidence_id"] and not interrupted:
            interrupted = True
            raise RuntimeError("crash after acquisition receipt")
        return original_ingest(self, source, **kwargs)
    monkeypatch.setattr(MemoryService, "ingest_source", crash_ingest)
    class SearchRuntime(Runtime):
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            if task.startswith("ADVANCE_LEARNING:"):
                return json.dumps({
                    "action": "SEARCH_EXTERNAL", "query": "checkpoint implementation receipt",
                    "ref": "", "offset": 0, "working_summary": "Need concrete implementation",
                    "missing_information": "Invocation recovery mechanism"})
            return await super().cognitive_probe(task, max_tokens, output_schema)
    explorer = Explorer()
    with pytest.raises(RuntimeError, match="crash after acquisition receipt"):
        run(tmp_path, SearchRuntime([]), explorer, rounds=0)
    checkpoint = tmp_path / "cycles/cycle-1/memory_session_step_0.json"
    state = json.loads(checkpoint.read_text())
    action_id = state["pending_action"]["action_id"]
    assert state["external_searches"][0]["action_id"] == action_id
    refs = state["refs"]
    result = run(tmp_path, Runtime([step("DEEPENED", DETAIL,
        query="agent external side effect idempotency restart")]), explorer, rounds=0)
    resumed = json.loads(checkpoint.read_text())
    assert explorer.queries == ["checkpoint implementation receipt"]
    assert all(resumed["refs"][ref] == node for ref, node in refs.items())
    assert result["sources"] == [DETAIL]
    assert result["facts"][0]["quote"] == DETAIL["summary"]
    assert resumed["decision_rounds_used"] == 2


def test_new_detail_does_not_mark_unanswered_question_sufficient(tmp_path):
    value = step("DEEPENED", DETAIL, query="worker crash reconciliation result table")
    value.update(question_status="PARTIAL", inquiry_action="CONTINUE",
                 answer="Persisted identity survives restart, but this source does not show how the external outcome is reconciled.")
    result = asyncio.run(investigate_learning(runtime=Runtime([value]), explorer=Explorer(),
        folder=tmp_path/"cycles/cycle-1", state_root=tmp_path, topic="agent-reliability",
        focus=FOCUS, memory={"beliefs": [{"proposition": KNOWN}], "research": {"history": []}},
        evidence=[DETAIL], selected=DETAIL, now=NOW, search_rounds=0))
    assert result["progressed"] is True
    assert result["question_status"] == "PARTIAL"
    session = json.loads((tmp_path/"cycles/cycle-1/memory_session_step_0.json").read_text())
    assert session["termination"] == "EVIDENCE_INCOMPLETE"
    assert read_research_state(tmp_path)["inquiries"]["agent-reliability"]["question_status"] == "PARTIAL"


def test_prediction_not_required_to_explain_saved_mechanism(tmp_path):
    memory = {"knowledge_catalog": {"K1": {"observation": "Invocation identity survives restart."}},
              "research": {"history": []}}
    runtime = Runtime([])
    result = asyncio.run(choose_research_focus(runtime=runtime,
        folder=tmp_path/"explain", topic="agent-reliability", memory=memory))
    assert result.approach == "EXPLAIN" and result.application is None
    assert not (tmp_path/"explain/learning_application_plan.json").exists()


def test_unfinished_questions_are_visible_across_topic_aliases(tmp_path):
    from wechat_traffic_agent.learning_research import _working_projection
    state = {"inquiries": {}, "history": [
        {"cycle": str(i), "topic": "btc-regime-" + str(i), "focus": {"question": "Does HMM improve BTC forecasts?"},
         "question_status": "PARTIAL", "inquiry_action": "CHANGE_METHOD",
         "learning_delta": "A regime input was located.", "facts": []}
        for i in range(3)]}
    session = SimpleNamespace(state={"prompt_window": 8})
    projection = _working_projection({"research": state}, "new-btc-slug", session)
    assert len(projection["research_trajectory"]) == 3
    assert all(row["question_status"] == "PARTIAL" for row in projection["research_trajectory"])
    assert projection["research"]["history"] == []


def test_parked_inquiry_keeps_resume_condition_after_restart(tmp_path):
    value = step("UNRESOLVED", INTRO)
    value.update(inquiry_action="PARK", resume_when="A timestamped out-of-sample result table becomes available.",
                 answer="The source repeats the principle but supplies no execution result, so the application cannot yet be checked.")
    result = run(tmp_path, Runtime([value]), Explorer(), rounds=0)
    inquiry = read_research_state(tmp_path)["inquiries"]["agent-reliability"]
    assert inquiry["inquiry_action"] == "PARK" and inquiry["resume_when"] == result["resume_when"]
    replay = run(tmp_path, Runtime([]), Explorer(), rounds=0)
    assert replay == result


def test_exact_known_observation_cannot_count_again_as_progress(tmp_path):
    value = step("DEEPENED", DETAIL)
    memory = {"beliefs": [{"proposition": KNOWN}], "research": {"history": [
        {"cycle": "prior", "topic": "other-slug", "facts": value["facts"]}]}}
    with pytest.raises(ValueError, match="already learned observations"):
        asyncio.run(investigate_learning(runtime=Runtime([value, value]), explorer=Explorer(),
            folder=tmp_path/"cycles/repeat", state_root=tmp_path, topic="agent-reliability",
            focus=FOCUS, memory=memory, evidence=[DETAIL], selected=DETAIL, now=NOW, search_rounds=0))
