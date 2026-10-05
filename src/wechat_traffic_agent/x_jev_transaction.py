"""A Jev observation/action transaction on an Agent Runtime-owned X tab."""
import json

def jev_x_transaction(text: str, expected_handle: str = "", checkpoint_path: str = "") -> str:
    return ("outbound = " + json.dumps(text, ensure_ascii=False)
            + "\nexpected_handle = " + json.dumps(expected_handle)
            + "\ncheckpoint_path = " + json.dumps(checkpoint_path) + "\n" + TRANSACTION)

def jev_x_identity(expected_handle: str = "") -> str:
    helpers = TRANSACTION[:TRANSACTION.index("\ntry:\n    profile =")]
    return "expected_handle = " + json.dumps(expected_handle) + "\n" + helpers + r'''
try:
    profile = wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    if not profile:
        raise RuntimeError('X profile is not authenticated')
    handle = str(profile).strip('/').split('/',1)[0]
    if not handle or handle in {'home','i'} or (expected_handle and handle.casefold() != expected_handle.casefold()):
        raise RuntimeError('X authenticated identity unavailable' if not handle else 'X authenticated identity @' + handle + ' does not match expected @' + expected_handle)
    navigate('https://x.com/' + handle)
    label = wait_value("(() => { const a=[...document.querySelectorAll('a[href]')].find(e=>e.getAttribute('href')===location.pathname+'/verified_followers'||e.getAttribute('href')===location.pathname+'/followers'); return a?.getAttribute('aria-label')||a?.innerText||''; })()")
    if not label:
        raise RuntimeError('X profile follower count is not observable')
    match = re.search(r'([\d][\d,.]*)\s*([KMB万亿]?)', str(label), re.I)
    if not match:
        raise RuntimeError('X follower count display could not be parsed: ' + str(label))
    scale = {'':1, 'K':1000, 'M':1000000, 'B':1000000000, '万':10000, '亿':100000000}
    count = int(float(match.group(1).replace(',','')) * scale[match.group(2).upper()])
    result = {'status':'IDENTITY_OBSERVED', 'user':{'username':handle,
              'public_metrics':{'followers_count':count},
              'followers_display':str(label), 'followers_precision':'display'}}
except Exception as exc:
    result = {'status':'FAILED','stage':'identity','detail':str(exc),'error_type':type(exc).__name__}
'''

TRANSACTION = r"""
import json, re, time
from urllib.parse import urlsplit, parse_qs
from jev_ultrafast.browser import StalePage
stage = 'identity'
attempted = False
action_count = 0
def norm(value):
    return ' '.join(str(value or '').split())

def wait_value(expression, seconds=15.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = browser.evaluate(expression)
        except StalePage:
            value = None
        if value:
            return value
        time.sleep(0.15)
    return None

def navigate(url):
    deadline = time.monotonic() + 25.0
    try:
        response = browser.call('Page.navigate', url=url, _response_timeout=20.0)
        if response.get('errorText'):
            raise RuntimeError('X navigation failed: ' + str(response['errorText']))
    except TimeoutError:
        # Reconcile a late acknowledgement by observing the destination.
        pass
    while time.monotonic() < deadline:
        try:
            state = browser.evaluate('({url:location.href, ready:document.readyState})') or {}
        except StalePage:
            state = {}
        actual=urlsplit(str(state.get('url') or ''))
        wanted=urlsplit(url)
        same=(actual.netloc==wanted.netloc and actual.path.rstrip('/')==wanted.path.rstrip('/')
              and all(parse_qs(actual.query).get(k)==v for k,v in parse_qs(wanted.query).items()))
        if same and state.get('ready') == 'complete':
            return
        time.sleep(0.15)
    raise RuntimeError('X navigation did not reach ' + url)

def find_post(handle):
    navigate('https://x.com/' + handle)
    if not wait_value('Boolean(document.querySelector("article"))', 12.0):
        raise RuntimeError('X profile timeline did not become observable')
    rows = browser.evaluate('''(() => [...document.querySelectorAll('article')].slice(0,20).map(a=>({
      text:a.querySelector('[data-testid="tweetText"]')?.innerText || '',
      hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(e=>e.getAttribute('href')||'')
    })))()''') or []
    expected = norm(outbound)
    for row in rows:
        observed = norm(row.get('text'))
        exact = observed == expected
        folded = len(observed) >= 40 and expected.startswith(observed)
        if not exact and not folded:
            continue
        for href in row.get('hrefs') or []:
            match = re.match(r'^/([^/?#]+)/status/(\d+)(?:[/?#]|$)', str(href))
            if not match or match.group(1).casefold() != handle.casefold():
                continue
            if exact:
                return match.group(2)
            # Timeline text can be folded. Follow its observed status link and
            # verify the complete text before accepting this publication.
            path = '/' + match.group(1) + '/status/' + match.group(2)
            navigate('https://x.com' + path)
            expression = ('(() => {const path=' + json.dumps(path) + ';'
                'const expected=' + json.dumps(expected, ensure_ascii=False) + ';'
                'const a=[...document.querySelectorAll("article")].find(a=>'
                '[...a.querySelectorAll("a[href]")].some(e=>e.getAttribute("href")===path));'
                'const text=a?.querySelector("[data-testid=tweetText]")?.innerText||"";'
                'return text.replace(/\\s+/g," ").trim()===expected ? text : null;})()')
            if norm(wait_value(expression)) == expected:
                return match.group(2)
    return ''

def observe_action(kind):
    deadline = time.monotonic() + 20.0
    labels = []
    while time.monotonic() < deadline:
        page = browser.observe(screenshot=False)
        actions = page.get('actions') or []
        labels = [str(a.get('label') or '') for a in actions if a.get('kind') == kind]
        if kind == 'fill':
            candidates = [a for a in actions if a.get('kind') == 'fill' and a.get('role') == 'textbox'
                          and any(word in str(a.get('label') or '').casefold()
                                  for word in ('post text','tweet text','帖子文本','推文文本'))]
            if not candidates:
                candidates = [a for a in actions if a.get('kind') == 'fill' and a.get('role') == 'textbox'
                              and not any(word in str(a.get('label') or '').casefold()
                                          for word in ('search','搜索'))]
        else:
            candidates = [a for a in actions if a.get('kind') == 'click'
                          and str(a.get('label') or '').strip().casefold() in {'post','发帖','发布','tweet'}]
        if len(candidates) == 1:
            return page, candidates[0]
        if len(candidates) > 1:
            raise RuntimeError('Jev observed ambiguous ' + kind + ' actions: ' + str(labels))
        time.sleep(0.15)
    raise RuntimeError('Jev did not observe X ' + kind + ' control; labels=' + str(labels))

def act_observed(kind, text=None):
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        page, action = observe_action(kind)
        try:
            browser.act(action, page, text=text)
            return action
        except StalePage:
            # Jev rejects stale observations before dispatching input.
            time.sleep(0.15)
    raise RuntimeError('Jev X ' + kind + ' observation did not remain fresh')

try:
    profile = wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    if not profile:
        raise RuntimeError('X profile is not authenticated')
    handle = str(profile).strip('/').split('/',1)[0]
    if not handle or handle in {'home','i'}:
        raise RuntimeError('X profile handle is unavailable')
    if expected_handle and handle.casefold() != expected_handle.casefold():
        raise RuntimeError('X profile is @' + handle + ', expected @' + expected_handle)
    stage = 'reconcile'
    post_id = find_post(handle)
    duplicate = bool(post_id)
    if not post_id:
        stage = 'composer'
        navigate('https://x.com/compose/post')
        stage = 'fill'
        editor = act_observed('fill', text=outbound)
        action_count += 1
        expression = '(() => {const e=window.__jevFast?.nodes.get(' + str(editor['node']) + '); return e?.innerText || e?.value || "";})()'
        deadline = time.monotonic() + 5.0
        rendered = ''
        while time.monotonic() < deadline:
            rendered = str(browser.evaluate(expression) or '')
            if norm(rendered) == norm(outbound):
                break
            time.sleep(0.15)
        if norm(rendered) != norm(outbound):
            raise RuntimeError('Jev fill mismatch: expected_chars=' + str(len(norm(outbound))) + ' actual_chars=' + str(len(norm(rendered))))
        stage = 'submit'
        attempted = True
        checkpoint = globals().get('checkpoint_path')
        if checkpoint:
            import json, os, tempfile
            from pathlib import Path
            path = Path(checkpoint)
            previous = json.loads(path.read_text())
            previous.update(status='SUBMIT_PENDING', attempted=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
                os.chmod(f.name, 0o600)
                json.dump(previous, f)
                f.flush()
                os.fsync(f.fileno())
                tmp = f.name
            os.replace(tmp, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        act_observed('click')
        action_count += 1
        stage = 'verify'
        time.sleep(1.0)
        for _ in range(4):
            post_id = find_post(handle)
            if post_id:
                break
            time.sleep(0.7)
        if not post_id:
            raise RuntimeError('Jev submit has no verified public status URL')
    result = {'status':'POSTED','post_id':post_id,'url':f'https://x.com/{handle}/status/{post_id}',
              'handle':handle,'duplicate_found':duplicate,'action_count':action_count,'executor':'JEV_AGENT_RUNTIME'}
except Exception as exc:
    result = {'status':'PUBLICATION_UNVERIFIED' if attempted else 'FAILED', 'stage':stage,
              'attempted':attempted, 'action_count':action_count,
              'error_type':type(exc).__name__, 'detail':str(exc)}
"""

def jev_x_reconcile(text: str, expected_handle: str = "") -> str:
    """Resolve an uncertain submit by observation without composing or submitting."""
    helpers = TRANSACTION[:TRANSACTION.index("\ntry:\n    profile =")]
    return "outbound = " + json.dumps(text, ensure_ascii=False) + "\nexpected_handle = " + json.dumps(expected_handle) + "\n" + helpers + r'''
try:
    profile = wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
    handle = str(profile or '').strip('/').split('/',1)[0]
    if not handle or handle in {'home','i'} or (expected_handle and handle.casefold() != expected_handle.casefold()):
        raise RuntimeError('X authenticated identity unavailable' if not handle else 'X authenticated identity @' + handle + ' does not match expected @' + expected_handle)
    post_id = find_post(handle)
    if not post_id:
        raise RuntimeError('No matching public status was observed after uncertain submit')
    result = {'status':'POSTED','post_id':post_id,'url':f'https://x.com/{handle}/status/{post_id}',
              'handle':handle,'duplicate_found':True,'action_count':0,'executor':'JEV_AGENT_RUNTIME',
              'verification_only':True}
except Exception as exc:
    result = {'status':'PUBLICATION_UNVERIFIED','stage':'reconcile','attempted':True,
              'action_count':0,'verification_only':True,'error_type':type(exc).__name__,'detail':str(exc)}
'''
