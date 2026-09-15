"""First-party browser beacon for privacy-minimized website engagement evidence."""


def first_party_engagement_script() -> str:
    return r'''<script>(()=>{
const hex=()=>{const b=new Uint8Array(16);crypto.getRandomValues(b);return [...b].map(x=>x.toString(16).padStart(2,'0')).join('')};
const get=(store,key)=>{let v=store.getItem(key);if(!/^[0-9a-f]{32}$/.test(v||'')){v=hex();store.setItem(key,v)}return v};
let visitor,session;try{visitor=get(localStorage,'lr_vid');session=get(sessionStorage,'lr_sid')}catch(_){return}
const route=location.pathname||'/';
const send=(event_type,target_route=null)=>{const payload={schema_version:'website-engagement-event.v1',event_id:hex(),visitor_token:visitor,session_token:session,event_type,route};if(target_route)payload.target_route=target_route;const body=JSON.stringify(payload);try{navigator.sendBeacon('/events',new Blob([body],{type:'application/json'}))}catch(_){}};
send('page_view');
if(route.startsWith('/articles/')){let visible=0,done=false;const timer=setInterval(()=>{if(document.visibilityState==='visible')visible+=1;if(!done&&visible>=30){done=true;clearInterval(timer);send('engaged_30s')}},1000)}
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a[href]');if(!a)return;let u;try{u=new URL(a.href,location.href)}catch(_){return}if(u.origin!==location.origin||u.pathname===route)return;send('internal_link_click',u.pathname)},true);
})();</script>'''


__all__ = ["first_party_engagement_script"]
