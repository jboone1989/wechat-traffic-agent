"""Read public counters from the exact verified post/Article surface."""
import json
from .x_jev_social import helpers


def jev_x_metrics(url, marker):
    return 'destination = '+json.dumps(url)+'\nmarker = '+json.dumps(marker)+'\n'+helpers()+r'''
try:
    navigate(destination)
    wait_value('Boolean(document.querySelector("main"))',15)
    raw=wait_value(''' + "'''" + r'''(() => {
      const marker='''+json.dumps(marker)+r''';
      const article=marker.includes('/i/article/') ? document.querySelector('main') :
        [...document.querySelectorAll('article')].find(a=>[...a.querySelectorAll('time')].some(t=>
          (t.closest('a')?.getAttribute('href')||'').includes(marker)));
      if(!article)return null;
      const counter=id=>{
        const ns=[...article.querySelectorAll('[data-testid="'+id+'"]')];
        return ns.length===1 ? (ns[0].getAttribute('aria-label')||ns[0].innerText||'') : '';
      };
      const analytics=[...article.querySelectorAll('a[href$="/analytics"]')];
      return {replies:counter('reply'),reposts:counter('retweet'),likes:counter('like'),
        views:analytics.length===1 ? analytics[0].innerText : ''};
    })()''' + "'''" + r''',15)
    if not isinstance(raw,dict):
        raise RuntimeError('Public X metric surface did not render')
    result={'status':'OBSERVED',**raw}
except Exception as exc:
    result={'status':'FAILED','detail':str(exc)}
'''
