import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("maintenance_cognition")

from wechat_traffic_agent.content_loop import ContinuousContentLoop, ReceiptRuntime, parse_feed
from wechat_traffic_agent.automatic_website import AutomaticWebsitePublisher
from wechat_traffic_agent.channels.web import StaticWebsiteConfig
from wechat_traffic_agent.web_editorial import digest
from test_web_editorial import site

NOW = datetime(2026, 9, 14, 0, tzinfo=timezone.utc)
PARAGRAPH = ' '.join('Explanation of supplied evidence helps readers distinguish observations from hypothetical interpretation.' for _ in range(36))

class Runtime:
    agent_manifest = SimpleNamespace(agent_type='content-agent')
    def __init__(self, number=1, passes=True, badquote=False, provenance_ids=False):
        self.calls=[]; self.number=number; self.passes=passes; self.badquote=badquote; self.provenance_ids=provenance_ids
    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        if task.startswith('PLAN_LEARNING:'):
            return json.dumps({'question':'How does the supplied mechanism work in practice?',
                'search_query':'mechanism implementation primary evidence',
                'why_interested':'I want to understand the specific mechanism behind this observation.',
                'approach':'EXPLAIN','completion_criterion':'Explain the concrete mechanism and its supported observation.','method_change':''})
        if task.startswith('ADVANCE_LEARNING:'):
            data=json.JSONDecoder().raw_decode(task.split('\n',1)[1])[0]
            source=data['primary_source']
            return json.dumps({'progress':'LEARNED','evidence_id':source['evidence_id'],
                'question_status':'ANSWERED','inquiry_action':'NEXT_QUESTION','resume_when':'',
                'answer':'The source explains the mechanism with a concrete observation and its limited scope.',
                'reasoning_steps':[{'kind':'INFERENCE','fact_indices':[0],
                    'explanation':'This observation supports the documented case; it does not by itself establish that the same mechanism works in another case.'}],
                'learning_delta':'The source adds a concrete observation about the current question.',
                'facts':[{'source_quote_id':next(key for key, value in data['source_quotes'].items()
                          if value['evidence_id']==source['evidence_id']),
                          'observation':'This source supplies a concrete observation for this inquiry.'}],
                'related_proposition':None,'next_question':'How does this observation hold in another case?',
                'next_query':'mechanism another case comparison primary experiment',
                'rationale':'This fixture adds an observation rather than repeating the prior statement.'})
        if task.startswith('LEARN_WRITING:'):
            return json.dumps({'techniques':[{'name':'Separate observations', 'instruction':'Separate facts from hypothetical interpretation.',
                'before':'This always works.', 'after':'Hypothetically this could work; we need evidence.',
                'check':'Locate a clear distinction in the article.'}], 'adaptation':'Use the previous critique to make the explanation more concrete.'})
        if task.startswith('SELECT_NEWS:'):
            return json.dumps({'title':f'Explaining evidence question {self.number}', 'news_id':f'news-{self.number}',
                'topic':'rare','related_ids':['a0'],'reader_question':'What does this observation actually support?',
                'gap':'Connect the fresh announcement to existing evidence interpretation.'})
        if task.startswith('CHECK_ARTICLE:'):
            source_id = 'a0'
            if self.provenance_ids:
                import re
                source_id = re.search(r'content-artifact:a0:sha256:[a-f0-9]+', task).group()
            return json.dumps({'grounded':self.passes,'useful':True,'applications':[{'technique_index':0,'article_quote':'Invented application quote' if self.badquote else 'Explanation of supplied evidence',
                    'explanation':'The explanation distinguishes interpretation from evidence.'}],
                'support':[{'section_index':0,'source_id':source_id,'source_quote':'Existing bounded evidence.'}],
                'next_practice':'NEXT_PRACTICE_MARKER: make the opening reader question more specific.',
                'rationale':'The article explains a mechanism while preserving uncertainty.'})
        return json.dumps({'title':f'Explaining evidence question {self.number}', 'summary':'A bounded interpretation of fresh evidence.',
            'sections':[{'heading':'Explanation','paragraphs':[PARAGRAPH]}],
            'topic':f'Explaining evidence question {self.number}','source_urls':[], 'disclosure':None})
    def persist(self): pass


def setup(tmp_path, number=1, **kwargs):
    artifacts, web = site(tmp_path)
    public=tmp_path/'public';public.mkdir();(public/'current').symlink_to(web)
    curriculum=tmp_path/'practice.md';curriculum.write_text('Practice separating observations from hypothetical interpretations.')
    config=StaticWebsiteConfig(site_name='Lab',description='Evidence',base_url='https://example.com',indexable=False)
    publisher=AutomaticWebsitePublisher(state_root=tmp_path/'state',public_root=public,starter_path=artifacts,config=config)
    async def fetch(feeds, now):
        return {'items':[{'news_id':f'news-{number}', 'title':'New evidence announcement', 'summary':'The publisher reports a bounded observation.',
            'url':'https://example.org/news', 'published_at':NOW.isoformat(), 'publisher_feed':feeds[0]}], 'observed_at':now.isoformat(),'failures':[]}
    runtime=Runtime(number, **kwargs)
    worker=ContinuousContentLoop(runtime=runtime,state_root=tmp_path/'state',artifacts_path=artifacts,site_root=public/'current',
        base_url='https://example.com',curriculum_path=curriculum,feeds=['https://example.org/feed'],fetcher=fetch,publisher=publisher)
    return runtime,worker,publisher


def test_learn_publish_restart_and_next_cycle_reuses_experience(tmp_path):
    runtime,worker,publisher=setup(tmp_path)
    result=asyncio.run(worker.run_once(now=NOW))
    assert result['status']=='PUBLISHED' and len(runtime.calls)==4
    assert result['publication']['reviewer_kind']=='AUTOMATED'
    assert result['publication']['audit']['passed']
    current = publisher.public / 'current'
    about = (current / 'about' / 'index.html').read_text(encoding='utf-8')
    ferro = (current / 'agents' / 'ferro' / 'index.html').read_text(encoding='utf-8')
    assert 'About LivingRuntime' in about
    assert 'About the Content Agent' not in about
    assert 'LivingRuntime Agent #001' in ferro
    draft=json.loads(Path(result['draft_path']).read_text())
    assert draft['artifact']['public_safe'] is False
    assert len(json.loads(publisher.inventory_path().read_text()))==4
    before=list(runtime.calls)
    assert asyncio.run(worker.run_once(now=NOW))['status']=='ALREADY_PROCESSED'
    assert runtime.calls==before
    # New process, new source, next scheduled window; carry previous practice forward.
    runtime2=Runtime(2);worker.runtime=runtime2
    old_fetch=worker.fetcher
    async def new_fetch(feeds,now):
        row=await old_fetch(feeds,now);row['items'][0]['news_id']='news-2';return row
    worker.fetcher=new_fetch
    second=asyncio.run(worker.run_once(now=NOW+timedelta(hours=6)))
    assert second['status']=='PUBLISHED' and second['prior_experiences_used']==1
    assert 'NEXT_PRACTICE_MARKER' in runtime2.calls[0]
    assert 'Separate observations' in runtime2.calls[1] and 'Separate observations' in runtime2.calls[2]
    assert len(json.loads(publisher.inventory_path().read_text()))==5
    assert not (tmp_path/'state/content/reviews').exists()


@pytest.mark.parametrize('options',[{'passes':False}])
def test_failed_selfcheck_keeps_private_draft_and_previous_website(tmp_path,options):
    runtime,worker,publisher=setup(tmp_path,**options)
    before=(publisher.public/'current').resolve()
    result=asyncio.run(worker.run_once(now=NOW))
    assert result['status']=='HELD_BY_CHECKS'
    assert (publisher.public/'current').resolve()==before
    assert json.loads(Path(result['draft_path']).read_text())['artifact']['public_safe'] is False


def test_same_news_next_window_does_not_call_model_again(tmp_path):
    runtime,worker,publisher=setup(tmp_path)
    asyncio.run(worker.run_once(now=NOW));count=len(runtime.calls)
    assert asyncio.run(worker.run_once(now=NOW+timedelta(hours=6)))['status']=='NO_FRESH_EVIDENCE'
    assert len(runtime.calls)==count


def test_bad_model_response_bounded_persisted_no_retries_after_restart(tmp_path):
    runtime,worker,publisher=setup(tmp_path)
    async def bad(task, max_tokens, output_schema=None): runtime.calls.append(task);return '{"unexpected":null}'
    runtime.cognitive_probe=bad
    assert asyncio.run(worker.run_once(now=NOW))['status']=='ERROR'
    assert len(runtime.calls)==2 and worker.inbox.list_records()==()
    assert asyncio.run(worker.run_once(now=NOW))['previous_status']=='ERROR'
    assert len(runtime.calls)==2


def test_interrupted_cognition_retries_and_then_reuses_completed_receipt(tmp_path):
    runtime=Runtime(); receipts=ReceiptRuntime(runtime,tmp_path/'receipts')
    async def crash(task, max_tokens, output_schema=None): raise RuntimeError('simulated interruption')
    runtime.cognitive_probe=crash
    with pytest.raises(RuntimeError): asyncio.run(receipts.cognitive_probe('task',100))
    retry=Runtime()
    assert asyncio.run(ReceiptRuntime(retry,tmp_path/'receipts').cognitive_probe('task',100))
    assert retry.calls==['task']
    assert (tmp_path/'receipts'/'0.started').exists()
    assert (tmp_path/'receipts'/'1.json').exists()
    replay=Runtime()
    asyncio.run(ReceiptRuntime(replay,tmp_path/'receipts').cognitive_probe('task',100))
    assert replay.calls==[]


def test_receipt_runtime_forwards_schema_and_binds_it_to_phase_identity(tmp_path):
    class StructuredRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.schemas = []

        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            self.schemas.append(output_schema)
            return json.dumps({"ok": True})

    runtime = StructuredRuntime()
    receipts = ReceiptRuntime(runtime, tmp_path / "receipts")
    schema_a = {
        "type": "object",
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }
    schema_b = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
    }

    assert json.loads(asyncio.run(
        receipts.cognitive_probe("compose", 100, output_schema=schema_a)
    )) == {"ok": True}
    first = json.loads((tmp_path / "receipts" / "0.json").read_text())
    assert runtime.schemas == [schema_a]
    assert first["output_schema_sha256"] == digest(schema_a)

    changed_runtime = StructuredRuntime()
    changed = ReceiptRuntime(changed_runtime, tmp_path / "receipts")
    assert json.loads(asyncio.run(
        changed.cognitive_probe(
            "compose", 100, output_schema=schema_b
        )
    )) == {"ok": True}
    second = json.loads((tmp_path / "receipts" / "1.json").read_text())
    assert changed_runtime.schemas == [schema_b]
    assert second["output_schema_sha256"] == digest(schema_b)
    assert second["request_sha256"] != first["request_sha256"]


def test_receipt_recovery_can_skip_persisted_phase_only_by_exact_request_fingerprint(tmp_path):
    root = tmp_path / "receipts"
    root.mkdir()
    wanted_task = "phase-b"
    (root / "0.json").write_text(json.dumps({
        "request_sha256": digest({"task": "phase-a", "max_tokens": 100}),
        "response": "old-response",
    }))
    (root / "1.json").write_text(json.dumps({
        "request_sha256": digest({"task": wanted_task, "max_tokens": 100}),
        "response": "wanted-response",
    }))
    runtime = Runtime()
    receipts = ReceiptRuntime(runtime, root)
    assert asyncio.run(receipts.cognitive_probe(wanted_task, 100)) == "wanted-response"
    assert receipts.index == 2
    assert runtime.calls == []


def test_mechanical_receipt_does_not_reuse_normal_receipt(tmp_path):
    class DualRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.mechanical_calls = []

        async def mechanical_cognitive_probe(
            self, task, max_tokens, output_schema=None
        ):
            self.mechanical_calls.append(task)
            return "mechanical-response"

    root = tmp_path / "receipts"
    normal_runtime = Runtime()
    normal = ReceiptRuntime(normal_runtime, root)
    assert asyncio.run(normal.cognitive_probe("same-task", 100))
    first = json.loads((root / "0.json").read_text())
    assert first["request_sha256"] == digest(
        {"task": "same-task", "max_tokens": 100}
    )

    mechanical_runtime = DualRuntime()
    mechanical = ReceiptRuntime(mechanical_runtime, root)
    assert asyncio.run(
        mechanical.mechanical_cognitive_probe("same-task", 100)
    ) == "mechanical-response"
    second = json.loads((root / "1.json").read_text())
    assert second["cognition_mode"] == "MECHANICAL"
    assert second["request_sha256"] != first["request_sha256"]
    assert mechanical_runtime.mechanical_calls == ["same-task"]
    assert mechanical_runtime.calls == []


def test_changed_request_generates_new_response_without_reusing_old_input(tmp_path):
    root = tmp_path / "receipts"
    root.mkdir()
    (root / "0.json").write_text(json.dumps({
        "request_sha256": digest({"task": "phase-a", "max_tokens": 100}),
        "response": "old-response",
    }))
    (root / "1.json").write_text(json.dumps({
        "request_sha256": digest({"task": "other-phase", "max_tokens": 100}),
        "response": "other-response",
    }))
    runtime = Runtime()
    response = asyncio.run(ReceiptRuntime(runtime, root).cognitive_probe("phase-b", 100))
    assert runtime.calls == ["phase-b"]
    receipt = json.loads((root / "2.json").read_text())
    assert receipt["response"] == response
    assert receipt["request_sha256"] == digest({"task": "phase-b", "max_tokens": 100})
    assert json.loads((root / "0.json").read_text())["response"] == "old-response"


def test_feed_freshness_content_identity_and_xml_budget():
    def feed(date): return f'<rss><channel><item><title>News</title><link>https://example.org/story</link><description>Reported evidence</description><pubDate>{date}</pubDate></item></channel></rss>'.encode()
    a=parse_feed(feed('Sun, 13 Sep 2026 00:00:00 GMT'),'https://example.org/rss',NOW)
    b=parse_feed(feed('Sun, 13 Sep 2026 01:00:00 GMT'),'https://example.org/rss',NOW)
    assert a[0]['news_id']==b[0]['news_id']
    assert parse_feed(feed('Sun, 01 Sep 2024 00:00:00 GMT'),'feed',NOW)==[]
    assert parse_feed(feed('Sun, 20 Sep 2026 00:00:00 GMT'),'feed',NOW)==[]
    with pytest.raises(ValueError): parse_feed(b'<!DOCTYPE rss><rss/>','feed',NOW)
    with pytest.raises(ValueError): parse_feed(b'x'*512001,'feed',NOW)


def test_audit_failure_does_not_activate_candidate(tmp_path,monkeypatch):
    runtime,worker,publisher=setup(tmp_path)
    old=(publisher.public/'current').resolve()
    monkeypatch.setattr('wechat_traffic_agent.automatic_website.audit_static_site',lambda *a,**k:SimpleNamespace(passed=False))
    assert asyncio.run(worker.run_once(now=NOW))['status']=='ERROR'
    assert (publisher.public/'current').resolve()==old


def test_real_model_provenance_identifiers_are_resolved_without_weakening_quotes(tmp_path):
    runtime,worker,publisher=setup(tmp_path,provenance_ids=True)
    assert asyncio.run(worker.run_once(now=NOW))['status']=='PUBLISHED'


@pytest.mark.parametrize('repaired', [True, False])
def test_semantic_topic_mismatch_gets_one_bounded_repair(tmp_path, repaired):
    runtime,worker,publisher=setup(tmp_path)
    original=runtime.cognitive_probe
    selection_calls=[]
    async def mismatch(task, max_tokens, output_schema=None):
        raw=await original(task,max_tokens)
        if task.startswith('SELECT_NEWS:'):
            selection_calls.append(task)
            value=json.loads(raw)
            if len(selection_calls)==1 or not repaired:
                value['topic']='common'
            raw=json.dumps(value)
        return raw
    runtime.cognitive_probe=mismatch
    result=asyncio.run(worker.run_once(now=NOW))
    assert len(selection_calls)==2
    assert 'Allowed IDs for this topic' in selection_calls[1]
    assert result['status']==('PUBLISHED' if repaired else 'ERROR')
    if not repaired:
        assert worker.inbox.list_records()==()


def test_automatic_release_readable_with_restrictive_service_umask(tmp_path):
    import os
    runtime,worker,publisher=setup(tmp_path)
    old=os.umask(0o077)
    try:
        assert asyncio.run(worker.run_once(now=NOW))['status']=='PUBLISHED'
    finally:
        os.umask(old)
    root=(publisher.public/'current').resolve()
    assert root.parent.stat().st_mode & 0o005 == 0o005
    assert root.stat().st_mode & 0o005 == 0o005
    assert all(p.stat().st_mode & (0o005 if p.is_dir() else 0o004)
               for p in root.rglob('*'))
    home=(root/'index.html').read_text()
    assert 'AI agents that live,' in home
    assert 'learn, and work together.' in home
    assert 'LIVINGRUNTIME · PERSISTENT AGENT ECOSYSTEM' in home
    assert 'I’m Content Agent.' not in home
    assert 'Human-gated' not in home


@pytest.mark.parametrize('fault', ['noncontiguous_quote', 'section_index', 'unknown_source'])
def test_selfcheck_reference_format_is_repaired_once_without_loosening_evidence(tmp_path, fault):
    runtime,worker,publisher=setup(tmp_path)
    original=runtime.cognitive_probe
    checks=[]
    async def response(task, max_tokens, output_schema=None):
        raw=await original(task,max_tokens)
        if task.startswith('CHECK_ARTICLE:'):
            checks.append(task)
            row=json.loads(raw)
            if len(checks)==1:
                if fault=='noncontiguous_quote': row['applications'][0]['article_quote']='Explanation ... distinguish observations'
                elif fault=='section_index': row['support'][0]['section_index']=1
                else: row['support'][0]['source_id']='invented-source'
            raw=json.dumps(row)
        return raw
    runtime.cognitive_probe=response
    assert asyncio.run(worker.run_once(now=NOW))['status']=='PUBLISHED'
    assert len(checks)==2 and 'One repair only' in checks[1]


def test_persistently_invented_check_quote_is_error_and_never_published(tmp_path):
    runtime,worker,publisher=setup(tmp_path,badquote=True)
    before=(publisher.public/'current').resolve()
    assert asyncio.run(worker.run_once(now=NOW))['status']=='ERROR'
    assert sum(t.startswith('CHECK_ARTICLE:') for t in runtime.calls)==2
    assert worker.inbox.list_records()==()
    assert (publisher.public/'current').resolve()==before


def test_virtualbrain_required_topic_changes_selection_behavior(tmp_path):
    runtime, worker, _publisher = setup(tmp_path)
    original = runtime.cognitive_probe
    selection_calls = []

    class Mind:
        def plan_for_cycle(self, *, cycle, available_topics):
            assert 'common' in available_topics
            return {'cycle': cycle, 'goal': {'origin': 'INTEREST', 'interest_refs': ['common']},
                    'required_topic': 'common'}

        def observe_process_experience(self, **kwargs):
            return {'status': 'OBSERVED', 'topic': kwargs['topic']}

    worker.mind = Mind()

    async def obey_goal(task, max_tokens, output_schema=None):
        raw = await original(task, max_tokens)
        if task.startswith('SELECT_NEWS:'):
            selection_calls.append(task)
            row = json.loads(raw)
            if len(selection_calls) > 1:
                row.update(topic='common', related_ids=['a1'])
            return json.dumps(row)
        if task.startswith('CHECK_ARTICLE:'):
            row = json.loads(raw)
            row['support'][0]['source_id'] = 'a1'
            return json.dumps(row)
        return raw

    runtime.cognitive_probe = obey_goal
    result = asyncio.run(worker.run_once(now=NOW))
    assert result['status'] == 'PUBLISHED'
    assert len(selection_calls) == 2
    assert 'VirtualBrain endogenous goal requires topic: common' in selection_calls[1]
    assert result['mind_plan']['required_topic'] == 'common'
    draft = json.loads(Path(result['draft_path']).read_text())
    assert draft['artifact']['tags'] == ['common']
    assert result['mind_experience']['topic'] == 'common'


def test_mind_persistence_failure_prevents_publication(tmp_path):
    runtime, worker, publisher = setup(tmp_path)
    before = (publisher.public / 'current').resolve()

    class FailingMind:
        def plan_for_cycle(self, *, cycle, available_topics):
            return {'cycle': cycle, 'goal': None, 'required_topic': None}

        def observe_process_experience(self, **kwargs):
            raise OSError('simulated mind persistence failure')

    worker.mind = FailingMind()
    result = asyncio.run(worker.run_once(now=NOW))
    assert result['status'] == 'ERROR'
    assert result['error_type'] == 'OSError'
    assert (publisher.public / 'current').resolve() == before


def test_directed_cycle_id_allows_a_new_audited_cycle_without_erasing_bucket_history(tmp_path):
    runtime, worker, _publisher = setup(tmp_path)
    first = asyncio.run(worker.run_once(now=NOW))
    assert first["status"] == "PUBLISHED"
    runtime.number = 2
    async def new_fetch(feeds, now):
        return {"items":[{"news_id":"news-2", "title":"Directed fresh evidence", "summary":"A fresh bounded observation.",
            "url":"https://example.org/directed", "published_at":NOW.isoformat(), "publisher_feed":feeds[0]}],
            "observed_at":now.isoformat(), "failures":[]}
    worker.fetcher = new_fetch
    directed = asyncio.run(worker.run_once(now=NOW, cycle_id="v2-directed-1789491601"))
    assert directed["cycle"] == "v2-directed-1789491601"
    assert directed["status"] == "PUBLISHED"
    assert (worker.root / "cycles" / first["cycle"] / "result.json").exists()
    assert (worker.root / "cycles" / "v2-directed-1789491601" / "result.json").exists()


def test_preflight_is_local_and_distinguishes_processed_cycle(tmp_path):
    runtime, worker, _publisher = setup(tmp_path)
    before_calls = list(runtime.calls)
    first = worker.preflight(now=NOW)
    assert first["status"] == "READY"
    assert first["cycle"].startswith("v2-")
    assert runtime.calls == before_calls

    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    calls_after_publish = list(runtime.calls)
    replay = worker.preflight(now=NOW)
    assert replay == {**replay, "status": "ALREADY_PROCESSED"}
    assert replay["cycle"] == first["cycle"]
    assert runtime.calls == calls_after_publish



def test_changed_model_phase_input_invalidates_stale_recovery_without_retrying_model(tmp_path):
    runtime, worker, _publisher = setup(tmp_path)

    async def stale_recovery(folder, cycle, now):
        raise ValueError("model phase input changed during recovery")

    worker._run = stale_recovery
    result = asyncio.run(worker.run_once(now=NOW))

    assert result["status"] == "RECOVERY_INVALIDATED"
    assert result["reason"] == "MODEL_PHASE_INPUT_CHANGED"
    assert runtime.calls == []
    cycle_dir = Path(result["diagnostic_ref"].split(":")[1]) if False else next(
        (worker.root / "cycles").glob("v2-*")
    )
    diagnostic = json.loads((cycle_dir / "error_diagnostic.json").read_text())
    invalidated = json.loads((cycle_dir / "recovery_invalidated.json").read_text())
    persisted = json.loads((cycle_dir / "result.json").read_text())
    assert diagnostic["message"] == "model phase input changed during recovery"
    assert "stale_recovery" in diagnostic["traceback"]
    assert invalidated["reason"] == "MODEL_PHASE_INPUT_CHANGED"
    assert persisted["status"] == "RECOVERY_INVALIDATED"

def test_changed_input_preserves_interrupted_request_and_appends_new_receipt(tmp_path):
    root=tmp_path/"receipts"
    runtime=Runtime()
    async def crash(task,max_tokens,output_schema=None):
        raise RuntimeError("explicit failed old generation")
    runtime.cognitive_probe=crash
    with pytest.raises(RuntimeError):
        asyncio.run(ReceiptRuntime(runtime,root).cognitive_probe("old oversized input",100))
    before=(root/"0.started").read_bytes()
    fresh=Runtime()
    asyncio.run(ReceiptRuntime(fresh,root).cognitive_probe("new projected input",100))
    assert fresh.calls==["new projected input"]
    assert (root/"0.started").read_bytes()==before
    receipt=json.loads((root/"1.json").read_text())
    assert receipt["changed_input_after_interrupted_requests"][0]["request_sha256"] == json.loads(before)["request_sha256"]
    # Recovery of the same new input uses its receipt without another call.
    replay=Runtime()
    asyncio.run(ReceiptRuntime(replay,root).cognitive_probe("new projected input",100))
    assert replay.calls==[]

def test_site_maintenance_and_public_url_are_first_class(tmp_path, monkeypatch):
    runtime, worker, publisher = setup(tmp_path)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    publication = result["publication"]
    assert publication["site_url"] == (
        publisher.config.base_url + publication["outcome_attribution"]["public_route"]
    )
    first = publisher.maintain(now=NOW)
    assert first["status"] == "HEALTHY"
    assert first["article_count"] >= 1
    assert first["audit"]["passed"] is True
    assert (publisher.state / "maintenance" / (NOW.date().isoformat() + ".json")).is_file()
    monkeypatch.setattr(
        "wechat_traffic_agent.automatic_website.audit_static_site",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("repeat audit")),
    )
    assert publisher.maintain(now=NOW) == first
