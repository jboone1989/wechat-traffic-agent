import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.channels.web import StaticWebChannel, StaticWebsiteConfig
from wechat_traffic_agent.web_editorial import WebEditorialWorker, website_snapshot, rank_proposals, _Proposals


def site(tmp_path):
    rows = [ContentArtifact(artifact_id=f"a{i}", kind="article", slug=f"article-{i}", title=f"Existing article number {i}",
            summary="Existing bounded evidence.", sections=(ContentSection(heading="Facts", paragraphs=("Existing explanation.",)),),
            tags=("rare",) if i == 0 else ("common",), created_at=datetime(2026,1,1,tzinfo=timezone.utc), public_safe=True)
            for i in range(3)]
    artifacts=tmp_path/'articles.json';artifacts.write_text(json.dumps([a.model_dump(mode='json') for a in rows]))
    root=tmp_path/'site'
    StaticWebChannel(StaticWebsiteConfig(site_name="Lab",description="Evidence",base_url="https://example.com")).build(rows,root)
    return artifacts,root


def candidate(topic='rare',related=None):
    return dict(title="A genuinely new explanation" if topic=='rare' else "A different common explanation",topic=topic,
                gap="Missing explanation beyond the existing introduction.",related_ids=related or ['a0'],
                evidence_needed=['Existing article explanations'],evidence_status='EXISTING_CONTENT')


class Runtime:
    agent_manifest=SimpleNamespace(agent_type='content-agent')
    def __init__(self, external=False): self.calls=[];self.external=external
    async def cognitive_probe(self,task,max_tokens,output_schema=None):
        self.calls.append(task)
        if 'Propose up to five' in task:
            row=candidate()
            if self.external: row['evidence_status']='EXTERNAL_REQUIRED'
            return json.dumps({'candidates':[row]})
        return json.dumps(dict(title='A genuinely new explanation',summary='Grounded explanation.',
                         sections=[dict(heading='Explanation',paragraphs=['A bounded synthesis.'])],
                         topic='A genuinely new explanation',source_urls=[],disclosure=None))
    def persist(self): pass


def test_snapshot_and_ranking_use_coverage_and_article_links(tmp_path):
    artifacts,root=site(tmp_path)
    snap=website_snapshot(artifacts,root,'https://example.com')
    assert snap['topics']=={'common':['a1','a2'],'rare':['a0']}
    assert all(a!=b for a,b in snap['article_links'])
    # A global nav link is not an editorial incoming link.
    p=root/'articles/article-1/index.html'
    p.write_text(p.read_text().replace('<nav aria-label="Primary">','<nav aria-label="Primary"><a href="/articles/article-0/">nav</a>'))
    after=website_snapshot(artifacts,root,'https://example.com')
    assert after['article_links']==snap['article_links']
    ranked=rank_proposals(snap,_Proposals(candidates=[candidate('common',['a1']),candidate()]))
    assert ranked[0]['topic']=='rare'
    assert ranked[0]['suggested_links']==['/articles/article-0/']
    with pytest.raises(ValueError): rank_proposals(snap,_Proposals(candidates=[candidate('invented')]))
    with pytest.raises(ValueError): rank_proposals(snap,_Proposals(candidates=[candidate(related=['unknown'])]))
    (root/'articles/article-0/index.html').unlink()
    with pytest.raises(ValueError,match='inventory disagree'): website_snapshot(artifacts,root,'https://example.com')



def test_snapshot_treats_notes_as_first_class_inventory(tmp_path):
    rows = [
        ContentArtifact(
            artifact_id="article-1", kind=ContentKind.ARTICLE, slug="article-one",
            title="Article one", summary="A bounded article.",
            sections=(ContentSection(heading="Facts", paragraphs=("Grounded article text.",)),),
            tags=("shared",), created_at=datetime(2026, 9, 16, tzinfo=timezone.utc), public_safe=True,
        ),
        ContentArtifact(
            artifact_id="note-1", kind=ContentKind.NOTE, slug="note-one",
            title="Note one", summary="A bounded note.",
            sections=(ContentSection(heading="Thought", paragraphs=("Grounded note text.",)),),
            tags=("shared",), created_at=datetime(2026, 9, 16, tzinfo=timezone.utc), public_safe=True,
        ),
    ]
    artifacts = tmp_path / "inventory.json"
    artifacts.write_text(json.dumps([row.model_dump(mode="json") for row in rows]))
    root = tmp_path / "site"
    StaticWebChannel(StaticWebsiteConfig(site_name="Lab", description="Evidence", base_url="https://example.com")).build(rows, root)
    snap = website_snapshot(artifacts, root, "https://example.com")
    assert {row["route"] for row in snap["articles"]} == {"/articles/article-one/", "/notes/note-one/"}
    assert snap["topics"]["shared"] == ["article-1", "note-1"]

def test_real_compose_path_drafts_once_and_restored_plan_skips_both_models(tmp_path):
    artifacts,root=site(tmp_path)
    runtime=Runtime()
    kwargs=dict(state_root=tmp_path/'state',artifacts_path=artifacts,site_root=root,base_url='https://example.com')
    first=asyncio.run(WebEditorialWorker(runtime=runtime,**kwargs).run_once())
    assert first['status']=='DRAFT_CREATED' and len(runtime.calls)==2
    plan=json.loads(open(first['plan_path']).read())
    assert plan['candidates'][0]['gap'] and plan['candidates'][0]['evidence_needed']
    restarted=Runtime()
    second=asyncio.run(WebEditorialWorker(runtime=restarted,**kwargs).run_once())
    assert second['status']=='ALREADY_DRAFTED' and restarted.calls==[]
    draft=json.loads(open(first['draft_path']).read())
    assert draft['artifact']['public_safe'] is False
    assert draft['artifact']['source_refs'][0]['source']=='content-website'
    assert not (tmp_path/'state/content/reviews').exists()


def test_missing_external_evidence_stops_before_generation(tmp_path):
    artifacts,root=site(tmp_path); runtime=Runtime(external=True)
    worker=WebEditorialWorker(runtime=runtime,state_root=tmp_path/'state',artifacts_path=artifacts,site_root=root,base_url='https://example.com')
    assert asyncio.run(worker.run_once())['status']=='NEEDS_EVIDENCE'
    assert asyncio.run(worker.run_once())['status']=='NEEDS_EVIDENCE'
    assert len(runtime.calls)==1
    assert worker.inbox.list_records()==()


def test_invalid_plan_repair_is_bounded_without_draft(tmp_path):
    artifacts,root=site(tmp_path)
    class Bad(Runtime):
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            self.calls.append(task)
            return json.dumps({'candidates':[candidate(related=['invented'])]})
    runtime=Bad();worker=WebEditorialWorker(runtime=runtime,state_root=tmp_path/'state',artifacts_path=artifacts,site_root=root,base_url='https://example.com')
    with pytest.raises(ValueError): asyncio.run(worker.run_once())
    assert len(runtime.calls)==2 and worker.inbox.list_records()==()
