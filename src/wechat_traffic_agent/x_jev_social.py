"""Observed X discovery and verified replies on the Runtime-owned Jev tab."""
import json
from .x_jev_transaction import TRANSACTION


def helpers():
    return TRANSACTION[:TRANSACTION.index("\ntry:\n    profile =")]


def jev_x_discover(url, limit=20):
    return 'destination = ' + json.dumps(url) + '\nlimit = ' + str(max(1, min(limit, 50))) + '\n' + helpers() + r'''
try:
    navigate(destination)
    handle = str(wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")') or '').strip('/')
    if not handle:
        raise RuntimeError('X is not authenticated')
    rendered = wait_value('Boolean(document.querySelector("article") || document.querySelector("[data-testid=emptyState]"))', 12)
    if not rendered:
        raise RuntimeError('X discovery timeline/empty state did not render')
    rows = browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('article')].map(a=>({
      text:a.querySelector('[data-testid="tweetText"]')?.innerText || '',
      hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(e=>e.getAttribute('href')||'')
    })))()''' + "'''" + r''') or []
    posts = []
    seen = set()
    for row in rows:
        if not norm(row.get('text')):
            continue
        for href in row.get('hrefs') or []:
            m = re.match(r'^/([^/?#]+)/status/(\d+)(?:[/?#]|$)', href)
            if m and m.group(1).casefold() != handle.casefold() and m.group(2) not in seen:
                seen.add(m.group(2))
                posts.append({'id':m.group(2),'username':m.group(1),'text':row['text'],
                              'url':'https://x.com/'+m.group(1)+'/status/'+m.group(2)})
                break
    result = {'status':'OBSERVED','posts':posts[:limit],'url':destination}
except Exception as exc:
    result = {'status':'FAILED','detail':str(exc),'error_type':type(exc).__name__}
'''


def jev_x_reply(text, target_url, expected_handle='', reconcile_only=False):
    return ('outbound = ' + json.dumps(text, ensure_ascii=False) + '\ntarget_url = ' + json.dumps(target_url)
            + '\nexpected_handle = ' + json.dumps(expected_handle) + '\nreconcile_only = ' + repr(reconcile_only) + '\n' + helpers() + r'''
def reply_action(kind, submit=False):
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        page = browser.observe(screenshot=False)
        candidates=[]
        for a in page.get('actions') or []:
            if a.get('kind') != kind:
                continue
            label=str(a.get('label') or '').strip().casefold()
            node=int(a['node'])
            if kind == 'fill':
                if a.get('role')=='textbox' and not any(w in label for w in ('search','搜索')):
                    candidates.append(a)
            elif submit:
                if label in {'reply','回复'} and browser.evaluate('Boolean(window.__jevFast.nodes.get('+str(node)+')?.closest("[role=dialog]"))'):
                    candidates.append(a)
            elif ('reply' in label or '回复' in label):
                parent = browser.evaluate('(() => {const e=window.__jevFast.nodes.get('+str(node)+');return [...(e?.closest("article")?.querySelectorAll("a[href]")||[])].map(a=>a.getAttribute("href"));})()') or []
                if any('/status/'+target_id in h for h in parent):
                    candidates.append(a)
        if len(candidates)==1:
            try:
                browser.act(candidates[0],page,text=outbound if kind=='fill' else None)
                return candidates[0]
            except StalePage:
                continue
        time.sleep(.15)
    raise RuntimeError('Jev reply control unavailable or ambiguous')

def find_reply(handle):
    navigate(target_url)
    wait_value('Boolean(document.querySelector("article"))',12)
    rows=browser.evaluate(''' + "'''" + r'''(() => [...document.querySelectorAll('article')].map(a=>({text:a.querySelector('[data-testid="tweetText"]')?.innerText||'',hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(e=>e.getAttribute('href'))})))()''' + "'''" + r''') or []
    for row in rows:
        if norm(row.get('text'))==norm(outbound):
            for href in row.get('hrefs') or []:
                m=re.match(r'^/([^/?#]+)/status/(\d+)',href)
                if m and m.group(1).casefold()==handle.casefold() and m.group(2)!=target_id:
                    # Verify the reply's own permalink also shows the intended parent.
                    navigate('https://x.com/'+handle+'/status/'+m.group(2))
                    if wait_value('Boolean(document.querySelector(\'a[href*="/status/'+target_id+'"]\'))',12):
                        return m.group(2)
    return ''
try:
    if not re.fullmatch(r'https://x\.com/[^/?#]+/status/\d+',target_url):
        raise ValueError('Invalid reply target URL')
    target_id=target_url.rsplit('/',1)[1]
    navigate(target_url)
    profile=wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    handle=str(profile or '').strip('/')
    if not handle or (expected_handle and handle.casefold()!=expected_handle.casefold()):
        raise RuntimeError('Reply identity mismatch')
    post_id=find_reply(handle)
    duplicate=bool(post_id)
    if not post_id:
        if reconcile_only:
            attempted=True
            raise RuntimeError('Pending reply not publicly reconciled; no repeated submission')
        navigate(target_url)
        reply_action('click')
        editor=reply_action('fill')
        rendered=browser.evaluate('window.__jevFast.nodes.get('+str(editor['node'])+')?.innerText')
        if norm(rendered)!=norm(outbound):
            raise RuntimeError('Reply fill mismatch')
        attempted=True
        reply_action('click',submit=True)
        for _ in range(4):
            time.sleep(.8)
            post_id=find_reply(handle)
            if post_id:
                break
        if not post_id:
            raise RuntimeError('Reply public URL/parent relationship not verified')
    result={'status':'POSTED','post_id':post_id,'url':'https://x.com/'+handle+'/status/'+post_id,
            'handle':handle,'duplicate_found':duplicate,'target_post_id':target_id}
except Exception as exc:
    result={'status':'PUBLICATION_UNVERIFIED' if attempted else 'FAILED','detail':str(exc),'attempted':attempted}
''')
