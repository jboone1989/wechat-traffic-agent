import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
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
            return json.dumps({'grounded':self.passes,'useful':True,'no_private_data':True,'no_investment_advice':True,
                'applications':[{'technique_index':0,'article_quote':'Invented application quote' if self.badquote else 'Explanation of supplied evidence',
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
    async def bad(task,max_tokens): runtime.calls.append(task);return '{"unexpected":null}'
    runtime.cognitive_probe=bad
    assert asyncio.run(worker.run_once(now=NOW))['status']=='ERROR'
    assert len(runtime.calls)==2 and worker.inbox.list_records()==()
    assert asyncio.run(worker.run_once(now=NOW))['previous_status']=='ERROR'
    assert len(runtime.calls)==2


def test_interrupted_model_call_is_not_repeated(tmp_path):
    runtime=Runtime(); receipts=ReceiptRuntime(runtime,tmp_path/'receipts')
    async def crash(task,max_tokens): raise RuntimeError('simulated interruption')
    runtime.cognitive_probe=crash
    with pytest.raises(RuntimeError): asyncio.run(receipts.cognitive_probe('task',100))
    with pytest.raises(ValueError,match='interrupted'): asyncio.run(ReceiptRuntime(Runtime(),tmp_path/'receipts').cognitive_probe('task',100))


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


def test_receipt_recovery_does_not_guess_when_no_exact_later_request_exists(tmp_path):
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
    with pytest.raises(ValueError, match="input changed"):
        asyncio.run(ReceiptRuntime(Runtime(), root).cognitive_probe("phase-b", 100))


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
    async def mismatch(task,max_tokens):
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
    assert 'The website is the interface. The agent is the product.' in home
    assert 'Audited automatic publishing' in home
    assert 'Human-gated' not in home


@pytest.mark.parametrize('fault', ['noncontiguous_quote', 'section_index', 'unknown_source'])
def test_selfcheck_reference_format_is_repaired_once_without_loosening_evidence(tmp_path, fault):
    runtime,worker,publisher=setup(tmp_path)
    original=runtime.cognitive_probe
    checks=[]
    async def response(task,max_tokens):
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

    async def obey_goal(task, max_tokens):
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
