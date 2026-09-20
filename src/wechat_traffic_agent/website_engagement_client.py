"""First-party browser beacon for privacy-minimized website engagement evidence."""


def first_party_engagement_script() -> str:
    return r'''<script>(()=>{
const hex=()=>{const b=new Uint8Array(16);crypto.getRandomValues(b);return [...b].map(x=>x.toString(16).padStart(2,'0')).join('')};
const get=(store,key)=>{let v=store.getItem(key);if(!/^[0-9a-f]{32}$/.test(v||'')){v=hex();store.setItem(key,v)}return v};
let visitor,session;try{visitor=get(localStorage,'lr_vid');session=get(sessionStorage,'lr_sid')}catch(_){return}
const route=location.pathname||'/';
const acquisition=(()=>{try{const saved=sessionStorage.getItem('lr_acq');if(/^(direct|google|x|social|search|referral|other|unknown)$/.test(saved||''))return saved;let v='direct';if(document.referrer){try{const u=new URL(document.referrer),h=(u.hostname||'').toLowerCase();if(u.origin===location.origin)v='direct';else if(h==='x.com'||h.endsWith('.x.com')||h==='t.co'||h.endsWith('.t.co'))v='x';else if(h==='google.com'||h.endsWith('.google.com')||h.includes('.google.'))v='google';else if(/bing\\.|duckduckgo\\.|baidu\\.|yahoo\\./.test(h))v='search';else if(/facebook\\.|instagram\\.|linkedin\\.|reddit\\.|weibo\\.|weixin\\.|wechat\\./.test(h))v='social';else v='referral'}catch(_){v='unknown'}}sessionStorage.setItem('lr_acq',v);return v}catch(_){return'unknown'}})();
const device_class=(()=>{const ua=navigator.userAgent||'';if(/iPad|Tablet|PlayBook|Silk/i.test(ua))return'tablet';if(/Mobi|Android|iPhone|iPod/i.test(ua))return'mobile';return ua?'desktop':'unknown'})();
let audience_class='public';try{if(localStorage.getItem('lr_audience_role')==='operator')audience_class='operator'}catch(_){}
const send=(event_type,target_route=null)=>{const payload={schema_version:'website-engagement-event.v1',event_id:hex(),visitor_token:visitor,session_token:session,event_type,route,acquisition,device_class,audience_class};if(target_route)payload.target_route=target_route;const body=JSON.stringify(payload);try{navigator.sendBeacon('/events',new Blob([body],{type:'application/json'}))}catch(_){}};
send('page_view');
if(route.startsWith('/articles/')){let visible=0,done=false;const timer=setInterval(()=>{if(document.visibilityState==='visible')visible+=1;if(!done&&visible>=30){done=true;clearInterval(timer);send('engaged_30s')}},1000)}
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a[href]');if(!a)return;let u;try{u=new URL(a.href,location.href)}catch(_){return}if(u.origin!==location.origin||u.pathname===route)return;send('internal_link_click',u.pathname)},true);
})();</script>'''


__all__ = ["first_party_engagement_script"]
