"""Native illustrated X Articles through observed Jev editor actions."""
import json
from .x_jev_social import helpers


def jev_x_article(*, title, text, image_path, expected_handle='', draft_url='', prepare_only=False, checkpoint_path='', inline_images=()):
    values=dict(title=title,outbound=text,image_path=image_path,expected_handle=expected_handle,
                draft_url=draft_url,prepare_only=prepare_only,checkpoint_path=checkpoint_path,inline_images=list(inline_images))
    return '\n'.join(k+' = '+repr(v) for k,v in values.items())+'\n'+helpers()+PUBLIC_TEXT+ARTICLE


def jev_x_article_reconcile(*, title, text, draft_url, cover_source, expected_handle='', public_url=''):
    values=dict(title=title,outbound=text,draft_url=draft_url,cover_source=cover_source,
                expected_handle=expected_handle,public_url=public_url)
    return '\n'.join(k+' = '+repr(v) for k,v in values.items())+'\n'+helpers()+PUBLIC_TEXT+r'''
try:
    article_id=draft_url.rstrip('/').rsplit('/',1)[1]
    if not article_id.isdigit() or '/compose/articles/edit/' not in draft_url:
        raise RuntimeError('Pending Article has no recoverable draft identity')
    public_url,post_id=verified_public_location(public_url,expected_handle)
    navigate(public_url)
    profile=wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    handle=str(profile or '').strip('/')
    if not handle or (expected_handle and handle.casefold()!=expected_handle.casefold()):
        raise RuntimeError('Article reconciliation identity mismatch')
    visible=wait_public_text(title,outbound)
    if norm(title) not in norm(visible) or norm(outbound) not in norm(visible):
        raise RuntimeError('Pending Article is not publicly verified; no repeated submission')
    images=wait_public_images(cover_source)
    if not cover_source or not all(any(media_key(image)==media_key(item['src']) for image in images) for item in cover_source):
        raise RuntimeError('Pending Article images have not been publicly verified')
    result={'status':'POSTED','post_id':post_id,'url':public_url,'handle':handle,
            'draft_url':draft_url,'cover':images,'duplicate_found':True}
except Exception as exc:
    result={'status':'PUBLICATION_UNVERIFIED','attempted':True,'draft_url':draft_url,'detail':str(exc)}
'''


PUBLIC_TEXT=r'''
def verified_public_location(value,handle):
    parts=urlsplit(str(value or ''))
    match=re.fullmatch(r'/([^/]+)/status/(\d+)',parts.path)
    if parts.scheme!='https' or parts.netloc!='x.com' or not match or match[1].casefold()!=handle.casefold():
        raise RuntimeError('Observed public Article URL unavailable; no guessed URL or repeated submission')
    return str(value),match[2]

def media_key(source):
    value=urlsplit(str(source))
    return (value.netloc, re.sub(r'\.(?:png|jpe?g|webp)$','',value.path,flags=re.I))


def wait_public_images(expected):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        # Native Article body images may load only after scrolling into view.
        browser.evaluate("(() => [...document.querySelectorAll('main img')].filter(e=>e.src.startsWith('https://pbs.twimg.com/media/')).forEach(e=>e.scrollIntoView({block:'center'})))()")
        images=browser.evaluate("(() => [...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=500 && e.src.startsWith('https://pbs.twimg.com/media/')).map(e=>e.src))()") or []
        if expected and all(any(media_key(image)==media_key(item['src']) for image in images) for item in expected):
            return images
        time.sleep(.25)
    return []

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
import json

def checkpoint(status,submitted,cover=None,public_url=''):
    if not checkpoint_path:
        return
    import json
    from pathlib import Path
    path=Path(checkpoint_path)
    previous=json.loads(path.read_text()) if path.exists() else {}
    previous.update({'status':status,'attempted':submitted,'draft_url':draft_url})
    if public_url:
        previous['public_url']=public_url
    if cover:
        previous['cover_source']=cover
    pending=path.with_suffix(path.suffix+'.tmp')
    pending.write_text(json.dumps(previous,ensure_ascii=False))
    pending.replace(path)

def observed(labels, kind='click', text=None, preferred=(), selector=None):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        browser.evaluate('(() => {const labels='+json.dumps(sorted(preferred or labels))+';'
          'const selector='+json.dumps(selector)+'; const kind='+json.dumps(kind)+';'
          'const nodes=[...document.querySelectorAll(selector || (kind==="fill"?"textarea,input,[contenteditable=true]":"button,a,[role=button]"))]'
          '.filter(e=>e.getClientRects().length && (selector || labels.includes((e.getAttribute("aria-label") || e.getAttribute("placeholder") || e.innerText || "").trim().toLowerCase())));'
          'if(nodes.length===1) nodes[0].scrollIntoView({block:"center"});})()')
        page=browser.observe(screenshot=False)
        candidates=[a for a in page.get('actions') or [] if a.get('kind')==kind
            and (str(a.get('label') or '').strip().casefold() in labels
                 or (selector and browser.evaluate('Boolean(window.__jevFast.nodes.get('+str(a['node'])+')?.matches('+json.dumps(selector)+'))')))]
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

def fill_body():
    selector='[data-testid="composer"][contenteditable="true"]'
    return observed({'textbox','article body','正文',outbound.casefold()},kind='fill',text=outbound,selector=selector)

def upload_cover():
    previous_cover=browser.evaluate("(() => [...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=500 && !e.closest('[contenteditable]') && e.src.startsWith('https://pbs.twimg.com/media/')).map(e=>e.src))()") or []
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
    cover=wait_value("(() => {const previousCover="+json.dumps(previous_cover)+"; return [...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=500 && !e.closest('[contenteditable]') && e.src.startsWith('https://pbs.twimg.com/media/') && !previousCover.includes(e.src)).map(e=>({src:e.src,width:e.naturalWidth,height:e.naturalHeight}));})()",30)
    if not cover:
        raise RuntimeError('Article uploaded cover not observed')
    return cover


def upload_inline_images(cover):
    if not inline_images:
        return []
    fill_body()
    inserted=[]
    for image in inline_images:
        browser.call('Page.setInterceptFileChooserDialog',enabled=True)
        try:
            observed({'add media','添加媒体内容'})
            observed({'media','媒体','upload media','upload files','upload image','upload images','上传媒体','上传文件','上传图片','上传照片或视频','上传照片和视频'})
            spec=wait_value("(() => [...document.querySelectorAll('input[type=file]')].map(e=>({accept:e.accept,multiple:e.multiple,dialog:!!e.closest('[role=dialog]')})))()",10) or []
            candidates=[index for index,item in enumerate(spec) if item.get('dialog')
                        and any(word in item.get('accept','') for word in ('image/','.png','.jpg'))]
            if len(candidates)!=1:
                raise RuntimeError('Inline image file input unavailable or ambiguous')
            doc=browser.call('DOM.getDocument',depth=-1,pierce=True)
            nodes=browser.call('DOM.querySelectorAll',nodeId=doc['root']['nodeId'],selector='input[type=file]')['nodeIds']
            browser.call('DOM.setFileInputFiles',nodeId=nodes[candidates[0]],files=[image['path']])
        finally:
            browser.call('Page.setInterceptFileChooserDialog',enabled=False)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            previews=browser.evaluate("(() => [...document.querySelectorAll('[contenteditable=true] img')].filter(i=>i.naturalWidth>=500 && (i.src.startsWith('https://pbs.twimg.com/media/') || i.src.startsWith('blob:'))).map(i=>({src:i.src,width:i.naturalWidth,height:i.naturalHeight})))()") or []
            processing=browser.evaluate("(() => {const t=document.querySelector('main')?.innerText || '';return /正在处理媒体|取消上传|Processing media|Cancel upload/i.test(t);})()")
            if len(previews)>len(inserted) and not processing:
                inserted=previews
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Native Article inline image did not upload')
    # Preview exposes durable asset identities; the editor retains blob previews.
    # This is still an unpublished draft.
    observed({'preview','预览'})
    visible=wait_public_text(title,outbound)
    if norm(outbound) not in norm(visible):
        raise RuntimeError('Native Preview body text not verified')
    saved=wait_value("(() => [...document.querySelectorAll('main img')].filter(i=>i.naturalWidth>=500 && i.src.startsWith('https://pbs.twimg.com/media/')).map(i=>({src:i.src,width:i.naturalWidth,height:i.naturalHeight})))()",30) or []
    if not all(any(media_key(item['src'])==media_key(image['src']) for image in saved) for item in cover):
        raise RuntimeError('Native Preview cover differs from uploaded cover')
    inline=[image for image in saved if not any(media_key(image['src'])==media_key(item['src']) for item in cover)]
    if len(inline)!=len(inline_images):
        raise RuntimeError('Native Preview body images not verified')
    navigate(draft_url)
    return inline

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
    body_editor=fill_body()
    for editor,expected in [(title_editor,title),(body_editor,outbound)]:
        value=browser.evaluate('(() => {const e=window.__jevFast.nodes.get('+str(editor['node'])+');return e?.value || e?.innerText || "";})()')
        if norm(value)!=norm(expected):
            raise RuntimeError('Native Article fill mismatch')
    stage='article_cover'
    cover=upload_cover()
    inline=upload_inline_images(cover)
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
                stage='article_final_confirm'
                checkpoint('FINAL_CONFIRM_PENDING',True,images_to_verify)
                browser.call('Page.bringToFront')
                try:
                    browser.act(confirm[0],page)
                except StalePage:
                    # Jev's mouse hit-test can encounter X's transparent modal overlay.
                    # Activate only this freshly observed enabled native dialog button.
                    clicked=browser.evaluate('(() => {const e=window.__jevFast.nodes.get('+str(confirm[0]['node'])+');if(!e || !e.isConnected || e.tagName!=="BUTTON" || !e.closest("[role=dialog]") || !e.getClientRects().length || e.disabled || e.getAttribute("aria-disabled")==="true" || !["publish","发布"].includes((e.getAttribute("aria-label") || e.innerText || "").trim().toLowerCase()))return false; e.click(); return true;})()')
                    if clicked is not True:
                        raise RuntimeError('Fresh native Article confirmation button unavailable')
                break
            if '/compose/articles/edit/' not in str(browser.evaluate('location.href') or ''):
                break
        stage='article_verify'
        public_url=wait_value('location.href.includes("/status/") && location.href',30)
        public_url,post_id=verified_public_location(public_url,handle)
        checkpoint('PUBLICATION_UNVERIFIED',True,images_to_verify,public_url)
        navigate(public_url)
        # Independently reopen the observed public URL and verify the full content.
        visible=wait_public_text(title,outbound)
        if norm(title) not in norm(visible) or norm(outbound) not in norm(visible):
            raise RuntimeError('Public Article title/full body not verified')
        images=wait_public_images(images_to_verify)
        if not images:
            raise RuntimeError('Public Article image not verified')
        if not all(any(media_key(image)==media_key(item['src']) for image in images) for item in images_to_verify):
            raise RuntimeError('Public Article images differ from uploaded cover/body images')
        result={'status':'POSTED','post_id':post_id,'url':public_url,'handle':handle,
                'draft_url':draft_url,'cover':images,'duplicate_found':False}
except Exception as exc:
    result={'status':'PUBLICATION_UNVERIFIED' if attempted else 'FAILED','stage':stage,
            'attempted':attempted,'detail':str(exc),'draft_url':draft_url}
'''
