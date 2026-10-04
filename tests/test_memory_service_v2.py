import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import BaseModel
import pytest

from wechat_traffic_agent.memory_service import MemoryService
from agent_runtime.memory.session import (
    MemorySession, MemoryAction, MemoryContinuationRequired, BudgetedRuntime,
)
from wechat_traffic_agent.runtime_memory_adapter import memory_decision
from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.memory_vector import SemanticVectorIndex
from conftest import RetrievalModelsDouble


class MaintenanceRuntime:
    def __init__(self): self.tasks=[]
    async def cognitive_probe(self,task,max_tokens,output_schema=None):
        self.tasks.append(task)
        data=json.JSONDecoder().raw_decode(task.split('\n',1)[1])[0]
        if task.startswith('CONTEXTUALIZE_MEMORY:'):
            return json.dumps({'prefixes':[{'ref':c['ref'],'prefix':'This passage explains the checkpoint worker restart mechanism.'} for c in data['chunks']]})
        if task.startswith('LINK_MEMORY:'):
            return json.dumps({'relations':[{'fact_ref':'F0','subject':'worker','predicate':'USES','object':'checkpoint'}]})
        assert task.startswith('CONSOLIDATE_MEMORY:')
        return json.dumps({'summary':'The children describe qualified checkpoint behavior and unresolved recovery cases.','context_prefix':'Checkpoint recovery research and its source qualifications.'})


def source(text='The worker uses a checkpoint to restore state after a restart.'):
    return {'evidence_id':'source-long-'+('abc'*30),'title':'Checkpoint code','summary':text,'evidence_text':text,'url':'https://example.com/code'}


def research(row_source,cycle='cycle'):
    return {'cycle':cycle,'topic':'restart','observed_at':datetime.now(timezone.utc).isoformat(),
            'facts':[{'evidence_id':row_source['evidence_id'],'quote':row_source['summary'],'observation':'The worker uses a checkpoint to restore its state.'}],
            'sources':[row_source]}


def test_original_revision_invalidates_facts_graph_and_hierarchy(tmp_path):
    service=MemoryService(tmp_path)
    original=source();root=service.ingest_source(original,topic='restart')
    row=research(original);service.ingest_research(row)
    runtime=MaintenanceRuntime()
    asyncio.run(service.enrich_relations(runtime,row))
    summary=asyncio.run(service.consolidate(runtime,'restart'))
    relation=service.store.query_memories(memory_types=['RELATIONAL'])[0]
    fact=service.store.query_memories(memory_types=['SEMANTIC'])[0]
    assert service.relations(fact['memory_id'])
    changed=source('The worker no longer uses a checkpoint and cannot restore state after restart.')
    service.ingest_source(changed,topic='restart')
    assert service.store.get_memory(fact['memory_id'])['status']=='DISPUTED'
    assert service.store.get_memory(relation['memory_id'])['status']=='DISPUTED'
    assert service.read(summary)['needs_refresh']
    assert service._node(root)['metadata']['source_version']!=service._node(fact['memory_id'])['metadata'].get('source_version')
    # Replaying an old receipt cannot roll back the active source pointer.
    service.ingest_source(original,topic='restart',activate=False)
    current=service._node(root)['metadata']
    assert json.loads(Path(current['archive']).read_text())['evidence_text']==changed['evidence_text']
    assert len(list((service.root/'evidence').glob('*.json')))==2

def test_relation_enrichment_skips_edges_not_present_in_fact(tmp_path):
    raw=source();row=research(raw)
    service=MemoryService(tmp_path);service.ingest_source(raw,topic='restart');service.ingest_research(row)
    class RelationRuntime:
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            return json.dumps({'relations':[
                {'fact_ref':'F0','subject':'worker','predicate':'USES','object':'checkpoint'},
                {'fact_ref':'F0','subject':'missing','predicate':'USES','object':'checkpoint'}]})
    asyncio.run(service.enrich_relations(RelationRuntime(),row))
    relations=service.store.query_memories(memory_types=['RELATIONAL'])
    assert [r['text'] for r in relations]==['worker USES checkpoint']


def test_canonical_transition_invalidates_ancestors_in_same_transaction(tmp_path):
    service=MemoryService(tmp_path)
    service.store.append_memory({'memory_id':'fact','text':'Checkpoint restores state.','kind':'FACT'})
    service.sync()
    service.put_node('lower','summary','A qualified checkpoint summary.',{},children=['fact'])
    service.put_node('upper','summary','A summary of the lower branch.',{},children=['lower'])
    before=max(c['change_seq'] for c in service.changes(0,limit=100))
    service.store.transition_memory('fact',status='RETRACTED')
    assert service._node('upper')['status']=='DIRTY'
    assert any(c['node_id']=='upper' and c['event_status']=='DIRTY' for c in service.changes(before,limit=100))
    assert not any(c['node_id']=='fact' for c in service.search('checkpoint'))


def test_rebuild_preserves_canonical_records_and_exact_evidence(tmp_path):
    service=MemoryService(tmp_path)
    raw=source();service.ingest_source(raw,topic='restart');service.ingest_research(research(raw))
    saved=service.search('checkpoint')
    service.rebuild_views()
    restored=MemoryService(tmp_path).search('checkpoint')
    assert {r['node_id'] for r in saved}=={r['node_id'] for r in restored}
    assert service.store.list_memories(limit=None)
    evidence=next(c for c in restored if c['type']=='evidence')
    assert service.read(evidence['node_id'])['text']==raw['evidence_text']


def test_context_prefix_does_not_change_original_or_reencode_unchanged_nodes(tmp_path):
    models=RetrievalModelsDouble();service=MemoryService(tmp_path,models=models)
    raw=source();root=service.ingest_source(raw,topic='restart')
    card=service.search('checkpoint',node_types=['evidence'])[0]
    before=service.read(card['node_id'])
    runtime=MaintenanceRuntime()
    asyncio.run(service.contextualize(runtime,[card['node_id']]))
    after=service.read(card['node_id'])
    assert after['text']==before['text'] and after['version']==before['version']
    assert after['context']!=before['context']
    service.search('checkpoint')
    encoded=len(models.encoded)
    service.ingest_source(raw,topic='restart')
    service.search('checkpoint')
    assert len(models.encoded)==encoded
    assert service._node(root)['status']=='ACTIVE'


def test_hybrid_search_does_not_require_cross_encoder_reranker(tmp_path):
    class NoRerank(RetrievalModelsDouble):
        def rerank(self, query, texts):
            raise AssertionError("cross-encoder must not run on the hot retrieval path")

    service = MemoryService(tmp_path, models=NoRerank())
    service.store.append_memory({
        'memory_id': 'checkpoint-fact',
        'text': 'A checkpoint restores worker state after restart.',
        'kind': 'FACT',
        'topics': ['recovery'],
    })
    service.sync()

    results = service.search('checkpoint restart', topic='recovery', limit=3)

    assert results
    assert results[0]['node_id'] == 'checkpoint-fact'


def test_summary_semantic_hierarchy_is_bounded_cached_and_descendable(tmp_path):
    service=MemoryService(tmp_path)
    for i in range(26):
        service.store.append_memory({'memory_id':'fact-'+str(i),'text':('Checkpoint worker restart ' if i%2 else 'Payment transaction recovery ')+str(i),'topics':['recovery'],'kind':'FACT'})
    runtime=MaintenanceRuntime()
    root=asyncio.run(service.consolidate(runtime,'recovery'))
    calls=len(runtime.tasks)
    assert calls>1
    assert 1<=len(service.expand(root))<=6
    assert asyncio.run(service.consolidate(runtime,'recovery'))==root
    assert len(runtime.tasks)==calls
    service.store.transition_memory('fact-0',status='RETRACTED')
    new_root=asyncio.run(service.consolidate(runtime,'recovery'))
    assert new_root!=root and len(runtime.tasks)>calls
    assert service._node(root)['status']=='SUPERSEDED'


def test_shared_vector_facade_only_encodes_changed_active_records(tmp_path):
    models=RetrievalModelsDouble()
    store=CanonicalMemoryStore(tmp_path)
    for i in range(20): store.append_memory({'memory_id':str(i),'text':'Fact '+str(i),'kind':'FACT'})
    index=SemanticVectorIndex(store,models=models)
    assert len(models.encoded)==20
    store.transition_memory('0',status='ARCHIVED');index.rebuild_if_stale()
    assert len(models.encoded)==20
    index.rebuild_if_stale();assert len(models.encoded)==20
    store.append_memory({'memory_id':'new','text':'New fact','kind':'FACT'})
    index.rebuild_if_stale();assert len(models.encoded)==21


def test_iterative_task_resumes_without_losing_tool_observation_or_reference(tmp_path):
    service=MemoryService(tmp_path)
    root=service.ingest_source(source(),topic='restart')
    class Decision(BaseModel): answer:str
    class Runtime:
        def __init__(self,tool):self.tool=tool;self.tasks=[]
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            if task.startswith('CONTEXTUALIZE_MEMORY:'): return await MaintenanceRuntime().cognitive_probe(task,max_tokens,output_schema)
            self.tasks.append(task)
            if self.tool:
                return json.dumps({'action':'EXPAND_SUMMARY','ref':'M1','offset':0,
                    'working_summary':'The source has a checkpoint mechanism.','missing_information':'I need its exact implementation excerpt.'})
            assert 'checkpoint mechanism' in task and 'tool_result' in task
            return json.dumps({'answer':'The worker uses a checkpoint to restore state.'})
    session=MemorySession(tmp_path/'cycle','test',service)
    session.state['cards']=session.cards([service._card(service._node(root))]);session.save()
    async def run(runtime,session):
        return await memory_decision(runtime=runtime,session=session,instruction='TEST: Decide from evidence.',payload_builder=lambda:{},contract_builder=lambda:Decision,max_turns=1)
    with pytest.raises(MemoryContinuationRequired):asyncio.run(run(Runtime(True),session))
    resumed=MemorySession(tmp_path/'cycle','test',service)
    runtime=Runtime(False);answer=asyncio.run(run(runtime,resumed))
    assert answer.answer and resumed.state['refs']['M1']==root
    assert resumed.state['status']=='DECIDED'
    assert asyncio.run(run(runtime,MemorySession(tmp_path/'cycle','test',service)))==answer
    assert len(runtime.tasks)==1


def test_final_request_budget_rejects_schema_and_repair_overflow_before_dispatch(tmp_path):
    class Runtime:
        async def cognitive_probe(self,*args,**kwargs):raise AssertionError('oversized prompt dispatched')
    runtime=BudgetedRuntime(Runtime(),tmp_path)
    with pytest.raises(ValueError,match='MEMORY_REQUEST_BUDGET_EXCEEDED'):
        asyncio.run(runtime.cognitive_probe('small',max_tokens=2400,output_schema={'description':'x'*40000}))
    assert json.loads((tmp_path/'memory_request_sizes.jsonl').read_text())['characters']>36000


def test_explicit_maintenance_fixture_keeps_real_budget_checks(tmp_path, maintenance_cognition):
    class Runtime:
        async def cognitive_probe(self,*args,**kwargs): raise AssertionError('oversized maintenance dispatched')
    runtime=BudgetedRuntime(Runtime(),tmp_path)
    with pytest.raises(ValueError,match='MEMORY_REQUEST_BUDGET_EXCEEDED'):
        asyncio.run(runtime.cognitive_probe('LINK_MEMORY:'+'x'*40000,max_tokens=2400))


def test_chinese_selection_shrinks_before_dispatch(tmp_path):
    class Decision(BaseModel):
        answer: str
    class Runtime:
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            return json.dumps({'answer':'ok'})
    session=MemorySession(tmp_path/'cycle','chinese',MemoryService(tmp_path))
    def packet():
        return {'excerpts':['汉语长文本用于检索窗口预算验证。'*200]*session.state.get('prompt_window',8)}
    answer=asyncio.run(memory_decision(runtime=Runtime(),session=session,instruction='TEST:',
        payload_builder=packet,contract_builder=lambda:Decision))
    assert answer.answer=='ok'
    assert session.state['prompt_window']<8
    metric=json.loads((tmp_path/'cycle'/'memory_request_sizes.jsonl').read_text())
    assert metric['estimated_input_tokens']+metric['reserved_output_tokens']<=12000


def test_raw_archive_growth_does_not_grow_learning_prompt(tmp_path):
    from test_learning_research import Runtime, Explorer, FOCUS, step, INTRO
    from wechat_traffic_agent.learning_research import investigate_learning
    sizes=[]
    for factor in (1,10):
        root=tmp_path/str(factor)
        original={**INTRO,'evidence_text':INTRO['summary']+' checkpoint unrelated background '* (4000*factor)}
        history=[{'cycle':'old-'+str(i),'topic':'agent-reliability','facts':[{'evidence_id':'old-source-'+str(i),
            'quote':'Long archived raw quotation. '*40,'observation':'Old unrelated observed mechanism '+str(i)}]} for i in range(6*factor)]
        runtime=Runtime([step('LEARNED',INTRO)])
        # The inquiry decision double need not implement archive maintenance.
        class Wrapped:
            async def cognitive_probe(self,task,max_tokens,output_schema=None):
                if task.startswith('LINK_MEMORY:'): return json.dumps({'relations':[]})
                if task.startswith(('CONSOLIDATE_MEMORY:','CONTEXTUALIZE_MEMORY:')):
                    return await MaintenanceRuntime().cognitive_probe(task,max_tokens,output_schema)
                runtime.calls.append(task)
                data=json.JSONDecoder().raw_decode(task.split('\n',1)[1])[0]
                return json.dumps({**step('LEARNED',INTRO,related=None),'evidence_id':data['primary_source']['evidence_id'],
                    'facts':[{'source_quote_id':next(iter(data['source_quotes'])),'observation':'A source excerpt provides a concrete recovery observation.'}]})
        result=asyncio.run(investigate_learning(runtime=Wrapped(),explorer=Explorer(),folder=root/'cycle',state_root=root,
            topic='agent-reliability',focus=FOCUS,memory={'beliefs':[], 'research':{'history':history}},
            evidence=[original],selected=original,now=datetime.now(timezone.utc),search_rounds=0))
        assert result['progressed']
        sizes.append(len(runtime.calls[0]))
        assert len(runtime.calls[0])<36000
        assert 'Long archived raw quotation' not in runtime.calls[0]
    assert abs(sizes[1]-sizes[0])<1500


def test_staged_excerpt_waits_for_context_before_dense_indexing(tmp_path):
    service=MemoryService(tmp_path)
    service.ingest_source(source(),topic='restart')
    card=service.search('checkpoint',node_types=['evidence'])[0]
    assert service._node(card['node_id'])['vector'] is None
    asyncio.run(service.contextualize(MaintenanceRuntime(),[card['node_id']]))
    service.search('checkpoint',node_types=['evidence'])
    assert service._node(card['node_id'])['vector'] is not None


def test_upgraded_canonical_router_uses_shared_ann_view(tmp_path):
    from wechat_traffic_agent.memory_retrieval import MemoryRouter
    service=MemoryService(tmp_path)
    service.store.append_memory({'memory_id':'checkpoint','text':'A checkpoint restores worker state after restart.','kind':'FACT'})
    router=MemoryRouter(tmp_path)
    assert router.recall('checkpoint worker',limit=1)[0]['memory_id']=='checkpoint'
    with service.store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_ann_state').fetchone()[0]>0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_vector_index'").fetchone()


def test_receipt_import_selects_newest_revision_and_marks_older_observation(tmp_path):
    from wechat_traffic_agent.content_loop import _put
    old=source();new=source('The worker uses a checkpoint only after the external action.')
    for name,raw,time in [('v2-1',old,'2026-01-01T00:00:00+00:00'),('v2-2',new,'2026-02-01T00:00:00+00:00')]:
        row=research(raw,name);row['observed_at']=time
        _put(tmp_path/'content/continuous/cycles'/name/'learning_result.json',row)
    service=MemoryService(tmp_path);service.migrate_receipts(tmp_path)
    pointer=service._node('source:'+old['evidence_id'])['metadata']
    assert json.loads(Path(pointer['archive']).read_text())['evidence_text']==new['evidence_text']
    old_fact=next(r for r in service.store.list_memories(limit=None) if r['payload'].get('cycle')=='v2-1')
    assert old_fact['status']=='DISPUTED'


def test_expired_original_cannot_create_active_derived_summary(tmp_path):
    service=MemoryService(tmp_path)
    service.store.append_memory({'memory_id':'old','text':'An obsolete checkpoint fact.','kind':'FACT','valid_from':'1999-01-01T00:00:00Z','valid_until':'2000-01-01T00:00:00Z'})
    service.sync();service.put_node('derived','summary','A summary of an obsolete fact.',{},children=['old'])
    assert service._node('derived')['status']=='DIRTY'


def test_scheduler_slice_exhaustion_is_resumable_across_time_bucket(tmp_path):
    from wechat_traffic_agent.content_loop import ContinuousContentLoop
    from datetime import timedelta
    class Loop(ContinuousContentLoop):
        async def _run(self,folder,cycle,now):
            if not hasattr(self,'attempted'):
                self.attempted=True;raise MemoryContinuationRequired('durable memory checkpoint')
            return {'status':'DONE','cycle':cycle}
    loop=Loop.__new__(Loop);loop.root=tmp_path/'continuous';loop.interval_hours=6
    now=datetime.now(timezone.utc)
    first=asyncio.run(loop.run_once(now=now))
    assert first['status']=='MEMORY_RETRIEVAL_PENDING'
    assert not (loop.root/'cycles'/first['cycle']/'result.json').exists()
    resumed=asyncio.run(loop.run_once(now=now+timedelta(hours=7)))
    assert resumed['status']=='DONE' and resumed['cycle']==first['cycle']
