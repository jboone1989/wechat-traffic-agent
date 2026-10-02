"""Durable image generation on Ferro's authenticated ChatGPT browser profile."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path


class ChatGPTImageGenerator:
    def __init__(self, provider=None, *, timeout_seconds=420):
        if provider is None:
            from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider
            provider = ChatGPTBrowserProvider.from_environment()
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.session_id = "ferro-image-generation"

    async def generate(self, *, prompt: str, folder: Path) -> dict:
        if not prompt.strip():
            raise ValueError("Image generation needs a prompt")
        return await asyncio.to_thread(self._generate_sync, prompt, Path(folder))

    def _generate_sync(self, prompt: str, folder: Path) -> dict:
        from agent_runtime.llm.chatgpt_browser import _BrowserProfileProcessLock
        from agent_runtime.integrations.browser import BROWSER_NAVIGATE
        folder.mkdir(parents=True, exist_ok=True)
        transaction = folder / "generation_transaction.json"
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        prior = json.loads(transaction.read_text()) if transaction.exists() else {}
        if prior and prior.get("prompt_sha256") != digest:
            raise ValueError("Image transaction prompt changed")
        if prior.get("status") == "GENERATED":
            image = Path(prior["path"])
            if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != prior["sha256"]:
                raise ValueError("Generated image no longer matches its receipt")
            return prior
        spec = importlib.util.find_spec("jev_ultrafast")
        if spec is None or spec.origin is None:
            raise RuntimeError("Image generation requires Jev")
        with _BrowserProfileProcessLock(self.provider.process_lock_path,
                                       timeout_seconds=self.timeout_seconds + 45):
            if prior.get("conversation_url"):
                self.provider.browser.invoke(BROWSER_NAVIGATE, {
                    "session_id": self.session_id, "url": prior["conversation_url"]})
            elif not prior.get("attempted"):
                # This session is separate from Ferro's reasoning conversation.
                self.provider.browser.invoke(BROWSER_NAVIGATE, {
                    "session_id": self.session_id, "url": "https://chatgpt.com/"})
            values = dict(prompt=prompt, transaction_path=str(transaction),
                          image_path=str(folder / "generated_image.png"),
                          prompt_sha256=digest, timeout=self.timeout_seconds)
            script = "\n".join(k + " = " + repr(v) for k, v in values.items()) + "\n" + GENERATE
            body = self.provider.browser.run_jev_session_script(
                self.session_id, script, package_root=Path(spec.origin).parent.parent,
                timeout_seconds=self.timeout_seconds + 45)
        if body.get("status") != "GENERATED":
            raise RuntimeError("ChatGPT image generation failed: " + str(body.get("detail") or body))
        return body


GENERATE = r'''
import base64, hashlib, io, json, os, time
from pathlib import Path
from jev_ultrafast.browser import StalePage
from PIL import Image
path=Path(transaction_path)
prior=json.loads(path.read_text()) if path.exists() else {}
outbound=('Create one actual original image using your image-generation tool. '
          'The image is an illustration for Ferro, an AI agent affiliated with LivingRuntime. '
          'Do not present an illustration as a photograph, real product screenshot, or measured result. '
          'Do not use existing copyrighted logos. Return the generated image.\n'+prompt)
def save(**changes):
    prior.update(changes)
    prior['prompt_sha256']=prompt_sha256
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(prior,ensure_ascii=False))
    os.replace(tmp,path)
def observe_state():
    return browser.evaluate("""(() => {
      const users=[...document.querySelectorAll('[data-message-author-role="user"]')];
      const assistants=[...document.querySelectorAll('[data-message-author-role="assistant"]')];
      const images=assistants.flatMap((a,index)=>[...a.querySelectorAll('img')].filter(i=>
        i.complete && i.naturalWidth>=256 && i.naturalHeight>=192).map(i=>({
          src:i.currentSrc||i.src,width:i.naturalWidth,height:i.naturalHeight,
          assistant_index:index,message_id:a.getAttribute('data-message-id')})));
      return {url:location.href,user_count:users.length,assistant_count:assistants.length,
        last_user:users.at(-1)?.innerText||'',images,
        response:assistants.at(-1)?.innerText||'',
        generating:Boolean(document.querySelector('[data-testid="stop-button"]'))};
    })()""")
def action(kind, text=None):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        page=browser.observe(screenshot=False)
        candidates=[]
        for a in page.get('actions') or []:
            if a.get('kind')!=kind:
                continue
            label=str(a.get('label') or '').casefold()
            if kind=='fill' and a.get('role')=='textbox' and not any(w in label for w in ('search','搜索')):
                candidates.append(a)
            elif kind=='click':
                send=browser.evaluate('window.__jevFast.nodes.get('+str(a['node'])+')?.getAttribute("data-testid")')
                if send=='send-button' or label in {'send','send prompt','send message','发送','发送提示','发送消息'}:
                    candidates.append(a)
        if len(candidates)==1:
            try:
                browser.act(candidates[0],page,text=text)
                return candidates[0]
            except StalePage:
                continue
        time.sleep(.25)
    raise RuntimeError('Image composer '+kind+' control unavailable or ambiguous')
try:
    deadline=time.monotonic()+timeout
    if not prior.get('attempted'):
        state=observe_state()
        if '/c/' in state['url'] or state['user_count']:
            raise RuntimeError('Image request must start in its own empty conversation')
        editor=action('fill',outbound)
        filled=browser.evaluate('(() => {const e=window.__jevFast.nodes.get('+str(editor['node'])+');return e?.innerText||e?.value||"";})()')
        if ' '.join(str(filled).split())!=' '.join(outbound.split()):
            raise RuntimeError('Image prompt fill mismatch')
        save(status='SUBMIT_PENDING',attempted=True,baseline_user_count=state['user_count'],
             baseline_assistant_count=state['assistant_count'],prompt=prompt)
        action('click')
    state=observe_state()
    while time.monotonic()<deadline:
        state=observe_state()
        if '/c/' in state['url'] and not prior.get('conversation_url'):
            save(conversation_url=state['url'])
        acknowledged=(state['user_count']>prior['baseline_user_count'] and
                      ' '.join(outbound.split()) in ' '.join(state['last_user'].split()))
        if acknowledged:
            save(status='GENERATING',submission_acknowledged=True)
            candidates=[i for i in state['images'] if i['assistant_index']>=prior['baseline_assistant_count']]
            if candidates and not state['generating']:
                if len(candidates)!=1:
                    raise RuntimeError('Generated image result is ambiguous')
                source=candidates[0]
                break
        time.sleep(1)
    else:
        raise RuntimeError('Image generation not completed; '+str(state['response'])[:600])
    # Download the exact image observed in the acknowledged assistant response,
    # using the browser's authenticated context, rather than inventing a file URL.
    expression="""(async () => {
      const response=await fetch("""+json.dumps(source['src'])+""");
      if(!response.ok)throw new Error('Image download HTTP '+response.status);
      const bytes=new Uint8Array(await response.arrayBuffer());
      if(bytes.length>20971520)throw new Error('Generated image exceeds 20 MiB');
      let binary='';
      for(let start=0;start<bytes.length;start+=8192)
        binary+=String.fromCharCode(...bytes.subarray(start,start+8192));
      return btoa(binary);
    })()"""
    download=browser.call('Runtime.evaluate',expression=expression,awaitPromise=True,
                         returnByValue=True,_response_timeout=60)
    if download.get('exceptionDetails'):
        raise RuntimeError('Observed image download failed')
    data=base64.b64decode(download['result']['value'],validate=True)
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        width,height=image.size
        if width<256 or height<192:
            raise RuntimeError('Generated image dimensions are too small')
        output=Path(image_path)
        tmp=output.with_suffix('.tmp.png')
        image.save(tmp,format='PNG')
        os.replace(tmp,output)
    save(status='GENERATED',path=str(output.resolve()),sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
         width=width,height=height,kind='AI_GENERATED',provider='CHATGPT_WEB_JEV',
         creator='Ferro via ChatGPT image generation',license='AI_GENERATED_ORIGINAL',
         source='observed ChatGPT image response',message_id=source['message_id'],ai_image_model=True)
    result=prior
except Exception as exc:
    save(status='GENERATION_UNVERIFIED' if prior.get('attempted') else 'FAILED',detail=str(exc))
    result=prior
'''
