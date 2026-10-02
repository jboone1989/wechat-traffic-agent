"""Native illustrated X Articles through observed Jev editor actions."""
import json
from .x_jev_social import helpers


def jev_x_article(*, title, text, image_path, expected_handle='', draft_url='', prepare_only=False, checkpoint_path='', inline_images=()):
    values=dict(title=title,outbound=text,image_path=image_path,expected_handle=expected_handle,
                draft_url=draft_url,prepare_only=prepare_only,checkpoint_path=checkpoint_path,inline_images=list(inline_images))
    return '\n'.join(k+' = '+repr(v) for k,v in values.items())+'\n'+helpers()+PUBLIC_TEXT+ARTICLE


def jev_x_article_reconcile(*, title, text, draft_url, cover_source, expected_handle=''):
    values=dict(title=title,outbound=text,draft_url=draft_url,cover_source=cover_source,
                expected_handle=expected_handle)
    return '\n'.join(k+' = '+repr(v) for k,v in values.items())+'\n'+helpers()+PUBLIC_TEXT+r'''
try:
    article_id=draft_url.rstrip('/').rsplit('/',1)[1]
    if not article_id.isdigit() or '/compose/articles/edit/' not in draft_url:
        raise RuntimeError('Pending Article has no recoverable draft identity')
    public_url='https://x.com/i/article/'+article_id
    navigate(public_url)
    profile=wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    handle=str(profile or '').strip('/')
    if not handle or (expected_handle and handle.casefold()!=expected_handle.casefold()):
        raise RuntimeError('Article reconciliation identity mismatch')
    visible=wait_public_text(title,outbound)
    if norm(title) not in norm(visible) or norm(outbound) not in norm(visible):
        raise RuntimeError('Pending Article is not publicly verified; no repeated submission')
    images=browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=500 && !e.src.includes('profile_images')).map(e=>e.src))()''' + "'''" + r''') or []
    if not cover_source or not all(any(image.split('?',1)[0]==item['src'].split('?',1)[0] for image in images) for item in cover_source):
        raise RuntimeError('Pending Article images have not been publicly verified')
    result={'status':'POSTED','post_id':article_id,'url':public_url,'handle':handle,
            'draft_url':draft_url,'cover':images,'duplicate_found':True}
except Exception as exc:
    result={'status':'PUBLICATION_UNVERIFIED','attempted':True,'draft_url':draft_url,'detail':str(exc)}
'''


PUBLIC_TEXT=r'''
def wait_public_text(title,body):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        visible=browser.evaluate('document.querySelector("main")?.innerText') or ''
        if norm(title) in norm(visible) and norm(body) in norm(visible):
            return visible
        time.sleep(.25)
    return ''

'''

ARTICLE=r'''

def checkpoint(status,submitted,cover=None):
    if not checkpoint_path:
        return
    import json
    from pathlib import Path
    path=Path(checkpoint_path)
    previous=json.loads(path.read_text()) if path.exists() else {}
    previous.update({'status':status,'attempted':submitted,'draft_url':draft_url})
    if cover:
        previous['cover_source']=cover
    pending=path.with_suffix(path.suffix+'.tmp')
    pending.write_text(json.dumps(previous,ensure_ascii=False))
    pending.replace(path)

def observed(labels, kind='click', text=None, preferred=()):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        page=browser.observe(screenshot=False)
        candidates=[a for a in page.get('actions') or [] if a.get('kind')==kind
            and str(a.get('label') or '').strip().casefold() in labels]
        preferred_candidates=[a for a in candidates
            if str(a.get('label') or '').strip().casefold() in preferred]
        if preferred_candidates:
            candidates=preferred_candidates
        if len(candidates)==1:
            try:
                browser.act(candidates[0],page,text=text)
                return candidates[0]
            except StalePage:
                continue
        time.sleep(.15)
    raise RuntimeError('Article control missing or ambiguous: '+str(labels))

def upload_cover():
    browser.call('Page.setInterceptFileChooserDialog',enabled=True)
    try:
        observed({'add photos or video','添加照片或视频'})
        # Observe the native image input before CDP's explicit file-upload command.
        spec=browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('input[type=file]')].map(e=>({accept:e.accept,multiple:e.multiple})))()''' + "'''" + r''') or []
        if len(spec)!=1 or 'image/png' not in spec[0].get('accept','') or spec[0].get('multiple'):
            raise RuntimeError('Article image upload input is unavailable/ambiguous')
        document=browser.call('DOM.getDocument',depth=-1,pierce=True)
        node=browser.call('DOM.querySelector',nodeId=document['root']['nodeId'],selector='input[type=file]')['nodeId']
        if not node:
            raise RuntimeError('Article image input disappeared')
        browser.call('DOM.setFileInputFiles',nodeId=node,files=[image_path])
    finally:
        browser.call('Page.setInterceptFileChooserDialog',enabled=False)
    # X may open a native crop dialog. Never click an unrelated save action.
    time.sleep(1.0)
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        page=browser.observe(screenshot=False)
        candidates=[a for a in page.get('actions') or [] if a.get('kind')=='click'
                    and str(a.get('label') or '').strip().casefold() in {'apply','应用','save','保存'}]
        candidates=[a for a in candidates if browser.evaluate('Boolean(window.__jevFast.nodes.get('+str(a['node'])+')?.closest("[role=dialog]"))')]
        if len(candidates)==1:
            try:
                browser.act(candidates[0],page)
                break
            except StalePage:
                continue
        # A blob preview is not a completed upload. Wait for a crop control or a hosted image.
        if browser.evaluate('Boolean(document.querySelector("main img[src*=pbs][src*=media]"))') and not browser.evaluate('Boolean(document.querySelector("[role=dialog]"))'):
            break
        time.sleep(.2)
    # Profile avatars cannot establish cover-upload success.
    cover=wait_value(''' + "'''" + r'''(() => [...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=500 && e.src.startsWith('https://pbs.twimg.com/media/')).map(e=>({src:e.src,width:e.naturalWidth,height:e.naturalHeight})))()''' + "'''" + r''',25)
    if not cover:
        raise RuntimeError('Article uploaded cover not observed')
    return cover

def upload_inline_images():
    if not inline_images:
        return []
    # Refill once before inserting media to restore the body selection at its end.
    # No text fill is performed after an image has been inserted.
    observed({'textbox','article body','正文',outbound.casefold()},kind='fill',text=outbound)
    inserted=[]
    for image in inline_images:
        browser.call('Page.setInterceptFileChooserDialog',enabled=True)
        try:
            observed({'add media','添加媒体内容'})
            spec=browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('input[type=file]')].map(e=>({accept:e.accept,multiple:e.multiple})))()''' + "'''" + r''') or []
            if not spec:
                observed({'upload media','upload files','upload image','upload images','上传媒体','上传文件','上传图片','上传照片或视频','上传照片和视频'})
                spec=wait_value(''' + "'''" + r'''(() => [...document.querySelectorAll('input[type=file]')].map(e=>({accept:e.accept,multiple:e.multiple})))()''' + "'''" + r''',10) or []
            candidates=[index for index,item in enumerate(spec) if any(word in item.get('accept','') for word in ('image/','.png','.jpg'))]
            if len(candidates)!=1:
                raise RuntimeError('Inline image file input unavailable or ambiguous')
            doc=browser.call('DOM.getDocument',depth=-1,pierce=True)
            nodes=browser.call('DOM.querySelectorAll',nodeId=doc['root']['nodeId'],selector='input[type=file]')['nodeIds']
            browser.call('DOM.setFileInputFiles',nodeId=nodes[candidates[0]],files=[image['path']])
        finally:
            browser.call('Page.setInterceptFileChooserDialog',enabled=False)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            hosted=browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('[contenteditable=true] img')].filter(i=>i.src.startsWith('https://pbs.twimg.com/media/')).map(i=>({src:i.src,width:i.naturalWidth,height:i.naturalHeight})))()''' + "'''" + r''') or []
            if len(hosted)>len(inserted):
                inserted=hosted
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Native Article inline image did not upload')
    return inserted

try:
    navigate(draft_url or 'https://x.com/compose/articles')
    profile=wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    handle=str(profile or '').strip('/')
    if not handle or (expected_handle and handle.casefold()!=expected_handle.casefold()):
        raise RuntimeError('Article identity mismatch')
    if not draft_url:
        observed({'write','撰写','create'},preferred={'create'})
    draft_url=wait_value('location.href.includes("/compose/articles/edit/") && location.href')
    if not draft_url:
        raise RuntimeError('Native Article editor URL not observed')
    article_id=draft_url.rstrip('/').rsplit('/',1)[1]
    if not article_id.isdigit():
        raise RuntimeError('Article ID invalid')
    checkpoint('DRAFT_EDITING',False)
    stage='article_title'
    title_editor=observed({'add a title','添加标题',title.casefold()},kind='fill',text=title)
    body_editor=observed({'textbox','article body','正文',outbound.casefold()},kind='fill',text=outbound)
    for editor,expected in [(title_editor,title),(body_editor,outbound)]:
        value=browser.evaluate('(() => {const e=window.__jevFast.nodes.get('+str(editor['node'])+');return e?.value || e?.innerText || "";})()')
        if norm(value)!=norm(expected):
            raise RuntimeError('Native Article fill mismatch')
    stage='article_cover'
    cover=upload_cover()
    inline=upload_inline_images()
    images_to_verify=cover+inline
    if prepare_only:
        result={'status':'DRAFT_PREPARED','draft_url':draft_url,'article_id':article_id,
                'handle':handle,'cover':cover,'inline':inline,'publication_authority':False}
    else:
        stage='article_publish'
        attempted=True
        checkpoint('SUBMIT_PENDING',True,images_to_verify)
        observed({'publish','发布'})
        # Publishing may require a confirmation dialog; use only its observed publish action.
        confirmation_deadline=time.monotonic()+15
        while time.monotonic()<confirmation_deadline:
            time.sleep(.25)
            page=browser.observe(screenshot=False)
            confirm=[a for a in page.get('actions') or [] if a.get('kind')=='click'
                     and str(a.get('label') or '').strip().casefold() in {'publish','发布'}
                     and browser.evaluate('Boolean(window.__jevFast.nodes.get('+str(a['node'])+')?.closest("[role=dialog]"))')]
            if len(confirm)==1:
                try:
                    browser.act(confirm[0],page)
                except StalePage:
                    continue
                break
            if '/compose/articles/edit/' not in str(browser.evaluate('location.href') or ''):
                break
        stage='article_verify'
        public_url='https://x.com/i/article/'+article_id
        navigate(public_url)
        # A guessed URL or editor success toast is not publication evidence.
        visible=wait_public_text(title,outbound)
        if norm(title) not in norm(visible) or norm(outbound) not in norm(visible):
            raise RuntimeError('Public Article title/full body not verified')
        images=browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=500 && !e.src.includes('profile_images')).map(e=>e.src))()''' + "'''" + r''') or []
        if not images:
            raise RuntimeError('Public Article image not verified')
        if not all(any(image.split('?',1)[0] == item['src'].split('?',1)[0] for image in images) for item in images_to_verify):
            raise RuntimeError('Public Article images differ from uploaded cover/body images')
        result={'status':'POSTED','post_id':article_id,'url':public_url,'handle':handle,
                'draft_url':draft_url,'cover':images,'duplicate_found':False}
except Exception as exc:
    result={'status':'PUBLICATION_UNVERIFIED' if attempted else 'FAILED','stage':stage,
            'attempted':attempted,'detail':str(exc),'draft_url':draft_url}
'''
