import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
from wechat_traffic_agent.x_browser import XBrowserPublicationUnverified


class TransactionTests(unittest.TestCase):
    def test_failed_article_verification_preserves_upload_checkpoint_for_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            image=folder/'cover.png';image.write_bytes(b'bound image bytes')
            transaction=folder/'article.json'
            class Transport(LocalHarnessXTransport):
                def __init__(self):self.state_root=folder;self.expected_handle='ferro';self.calls=0
                def _run_jev_sync(self,script):
                    self.calls+=1
                    if self.calls==1:
                        checkpoint=json.loads(transaction.read_text())
                        checkpoint.update({'attempted':True,'status':'SUBMIT_PENDING',
                            'draft_url':'https://x.com/compose/articles/edit/123',
                            'cover_source':[{'src':'https://pbs.twimg.com/media/exact-cover.png'}]})
                        transaction.write_text(json.dumps(checkpoint))
                        return {'status':'PUBLICATION_UNVERIFIED','attempted':True,'detail':'Public page still loading'}
                    self.assert_recovery='Pending Article is not publicly verified; no repeated submission' in script
                    return {'status':'POSTED','post_id':'123','url':'https://x.com/i/article/123',
                            'handle':'ferro','duplicate_found':True}
            transport=Transport()
            kwargs={'title':'Durable state','text':'An evidence grounded article.',
                    'image_path':str(image),'transaction_path':transaction}
            with self.assertRaises(XBrowserPublicationUnverified):
                asyncio.run(transport.publish_article(**kwargs))
            saved=json.loads(transaction.read_text())
            self.assertEqual(saved['cover_source'],[{'src':'https://pbs.twimg.com/media/exact-cover.png'}])
            result=asyncio.run(transport.publish_article(**kwargs))
            self.assertTrue(transport.assert_recovery)
            self.assertEqual(result.url,'https://x.com/i/article/123')
            asyncio.run(transport.publish_article(**kwargs))
            self.assertEqual(transport.calls,2)

    def test_crash_before_reply_submission_is_retryable(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            class Transport(LocalHarnessXTransport):
                def __init__(self):self.state_root=folder;self.expected_handle='ferro';self.calls=0
                def _run_jev_sync(self,script):
                    self.calls+=1
                    if self.calls==1:raise RuntimeError('Browser stopped before input')
                    self.retry_allowed='reconcile_only = False' in script
                    return {'status':'POSTED','post_id':'456','url':'https://x.com/ferro/status/456',
                            'handle':'ferro','duplicate_found':False}
            transport=Transport()
            kwargs={'text':'A useful bounded reply.','target_url':'https://x.com/reader/status/123'}
            with self.assertRaises(RuntimeError):asyncio.run(transport.reply(**kwargs))
            transaction=next((folder/'content/x/reply-transactions').glob('*.json'))
            self.assertEqual(json.loads(transaction.read_text())['status'],'PREPARING')
            asyncio.run(transport.reply(**kwargs))
            self.assertTrue(transport.retry_allowed)
            self.assertEqual(transport.calls,2)
