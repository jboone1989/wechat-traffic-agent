import asyncio
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

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
        'learning_delta':('An invocation ID is persisted before execution to reconcile work after restart.'
                          if progress in {'LEARNED','DEEPENED'} else ''),
        'facts':([{'evidence_id':source['evidence_id'], 'quote':source['summary'],
                   'observation':'Invocation identity is retained across the restart boundary.'}]
                 if progress in {'LEARNED','DEEPENED'} else []),
        'related_proposition':related,
        'next_question':'What happens if the worker dies after the external side effect?',
        'next_query':query, 'rationale':'The overview repeats a known idea; implementation detail advances the inquiry.'}


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
            decision = {**decision, 'facts': [
                {'source_quote_id': next((key for key, value in data['source_quotes'].items()
                    if value['quote'] == fact['quote'] and value['evidence_id'] == fact['evidence_id']),
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
    choice = {**step('LEARNED', source),
              'facts': [{'source_quote_id': next(iter(quotes)),
                         'observation': 'Invocation identity survives the restart boundary.'}]}
    contract = _research_choice_contract({KNOWN}, quotes)
    bound = _bind_research_choice(contract.model_validate(choice), quotes)
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
