import sys
import types
import unittest
from unittest.mock import patch
from wechat_traffic_agent.x_jev_social import jev_x_discover, jev_x_reply
from wechat_traffic_agent.x_jev_article import jev_x_article, jev_x_article_reconcile


def execute(browser, script):
    scope={'browser':browser}
    exec(compile(script,'jev-native-social','exec'),scope)
    return scope['result']


class Browser:
    def __init__(self,kind,*,verify=True,parent=True,fill=True,handle='ferro',existing=False):
        self.kind,self.verify,self.parent,self.fill,self.handle,self.existing=kind,verify,parent,fill,handle,existing
        self.url='https://x.com/home';self.submitted=False;self.dialog=False
        self.values={};self.actions=[];self.uploads=[]
        self.inline_dialog=False;self.upload_nodes=[]

    def call(self,method,**params):
        if method=='Page.navigate':self.url=params['url'];return {}
        if method in {'Page.setInterceptFileChooserDialog','Page.bringToFront','Input.dispatchKeyEvent'}:return {}
        if method=='DOM.getDocument':return {'root':{'nodeId':1}}
        if method=='DOM.querySelector':return {'nodeId':55}
        if method=='DOM.querySelectorAll':return {'nodeIds':[55,56] if self.inline_dialog else [55]}
        if method=='DOM.setFileInputFiles':
            self.uploads.append(params['files']);self.upload_nodes.append(params['nodeId']);self.inline_dialog=False;return {}
        raise AssertionError(method)

    def evaluate(self,expression):
        if 'scrollIntoView' in expression:return None
        if '?.matches(' in expression:return 'get(11)' in expression
        if 'Processing media|Cancel upload' in expression:return False
        if expression=='location.href':return self.url
        if 'location.href.includes("/status/")' in expression:return self.url if '/status/' in self.url else None
        if 'e.click(); return true' in expression:
            self.submitted=True;self.dialog=False;self.url='https://x.com/'+self.handle+'/status/987';return True
        if 'ready:document.readyState' in expression:return {'url':self.url,'ready':'complete'}
        if 'AppTabBar_Profile_Link' in expression:return '/'+self.handle
        if 'location.href.includes' in expression:return self.url if '/compose/articles/edit/' in self.url else None
        if 'document.querySelector("main")?.innerText' in expression:
            return 'Durable agents\nThe complete verified article body.' if self.verify and (self.submitted or self.values) else 'Unavailable'
        if 'input[type=file]' in expression:
            return [{'accept':'image/png,image/jpeg','multiple':False,'dialog':False},
                    *([{'accept':'image/png,image/jpeg','multiple':True,'dialog':True}] if self.inline_dialog else [])]
        if '[contenteditable=true] img' in expression:
            return [{'src':'https://pbs.twimg.com/media/inline.png','width':1024,'height':600}] if len(self.uploads)>1 else []
        if ".map(e=>e.src)" in expression and "!e.closest('[contenteditable]')" in expression:
            return []
        if 'naturalWidth>=500' in expression:
            return ['https://pbs.twimg.com/media/cover.png?name=large',*(['https://pbs.twimg.com/media/inline.png'] if len(self.uploads)>1 else [])] if 'map(e=>e.src)' in expression else [{'src':'https://pbs.twimg.com/media/cover.png','width':1500,'height':600},*([{'src':'https://pbs.twimg.com/media/inline.png','width':1024,'height':600}] if len(self.uploads)>1 else [])]
        if 'main img[src*=pbs][src*=media]' in expression:return bool(self.uploads)
        if 'Boolean(document.querySelector("[role=dialog]"))' in expression:return False
        if 'closest("[role=dialog]")' in expression:return self.dialog
        if '?.closest("article")' in expression:return ['/reader/status/123']
        if 'nodes.get' in expression:
            for n,value in self.values.items():
                if 'get('+str(n)+')' in expression:return value if self.fill else 'wrong text'
            return None
        if 'Boolean(document.querySelector("article")' in expression:return True
        if 'Boolean(document.querySelector(\'a[href*="/status/123"]\'))' in expression:return self.parent
        if "document.querySelectorAll('article')" in expression:
            if self.kind=='discover':return [{'text':'Agent state discussion','hrefs':['/reader/status/123']}]
            rows=[{'text':'Public parent post','hrefs':['/reader/status/123']}]
            if (self.submitted and self.verify) or self.existing:
                rows.append({'text':'A grounded reply.','hrefs':['/ferro/status/456']})
            return rows
        raise AssertionError(expression)

    def observe(self,screenshot=False):
        if self.kind=='article':
            actions=[{'kind':'fill','role':'textbox','label':'添加标题','node':10},
                     {'kind':'fill','role':'textbox','label':'textbox','node':11},
                     {'kind':'click','label':'发布','node':12},
                     {'kind':'click','label':'撰写','node':13},
                     {'kind':'click','label':'添加照片或视频','node':14},
                     {'kind':'click','label':'添加媒体内容','node':15},
                     {'kind':'click','role':'menuitem','label':'媒体','node':16},
                     {'kind':'click','role':'link','label':'预览','node':17}]
        elif self.dialog:
            actions=[{'kind':'fill','role':'textbox','label':'帖子文本','node':10},
                     {'kind':'click','label':'回复','node':12}]
        else:actions=[{'kind':'click','label':'1 回复','node':13}]
        return {'actions':actions,'page_key':'live'}

    def act(self,action,page,text=None):
        self.actions.append(action)
        if action['kind']=='fill':self.values[action['node']]=text
        elif action['node'] in {14,15}:pass
        elif action['node']==16:self.inline_dialog=True
        elif action['node']==17:self.url='https://x.com/compose/articles/edit/789/preview'
        elif action['node']==13:
            if self.kind=='article':self.url='https://x.com/compose/articles/edit/789'
            else:self.dialog=True
        elif self.kind=='article':
            if not self.dialog:self.dialog=True
            else:self.submitted=True;self.dialog=False;self.url='https://x.com/'+self.handle+'/status/987'
        else:self.submitted=True;self.dialog=False


class TestJevNativeSocial(unittest.TestCase):
    def setUp(self):
        import time
        clock=[0.]
        self.enterContext(patch.object(time,'monotonic',lambda:clock[0]))
        self.enterContext(patch.object(time,'sleep',lambda s:clock.__setitem__(0,clock[0]+s)))
        module=types.ModuleType('jev_ultrafast.browser');module.StalePage=type('StalePage',(ValueError,),{})
        self.enterContext(patch.dict(sys.modules,{'jev_ultrafast.browser':module}))

    def test_search_query_navigation_and_observed_posts(self):
        browser=Browser('discover')
        result=execute(browser,jev_x_discover('https://x.com/search?q=agent%20state&f=live'))
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual(result['posts'][0]['url'],'https://x.com/reader/status/123')
        self.assertEqual(browser.actions,[])

    def test_reply_requires_own_public_text_and_intended_parent(self):
        browser=Browser('reply')
        result=execute(browser,jev_x_reply('A grounded reply.','https://x.com/reader/status/123','ferro'))
        self.assertEqual(result['status'],'POSTED')
        self.assertEqual(result['url'],'https://x.com/ferro/status/456')
        self.assertEqual(result['target_post_id'],'123')

    def test_reply_with_wrong_parent_is_unverified(self):
        browser=Browser('reply',parent=False)
        result=execute(browser,jev_x_reply('A grounded reply.','https://x.com/reader/status/123','ferro'))
        self.assertEqual(result['status'],'PUBLICATION_UNVERIFIED')

    def test_pending_reply_never_submits_again(self):
        browser=Browser('reply')
        result=execute(browser,jev_x_reply('A grounded reply.','https://x.com/reader/status/123','ferro',reconcile_only=True))
        self.assertEqual(result['status'],'PUBLICATION_UNVERIFIED')
        self.assertEqual(browser.actions,[])

    def test_missing_submit_control_does_not_create_uncertain_publication(self):
        class MissingSubmit(Browser):
            def observe(self,screenshot=False):
                page=super().observe(screenshot)
                if self.dialog:
                    page['actions']=[a for a in page['actions'] if a['kind']=='fill']
                return page
        result=execute(MissingSubmit('reply'),jev_x_reply('A grounded reply.','https://x.com/reader/status/123','ferro'))
        self.assertEqual(result['status'],'FAILED')
        self.assertFalse(result['attempted'])

    def test_existing_reply_reconciles_without_input(self):
        browser=Browser('reply',existing=True)
        result=execute(browser,jev_x_reply('A grounded reply.','https://x.com/reader/status/123','ferro',reconcile_only=True))
        self.assertEqual(result['status'],'POSTED')
        self.assertTrue(result['duplicate_found'])
        self.assertEqual(browser.actions,[])

    def test_article_preparation_uploads_cover_without_publication(self):
        browser=Browser('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro',prepare_only=True))
        self.assertEqual(result['status'],'DRAFT_PREPARED')
        self.assertEqual(browser.uploads,[['/tmp/cover.png']])
        self.assertFalse(browser.submitted)

    def test_empty_article_list_with_create_and_write_uses_create_once(self):
        class EmptyList(Browser):
            def observe(self,screenshot=False):
                page=super().observe(screenshot)
                if self.url=='https://x.com/compose/articles':
                    page['actions'].append({'kind':'click','role':'button','label':'create','node':16})
                return page
            def act(self,action,page,text=None):
                if action['node']==16:
                    self.actions.append(action)
                    self.url='https://x.com/compose/articles/edit/789'
                else:
                    super().act(action,page,text)
        browser=EmptyList('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro',prepare_only=True))
        self.assertEqual(result['status'],'DRAFT_PREPARED')
        self.assertEqual([a['node'] for a in browser.actions if a['node'] in {13,16}],[16])

    def test_article_uploads_native_body_image_and_verifies_it_publicly(self):
        browser=Browser('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro',inline_images=[{'path':'/tmp/body.png'}]))
        self.assertEqual(result['status'],'POSTED')
        self.assertEqual(browser.uploads,[['/tmp/cover.png'],['/tmp/body.png']])
        self.assertEqual(browser.upload_nodes,[55,56])
        self.assertIn('https://pbs.twimg.com/media/inline.png',result['cover'])

    def test_public_article_waits_for_content_after_layout_renders(self):
        class Delayed(Browser):
            reads=0
            def evaluate(self,expression):
                if expression=='document.querySelector("main")?.innerText':
                    self.reads+=1
                    if self.reads<3:return 'X navigation only'
                return super().evaluate(expression)
        browser=Delayed('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro'))
        self.assertEqual(result['status'],'POSTED')
        self.assertGreaterEqual(browser.reads,3)

    def test_recovery_requires_every_persisted_image(self):
        browser=Browser('article');browser.submitted=True
        result=execute(browser,jev_x_article_reconcile(title='Durable agents',text='The complete verified article body.',
            draft_url='https://x.com/compose/articles/edit/789',expected_handle='ferro',public_url='https://x.com/ferro/status/987',cover_source=[
                {'src':'https://pbs.twimg.com/media/cover.png'}, {'src':'https://pbs.twimg.com/media/missing-body.png'}]))
        self.assertEqual(result['status'],'PUBLICATION_UNVERIFIED')
        self.assertEqual(browser.actions,[])

    def test_article_publication_requires_complete_public_content_and_cover(self):
        browser=Browser('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro'))
        self.assertEqual(result['status'],'POSTED')
        self.assertEqual(result['url'],'https://x.com/ferro/status/987')

    def test_article_missing_public_body_is_unverified(self):
        browser=Browser('article',verify=False)
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro'))
        self.assertEqual(result['status'],'PUBLICATION_UNVERIFIED')

    def test_wrong_article_identity_cannot_upload_or_publish(self):
        browser=Browser('article',handle='other')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro'))
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(browser.uploads,[])
        self.assertEqual(browser.actions,[])

    def test_article_reconciles_publication_after_process_restart(self):
        browser=Browser('article');browser.submitted=True
        result=execute(browser,jev_x_article_reconcile(title='Durable agents',text='The complete verified article body.',
            draft_url='https://x.com/compose/articles/edit/789',expected_handle='ferro',public_url='https://x.com/ferro/status/987',
            cover_source=[{'src':'https://pbs.twimg.com/media/cover.png'}]))
        self.assertEqual(result['status'],'POSTED')
        self.assertTrue(result['duplicate_found'])
        self.assertEqual(browser.actions,[])

    def test_occluded_confirmation_activates_only_observed_native_button(self):
        class Occluded(Browser):
            def act(self,action,page,text=None):
                if action['node']==12 and self.dialog:
                    from jev_ultrafast.browser import StalePage
                    raise StalePage('transparent overlay')
                return super().act(action,page,text)
        browser=Occluded('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro'))
        self.assertEqual(result['status'],'POSTED')
        self.assertEqual(result['post_id'],'987')
        self.assertNotEqual(result['post_id'],'789')

    def test_missing_observed_public_url_never_guesses_or_resubmits(self):
        browser=Browser('article');browser.submitted=True
        result=execute(browser,jev_x_article_reconcile(title='Durable agents',text='The complete verified article body.',
            draft_url='https://x.com/compose/articles/edit/789',expected_handle='ferro',
            cover_source=[{'src':'https://pbs.twimg.com/media/cover.png'}]))
        self.assertEqual(result['status'],'PUBLICATION_UNVERIFIED')
        self.assertEqual(browser.actions,[])
        self.assertEqual(browser.url,'https://x.com/home')

    def test_public_verification_waits_for_lazily_loaded_body_asset(self):
        class LazyBody(Browser):
            image_reads=0
            def evaluate(self,expression):
                if 'naturalWidth>=500' in expression and 'map(e=>e.src)' in expression and 'previousCover' not in expression:
                    self.image_reads+=1
                    if self.image_reads<3:return ['https://pbs.twimg.com/media/cover.png']
                    return ['https://pbs.twimg.com/media/cover.png','https://pbs.twimg.com/media/inline.png']
                return super().evaluate(expression)
        browser=LazyBody('article');browser.submitted=True
        result=execute(browser,jev_x_article_reconcile(title='Durable agents',text='The complete verified article body.',
            draft_url='https://x.com/compose/articles/edit/789',expected_handle='ferro',public_url='https://x.com/ferro/status/987',
            cover_source=[{'src':'https://pbs.twimg.com/media/cover.png'},{'src':'https://pbs.twimg.com/media/inline.png'}]))
        self.assertEqual(result['status'],'POSTED')
        self.assertGreaterEqual(browser.image_reads,3)
        self.assertEqual(browser.actions,[])

    def test_article_positions_inline_image_at_the_declared_passage(self):
        class Anchored(Browser):
            positioned=False
            def evaluate(self, expression):
                if "document.createTreeWalker" in expression:
                    self.positioned=True
                    return True
                return super().evaluate(expression)
            def call(self, method, **params):
                if method == "DOM.setFileInputFiles" and params["files"] == ["/tmp/body.png"]:
                    assert self.positioned
                return super().call(method, **params)
        browser=Anchored('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro',prepare_only=True,
            inline_images=[{'path':'/tmp/body.png','after_text':'verified article body.'}]))
        self.assertEqual(result['status'],'DRAFT_PREPARED')
        self.assertTrue(browser.positioned)
        self.assertFalse(browser.submitted)

    def test_missing_image_anchor_stops_before_upload_and_publication(self):
        class MissingAnchor(Browser):
            def evaluate(self, expression):
                if "document.createTreeWalker" in expression:return False
                return super().evaluate(expression)
        browser=MissingAnchor('article')
        result=execute(browser,jev_x_article(title='Durable agents',text='The complete verified article body.',
            image_path='/tmp/cover.png',expected_handle='ferro',
            inline_images=[{'path':'/tmp/body.png','after_text':'missing passage'}]))
        self.assertEqual(result['status'],'FAILED')
        self.assertIn('anchor missing',result['detail'])
        self.assertEqual(browser.uploads,[['/tmp/cover.png']])
        self.assertFalse(browser.submitted)

    def test_article_uses_native_paragraph_blocks_before_positioning_media(self):
        class Paragraphs(Browser):
            enters=0
            def call(self,method,**params):
                if method == 'Input.dispatchKeyEvent':
                    if params['type']=='keyDown':
                        self.enters+=1
                        self.values[11]+='\n'
                    return {}
                if method == 'Input.insertText':
                    self.values[11]+=params['text']
                    return {}
                return super().call(method,**params)
        browser=Paragraphs('article')
        result=execute(browser,jev_x_article(title='Durable agents',
            text='First explain the known observation.\n\nThen develop its implication.\n\nFinally explain its limit.',
            image_path='/tmp/cover.png',expected_handle='ferro',prepare_only=True))
        self.assertEqual(result['status'],'DRAFT_PREPARED')
        self.assertEqual(browser.enters,2)
        self.assertIn('Then develop its implication.',browser.values[11])
