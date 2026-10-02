import asyncio
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
import os
from unittest.mock import AsyncMock, patch

from wechat_traffic_agent import x_social as social, x_growth_mission as growth
from wechat_traffic_agent.x_browser import _post_id_from_url, _draft_sha256
from wechat_traffic_agent.x_outcomes import XOutcomeCollector
from wechat_traffic_agent.x_article_media import generate_article_cover
from wechat_traffic_agent.social_expression import ChannelSocialDraft

NOW=datetime(2026,10,2,12,tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


class TestGrowthLoop(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)

    def loop(self, posts=None, handler=None):
        transport=SimpleNamespace(identity=AsyncMock(return_value={"username":"ferro", "public_metrics":{"followers_count":1794}}),
            discover=AsyncMock(return_value=posts or [{"id":"123", "text":"How should persistent AI agents recover state?",
                                                     "username":"reader","url":"https://x.com/reader/status/123"}]))
        runtime=SimpleNamespace(invoke=lambda *a,**kw:self.fail('No X API call is allowed'))
        l1=SimpleNamespace(choose_x_discovery=lambda **kw:{"source":"HOME"})
        loop=social.XAutonomyLoop(runtime=runtime,model_runtime=SimpleNamespace(agent_manifest=None),state_root=self.root,
            base_url='',browser_publish_transport=transport,l1_decisions=l1)
        loop._handle_mention=handler or AsyncMock(return_value=True)
        return loop,transport

    def test_browser_discovery_never_requires_api_credits(self):
        loop,t=self.loop()
        result=asyncio.run(loop.observe(now=NOW))
        self.assertEqual(result['status'],'OK')
        self.assertEqual(result['new_mentions'],1)
        t.discover.assert_awaited_once_with(source='HOME',max_results=20)
        self.assertEqual(json.loads(loop.profile_path.read_text())['identity_source'],'JEV_AGENT_RUNTIME')

    def test_search_routes_agent_generated_query_and_persists_its_reason(self):
        from wechat_traffic_agent.x_discovery import XSearchPlan
        loop, transport = self.loop()
        loop.l1_decisions = SimpleNamespace(choose_x_discovery=lambda **kw: {"source": "SEARCH"})
        plan = XSearchPlan(domain="FINANCE", query="sovereign debt refinancing",
                           question="What changes refinancing risk?", rationale="A useful question in my finance interests.")
        with patch("wechat_traffic_agent.x_discovery.plan_x_search", AsyncMock(return_value=plan)):
            result = asyncio.run(loop.observe(now=NOW))
        self.assertEqual(result["status"], "OK")
        transport.discover.assert_awaited_once_with(source="SEARCH", query=plan.query, max_results=20)
        saved = json.loads((loop.root / "discovery.json").read_text())
        self.assertEqual(saved["decision"]["domain"], "FINANCE")
        self.assertEqual(saved["decision"]["query"], plan.query)
        self.assertEqual(saved["decision"]["question"], plan.question)

    def test_failed_query_generation_does_not_fall_back_to_agent_memory(self):
        loop, transport = self.loop()
        loop.l1_decisions = SimpleNamespace(choose_x_discovery=lambda **kw: {"source": "SEARCH"})
        with patch("wechat_traffic_agent.x_discovery.plan_x_search", AsyncMock(side_effect=RuntimeError("cognition unavailable"))):
            result = asyncio.run(loop.observe(now=NOW))
        self.assertEqual(result["status"], "X_OBSERVATION_FAILED")
        transport.discover.assert_not_awaited()
        self.assertIn("cognition unavailable", result["detail"])

    def test_failed_reply_remains_retryable(self):
        handler=AsyncMock(side_effect=[False,True])
        loop,t=self.loop(handler=handler)
        first=asyncio.run(loop.observe(now=NOW))
        self.assertEqual(first['status'],'DEGRADED')
        self.assertNotIn('123',json.loads(loop.seen_path.read_text())['ids'])
        second=asyncio.run(loop.observe(now=NOW))
        self.assertEqual(second['status'],'OK')
        self.assertIn('123',json.loads(loop.seen_path.read_text())['ids'])
        self.assertEqual(handler.await_count,2)

    def test_committed_post_is_not_handled_twice(self):
        loop,t=self.loop()
        asyncio.run(loop.observe(now=NOW))
        result=asyncio.run(loop.observe(now=NOW))
        self.assertEqual(result['new_mentions'],0)
        self.assertEqual(loop._handle_mention.await_count,1)

    def test_failed_discovery_cannot_report_success(self):
        loop,t=self.loop()
        t.discover.side_effect=RuntimeError('browser unavailable')
        result=asyncio.run(loop.observe(now=NOW))
        self.assertEqual(result['status'],'X_OBSERVATION_FAILED')
        self.assertFalse(loop.seen_path.exists())

    def test_cleared_blocker_overrides_stale_projection(self):
        folder=self.root/'content/x'
        write(folder/'growth_mission.json',{'schema_version':growth.MISSION_SCHEMA,'execution_blocker':{'status':'ACTIVE'}})
        write(folder/'publication_blocker.json',{'status':'CLEARED'})
        self.assertIsNone(growth.x_growth_mission_context(self.root)['execution_blocker'])

    def test_quantity_suggestions_are_only_advisory_context(self):
        folder=self.root/'content/x'
        write(folder/'growth_mission.json',{'schema_version':growth.MISSION_SCHEMA})
        write(folder/'strategy_advice.json',{'authority':'ADVISORY_ONLY','quotas_required':False,
                                           'suggested_posts_per_day':20,'suggested_replies_per_day':200})
        advice=growth.x_growth_mission_context(self.root)['owner_strategy_advice']
        self.assertFalse(advice['quotas_required'])

    def test_running_experiment_accepts_additional_snapshot_without_restart(self):
        original_start=NOW
        exp=SimpleNamespace(status='RUNNING',experiment_id='exp-1',evidence_refs=['first-publication'],
                            started_at=original_start,baseline_value=1794)
        mission=SimpleNamespace(mission_id='mission-1',status='ACTIVE')
        runtime=SimpleNamespace(observe_mission_metric=lambda *a,**kw:SimpleNamespace(observation_id='obs-2',observed_at=NOW),
                                start_mission_experiment=lambda *a:self.fail('Running experiment must not restart'))
        coordinator=growth.XGrowthMissionCoordinator(runtime=runtime,state_root=self.root,enabled=True)
        coordinator._mission=lambda:mission
        coordinator._open_experiment=lambda mid:exp
        coordinator.publication_binding=lambda **kw:{'bound':True}
        coordinator._persist_runtime=lambda:None
        coordinator._record_publication_for_roadmap=lambda **kw:{'status':'RECORDED'}
        snapshot=coordinator.record_publication_baseline(cycle='second',user={'public_metrics':{'followers_count':1796}},
                    identity_receipt_id='identity-2',now=NOW)
        self.assertEqual(snapshot['status'],'RECORDED')
        result=coordinator.start_experiment_from_publication(cycle='second',publication_receipt_id='receipt-2',post_id='222',now=NOW)
        self.assertEqual(result['status'],'ALREADY_RUNNING')
        self.assertEqual(exp.started_at,original_start)
        self.assertEqual(exp.baseline_value,1794)
        self.assertIn('x-post:222',exp.evidence_refs)

    def test_autonomous_short_post_is_eligible_without_website(self):
        post_id='2105973045118804419';url=f'https://x.com/ferro/status/{post_id}'
        receipt='x-browser-publication:standalone:'+post_id
        write(self.root/'content/continuous/cycles/standalone/x_social_expression.json',
              {'status':'POSTED','post_id':post_id,'url':url,'receipt_id':receipt})
        social.XHistoryLedger(self.root).append({'event_type':'EXPRESSION','cycle':'standalone','text':'My durable state survives worker restart.',
            'x_post_id':post_id,'x_url':url,'runtime_receipt_id':receipt,'cognition':{'topic':'agent state'}})
        collector=XOutcomeCollector(state_root=self.root,transport=SimpleNamespace())
        rows=collector._publications()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['post_id'],post_id)

    def test_unverified_history_has_no_outcome_authority(self):
        social.XHistoryLedger(self.root).append({'event_type':'REPLY','cycle':'social','text':'A reply.',
            'x_post_id':'2105973045118804419','x_url':'https://x.com/ferro/status/2105973045118804419',
            'target_post_id':'123','publication_verified':False})
        self.assertEqual(XOutcomeCollector(state_root=self.root,transport=SimpleNamespace())._publications(),[])

    def test_article_url_and_title_validation(self):
        self.assertEqual(_post_id_from_url('https://x.com/i/article/2105980723614158848'),'2105980723614158848')
        self.assertIsNone(_post_id_from_url('https://evil.example/i/article/123'))
        draft={'channel':'X','format':'ARTICLE','text':'A supported explanation of durable agent state.',
               'style_observation_ids':[],'rationale':'Structured treatment is useful here.'}
        with self.assertRaises(ValueError):
            ChannelSocialDraft.model_validate(draft)
        draft['title']='Durable agent state'
        self.assertEqual(ChannelSocialDraft.model_validate(draft).title,draft['title'])

    def test_original_cover_has_pixels_and_authorship_receipt(self):
        from PIL import Image
        receipt=generate_article_cover(title='Durable agent state',caption='Ferro / continuity notes',folder=self.root)
        with Image.open(receipt['path']) as image:
            self.assertEqual(image.size,(1500,600))
            self.assertIsNotNone(image.getbbox())
            self.assertGreater(len(image.getcolors(1500*600)),5)
        self.assertEqual(receipt['license'],'ORIGINAL_WORK')
        self.assertFalse(receipt['ai_image_model'])

    def test_owner_article_proceeds_without_website_artifact(self):
        loop,transport=self.loop()
        loop._handle_mention=None
        folder=self.root/'content/continuous/cycles/owner-article'
        folder.mkdir(parents=True)
        request={'decision':{'publication_surface':'X_ARTICLE'},'owner_goal_id':'growth-goal'}
        context={'owner_publication_request':request,'source_evidence':{'summary':'External state survives worker restart.'},
                 'cognition':{'worth_saying':True},'cycle':folder.name}
        draft=ChannelSocialDraft(channel='X',format='ARTICLE',title='Recoverable agent state',
            text='I am Ferro. Recoverable external state is the direction I want to test.',
            style_observation_ids=(),rationale='A supported structured explanation is useful.')
        safe=social.XSafetyDecision(safe=True,no_private_data=True,no_impersonation=True,no_harassment=True,
            no_investment_advice=True,no_fabricated_facts=True,attribution_ok=True,
            rationale='The text preserves the supplied evidence and uncertainty.')
        transport.publish_article=AsyncMock(return_value=SimpleNamespace(post_id='2105980723614158848',
            url='https://x.com/i/article/2105980723614158848',handle='ferro',provider='JEV_AGENT_RUNTIME',duplicate_found=False))
        with patch.dict(os.environ,{'CONTENT_X_PUBLICATION_ENABLED':'1'}), patch.object(social,'_structured',AsyncMock(side_effect=[draft,safe])):
            result=asyncio.run(loop._owner_longform(folder=folder,context=context,now=NOW,surface='X_ARTICLE'))
        self.assertEqual(result['status'],'POSTED')
        transport.publish_article.assert_awaited_once()
        self.assertFalse((folder/'publication.json').exists())


if __name__=='__main__':
    unittest.main()
