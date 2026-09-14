from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from wechat_traffic_agent.channel_evidence import ChannelEvidence,ChannelEvidenceStore

T=datetime(2026,1,1,tzinfo=timezone.utc)

def row(**kw):
    d=dict(channel='website',account='site',publication_id='p1',artifact_id='a1',published_content_sha256='a'*64,
           agent_candidate_sha256='a'*64,topic='validation',title_version='title:v1',structure_version='structure:v1',strategy_version='strategy:v1',
           objective='reads',distribution_cohort='organic',metric='views',metric_definition='analytics:pageviews:v1',unit='count',value=10,
           published_at=T,window_start=T,window_end=T+timedelta(days=1),observed_at=T+timedelta(days=2),revision=1,source='analytics-export',evidence_refs=['report:1'])
    d.update(kw);return ChannelEvidence(**d)

def test_revision_cutoff_unknown_and_restart(tmp_path):
    store=ChannelEvidenceStore(tmp_path)
    first=row(); path=store.record(first);assert store.record(first)==path
    store.record(row(value=20,revision=2,observed_at=T+timedelta(days=3)))
    assert store.as_of(T+timedelta(days=2))[0].value==10
    restored=ChannelEvidenceStore(tmp_path)
    assert restored.as_of(T+timedelta(days=4))[0].value==20
    with pytest.raises(FileExistsError): store.record(row(value=30))
    store.record(row(publication_id='p2',value=None))
    report=store.report(channel='website',account='site',cutoff=T+timedelta(days=4),dimension='topic')
    variant=report['groups'][0]['variants'][0]
    assert variant['observations']==1 and variant['unknown']==1 and variant['mean']==20
    assert report['publication_authority'] is False
    assert store.report(channel='wechat',account='site',cutoff=T+timedelta(days=4),dimension='topic')['status']=='NO_EVIDENCE'

@pytest.mark.parametrize('change',[{'metric_definition':'other'}, {'distribution_cohort':'paid'}, {'objective':'conversion'},
    {'window_end':T+timedelta(hours=12)}, {'agent_candidate_sha256':'b'*64}, {'agent_candidate_sha256':None},
    {'unit':'seconds'}, {'source':'different-provider'}])
def test_incompatible_measurements_and_editor_changes_are_not_pooled(tmp_path,change):
    s=ChannelEvidenceStore(tmp_path);s.record(row());s.record(row(publication_id='p2',**change))
    report=s.report(channel='website',account='site',cutoff=T+timedelta(days=4),dimension='strategy_version')
    assert len(report['groups'])==2

@pytest.mark.parametrize('change',[{'value':True},{'value':float('nan')},{'value':float('inf')},{'value':-1},
    {'unit':'ratio','value':1.1},{'value':1.5},{'observed_at':T},{'window_end':T}, {'evidence_refs':[]},
    {'evidence_refs':['same','same']},{'window_start':T.replace(tzinfo=None)}])
def test_invalid_or_immature_evidence_rejected(change):
    with pytest.raises(ValidationError): row(**change)
