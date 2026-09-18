"""Private Ferro operator console page."""
from __future__ import annotations


def operator_console_document() -> str:
    return r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Ferro Operator Console</title>
<style>
:root{color-scheme:dark;background:#080b0e;color:#edf2f6;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#14202b 0,#080b0e 34%);min-height:100vh}
main{width:min(1180px,calc(100% - 32px));margin:auto;padding:42px 0 72px}
h1{font-size:clamp(34px,5vw,54px);line-height:1.02;margin:8px 0 12px}h2{margin:28px 0 12px}h3{margin:0 0 8px}
p,.muted{color:#aeb9c5;line-height:1.55}.eyebrow{font-size:12px;letter-spacing:.16em;color:#89a0b7;text-transform:uppercase}
.card{border:1px solid #293641;background:#0e141a;border-radius:16px;padding:18px;box-shadow:0 14px 44px rgba(0,0,0,.16)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}.stack{display:grid;gap:10px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:210px}
input,select{width:100%;border:1px solid #354550;border-radius:9px;background:#080d11;color:#fff;padding:10px 11px;font:inherit}
label{display:grid;gap:6px;font-size:12px;color:#9aabb9}.two{display:grid;grid-template-columns:1fr 1fr;gap:8px}
button,a{border:0;border-radius:9px;background:#edf3f7;color:#111;padding:10px 14px;font-weight:700;text-decoration:none;cursor:pointer}
button.ghost{background:#16212a;color:#e8eff4;border:1px solid #33434f}button:disabled{opacity:.5;cursor:not-allowed}
.badge{display:inline-block;font-size:11px;padding:4px 8px;border:1px solid #43505d;border-radius:99px;letter-spacing:.04em}
.ok{color:#91e5b7;border-color:#2c7654}.pending{color:#f0ce7d;border-color:#8a6d2f}.bad{color:#ffaaa7;border-color:#7f3d3b}.info{color:#9dc9ff;border-color:#315b7e}
.hidden{display:none!important}.error{color:#ffaaa7}.success{color:#92e7b8}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}
.metric{font-size:28px;font-weight:750}.kicker{font-size:11px;color:#7f93a4;text-transform:uppercase;letter-spacing:.12em}
.divider{height:1px;background:#26343e;margin:12px 0}.resource.free{border-color:#285d49}.resource.paid{border-color:#604f2b}
@media(max-width:640px){.two{grid-template-columns:1fr}main{width:min(100% - 20px,1180px);padding-top:28px}}
</style></head><body><main>
<div class="eyebrow">LivingRuntime · private operator plane</div>
<h1>Ferro Operator Console</h1>
<p>Manage model resources without exposing secret values to Ferro, chat, logs, or status APIs.</p>

<section id="login" class="card">
<h2>Operator sign in</h2>
<p>Read the operator token only through trusted server access. Do not paste or send the token through chat or messaging.</p>
<div class="row"><input id="token" class="grow" type="password" autocomplete="off" placeholder="Operator token"><button id="signin">Sign in</button></div>
</section>

<section id="dash" class="hidden">
<div class="row"><h2 class="grow">LLM survival resources</h2><button id="refresh" class="ghost">Refresh</button><button id="logout" class="ghost">Log out</button></div>
<div id="flash"></div>

<div id="summary" class="grid"></div>

<h2>VirtualBrain routing</h2>
<div id="routing" class="card"></div>

<h2>Executable resource pool</h2>
<div id="resources" class="grid"></div>

<h2>Protected credentials & limits</h2>
<p class="muted">Secret values are write-only. Quota, reset and expiry metadata are non-secret control data used by the Survival Router.</p>
<div id="secrets" class="grid"></div>

<h2>Requests from Ferro</h2>
<div id="requests" class="stack"></div>
</section>

<script>
const el=id=>document.getElementById(id);
const fmt=v=>v===null||v===undefined||v===''?'—':String(v);
const num=v=>typeof v==='number'?Math.round(v*1000)/1000:v;
function showLogin(){el('login').classList.remove('hidden');el('dash').classList.add('hidden')}
function showDash(){el('login').classList.add('hidden');el('dash').classList.remove('hidden')}
function flash(s,bad=false){el('flash').className=bad?'error':'success';el('flash').textContent=s||''}
async function api(path,opts={}){
 const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json','X-Operator-Request':'1'},...opts});
 let body={};try{body=await r.json()}catch{}
 if(r.status===401){showLogin();throw new Error('authentication required')}
 if(!r.ok)throw new Error(body.error||'request failed');return body;
}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function badge(text,kind='info'){const b=node('span',text,'badge '+kind);return b}
function metricCard(label,value,detail){
 const c=node('div',undefined,'card');c.append(node('div',label,'kicker'),node('div',fmt(value),'metric'));if(detail)c.append(node('p',detail,'muted'));return c
}
function renderSummary(data){
 const r=data.llm_resources||{};const root=el('summary');root.replaceChildren();
 root.append(metricCard('Resources',r.resource_count||0,(r.free_count||0)+' free · '+(r.paid_count||0)+' paid'));
 const p=r.policy||{};root.append(metricCard('VB pressure',p.pressure||'UNKNOWN','mode '+fmt(p.routing_mode)));
 root.append(metricCard('Paid reserve',fmt(p.paid_reserve_floor), 'available '+fmt(p.paid_available_funds)));
 const latest=r.latest_route||{};root.append(metricCard('Latest route',latest.result||'NO ROUTE',fmt(latest.selected_resource_id)));
}
function renderRouting(data){
 const r=data.llm_resources||{},p=r.policy||{},latest=r.latest_route||{},root=el('routing');root.replaceChildren();
 const top=node('div',undefined,'row');top.append(badge(p.pressure||'UNKNOWN',(p.pressure==='NORMAL'?'ok':p.pressure==='WATCH'?'info':'pending')),badge(p.routing_mode||'NO POLICY','info'));
 if(p.prefer_free)top.append(badge('FREE PREFERRED','ok'));root.append(top);
 const body=node('p');body.textContent='Mind tick '+fmt(p.mind_tick)+' · minimum quality '+fmt(p.minimum_quality)+' · max failovers '+fmt(p.max_failovers)+' · available paid funds '+fmt(p.paid_available_funds)+' · reserve floor '+fmt(p.paid_reserve_floor);root.append(body);
 const d=node('div',undefined,'divider');root.append(d);
 const route=node('p');route.textContent='Latest: '+fmt(latest.result)+' · selected '+fmt(latest.selected_resource_id)+' · attempted '+fmt((latest.attempted_resource_ids||[]).join(' → '));root.append(route);
}
function renderResources(data){
 const rows=((data.llm_resources||{}).resources||[]),root=el('resources');root.replaceChildren();
 if(!rows.length){root.append(node('div','No executable LLM resources yet.','card muted'));return}
 for(const row of rows){
  const c=node('div',undefined,'card resource '+(row.pricing_class==='FREE'?'free':'paid'));
  const head=node('div',undefined,'row'),h=node('h3',row.provider_label+' · '+row.model,'grow');head.append(h);
  head.append(badge(row.pricing_class||'UNKNOWN',row.pricing_class==='FREE'?'ok':'pending'));
  if(row.reserve)head.append(badge('RESERVE','pending'));c.append(head);
  c.append(node('div',row.lifecycle+' · '+fmt(row.metrics_source),'muted'));
  const stats=node('p');stats.textContent='quality '+fmt(num(row.quality_score))+' · empirical success '+fmt(num(row.success_rate))+' · latency '+fmt(num(row.mean_latency_ms))+' ms';c.append(stats);
  const health=node('p');health.textContent='runtime success '+row.successes+' · failures '+row.failures+' · consecutive '+row.consecutive_failures+' · last error '+fmt(row.last_error_code);c.append(health);
  if(row.cooldown_until)c.append(badge('COOLDOWN until '+row.cooldown_until,'bad'));
  const quota=node('p');quota.textContent='quota '+fmt(row.quota_remaining)+' / '+fmt(row.quota_limit)+' '+fmt(row.quota_unit)+' · consumed '+fmt(row.consumed_quota)+' · reset '+fmt(row.quota_reset_at)+' · expires '+fmt(row.expires_at);c.append(quota);
  c.append(node('div',row.resource_id,'mono muted'));root.append(c);
 }
}
function renderRequests(rows){
 const root=el('requests');root.replaceChildren();
 if(!rows.length){root.append(node('div','No pending credential requests.','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.provider_label,'grow'),badge(row.urgency,row.urgency==='HIGH'?'bad':'pending'));c.append(head,node('p',row.summary));
  if(row.signup_url&&row.signup_url.startsWith('https://')){const a=node('a','Open official signup');a.href=row.signup_url;a.target='_blank';a.rel='noopener';c.append(a)}root.append(c)}
}
function field(labelText,input){const l=node('label');l.append(node('span',labelText),input);return l}
function input(type,placeholder,value=''){const x=document.createElement('input');x.type=type;x.placeholder=placeholder;x.value=value??'';return x}
function renderSecrets(rows){
 const root=el('secrets');root.replaceChildren();
 for(const row of rows){const c=node('div',undefined,'card');
  const head=node('div',undefined,'row'),h=node('h3',row.label,'grow');head.append(h,badge(row.configured?'CONFIGURED':'MISSING',row.configured?'ok':'pending'));c.append(head,node('div',row.env_name,'mono muted'));
  const secret=input('password','Paste new secret');secret.autocomplete='new-password';
  const save=node('button',row.configured?'Rotate secret':'Save secret');save.onclick=async()=>{const value=secret.value.trim();if(!value)return;save.disabled=true;try{const out=await api('/operator/api/secrets/'+encodeURIComponent(row.env_name),{method:'POST',body:JSON.stringify({value})});secret.value='';flash(out.label+' stored securely.');await refresh()}catch(e){flash(e.message,true)}finally{save.disabled=false}};
  c.append(field('Secret value (write-only)',secret),save);
  if(row.quota_supported){
   c.append(node('div',undefined,'divider'));
   const rem=input('number','unknown',row.quota_remaining??'');rem.min='0';rem.step='any';
   const lim=input('number','unknown',row.quota_limit??'');lim.min='0';lim.step='any';
   const unit=document.createElement('select');for(const u of ['REQUESTS','TOKENS','CREDITS']){const o=node('option',u);o.value=u;if(u===row.quota_unit)o.selected=true;unit.append(o)}
   const reset=input('text','2026-09-19T00:00:00+00:00',row.quota_reset_at??'');
   const expires=input('text','2026-10-01T00:00:00+00:00',row.expires_at??'');
   const pair=node('div',undefined,'two');pair.append(field('Remaining',rem),field('Limit',lim));c.append(pair,field('Quota unit',unit),field('Reset at (ISO + timezone)',reset),field('Expires at (ISO + timezone)',expires));
   const meta=node('button','Save limits','ghost');meta.onclick=async()=>{meta.disabled=true;try{await api('/operator/api/resources/'+encodeURIComponent(row.env_name),{method:'POST',body:JSON.stringify({quota_remaining:rem.value,quota_limit:lim.value,quota_unit:unit.value,quota_reset_at:reset.value,expires_at:expires.value})});flash(row.label+' resource metadata updated.');await refresh()}catch(e){flash(e.message,true)}finally{meta.disabled=false}};c.append(meta);
  }
  root.append(c);
 }
}
async function refresh(){const data=await api('/operator/api/status');showDash();renderSummary(data);renderRouting(data);renderResources(data);renderSecrets(data.secrets||[]);renderRequests(data.pending_requests||[])}
el('signin').onclick=async()=>{const token=el('token').value.trim();if(!token)return;try{await api('/operator/api/session',{method:'POST',body:JSON.stringify({token})});el('token').value='';await refresh()}catch(e){flash(e.message,true)}};
el('refresh').onclick=()=>refresh().catch(e=>flash(e.message,true));
el('logout').onclick=async()=>{try{await api('/operator/api/logout',{method:'POST',body:'{}'})}catch{}showLogin()};
refresh().catch(()=>showLogin());
</script></main></body></html>"""


__all__ = ["operator_console_document"]
