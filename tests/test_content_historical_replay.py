from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.content_historical_replay import build_historical_content_priors
NOW=datetime(2026,9,17,4,0,tzinfo=timezone.utc); ACCOUNT='https://example.com'
def _pub(root,cycle,rev,decided,published,profile='evidence-brief'):
 p=root/'content/continuous/cycles'/cycle/'publication.json'; p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'revision':rev,'outcome_attribution':{'published_at':published.isoformat(),'topic':'model-evaluation','content_strategy':{'strategy_id':f'strategy:{cycle}','decided_at':decided.isoformat(),'topic':'model-evaluation','profile':{'name':profile}}}}))
def _outcome(root,rev,published,observed,value):
 ChannelEvidenceStore(root/'content/channel_evidence').record(ChannelEvidence(channel='website',account=ACCOUNT,publication_id=rev,artifact_id=rev,published_content_sha256='a'*64,agent_candidate_sha256='a'*64,topic='model-evaluation',title_version='t',structure_version='s',strategy_version='v',objective='website_pageviews',distribution_cohort='web',metric='http_pageviews',metric_definition='views',unit='count',value=float(value),published_at=published,window_start=published,window_end=published+timedelta(hours=24),observed_at=observed,revision=1,source='nginx-access-log:v1',evidence_refs=(f'e:{rev}',)))
def test_legacy_outcome_without_strategy_binding_is_not_hindsight_labeled(tmp_path):
 root=tmp_path/'state'; published=NOW-timedelta(days=2)
 p=root/'content/continuous/cycles/legacy/publication.json'; p.parent.mkdir(parents=True); p.write_text(json.dumps({'revision':'a'*64,'outcome_attribution':{'published_at':published.isoformat(),'topic':'model-evaluation'}}))
 _outcome(root,'a'*64,published,published+timedelta(hours=25),10)
 report=build_historical_content_priors(state_root=root,account=ACCOUNT,as_of=NOW)
 assert report['status']=='NO_CAUSALLY_REPLAYABLE_SAMPLES' and report['records']==[]
def test_replay_uses_only_prior_cutoff_and_accumulates_historical_not_prospective(tmp_path):
 root=tmp_path/'state'; p1=NOW-timedelta(days=5); p2=NOW-timedelta(days=3)
 _pub(root,'c1','b'*64,p1-timedelta(minutes=5),p1); _outcome(root,'b'*64,p1,p1+timedelta(hours=25),10)
 _pub(root,'c2','c'*64,p2-timedelta(minutes=5),p2); _outcome(root,'c'*64,p2,p2+timedelta(hours=25),6)
 report=build_historical_content_priors(state_root=root,account=ACCOUNT,as_of=NOW)
 assert report['status']=='REPLAYED' and len(report['records'])==2
 assert report['scorecard']['historical_prior']['outcomes']==2
 assert report['scorecard']['prospective']['outcomes']==0
 assert all(r['prediction_id'].startswith('content-historical:') for r in report['records'])
