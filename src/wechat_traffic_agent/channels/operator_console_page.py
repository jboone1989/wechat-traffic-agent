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
<p>Send a one-time login link to the configured Enterprise WeChat operator. The link expires in 10 minutes and can be used once.</p>
<div class="row"><button id="wecomSignin">Send login link to Enterprise WeChat</button><span id="loginStatus" class="muted"></span></div>
<div class="divider"></div>
<details><summary class="muted">Emergency token fallback</summary>
<p class="muted">Use the durable operator token only for recovery when Enterprise WeChat delivery is unavailable.</p>
<div class="row"><input id="token" class="grow" type="password" autocomplete="off" placeholder="Operator token"><button id="signin" class="ghost">Sign in with token</button></div>
</details>
</section>

<section id="dash" class="hidden">
<div class="row"><h2 class="grow">Survival resources</h2><button id="refresh" class="ghost">Refresh</button><button id="logout" class="ghost">Log out</button></div>
<div id="flash"></div>

<div id="summary" class="grid"></div>

<h2>Resource domains</h2>
<div id="domains" class="grid"></div>

<h2>Survival needs & actions</h2>
<p class="muted">Low-risk local repairs can self-authorize under policy. Remote offload and primary relocation remain operator-controlled.</p>
<div id="survivalNeeds" class="stack"></div>
<div id="survivalActions" class="stack"></div>

<h2>Remote machines</h2>
<div class="card">
<h3>Hand a server to Ferro</h3>
<p class="muted">For a normal VPS, IP + password is enough. The password is used once to install a per-host SSH key, then discarded. Ferro keeps the key and host fingerprint, not the password.</p>
<div class="two"><label>IP or hostname<input id="sshHost" autocomplete="off" placeholder="207.246.99.253"></label><label>Password<input id="sshPassword" type="password" autocomplete="new-password" placeholder="One-time SSH password"></label></div>
<details><summary>Advanced SSH settings</summary>
<div class="two"><label>Name<input id="sshName" placeholder="vultr-edge-1"></label><label>SSH user<input id="sshUser" value="root" autocomplete="off"></label></div>
<label>SSH port<input id="sshPort" type="number" min="1" max="65535" value="22"></label>
</details>
<button id="bootstrapHost">Connect, audit & hand over to Ferro</button>
<div id="bootstrapStatus" class="muted"></div>
</div>
<details class="card"><summary>Advanced: register an already-running signed receiver</summary>
<div class="two"><label>Name<input id="hostName" placeholder="edge-node-1"></label><label>Reliability<select id="hostReliability"><option>EXPERIMENTAL</option><option>BEST_EFFORT</option><option>RELIABLE</option></select></label></div>
<label>HTTPS health URL<input id="hostHealth" placeholder="https://node.example.com/health"></label>
<label>Signed deployment control URL (optional)<input id="hostControl" placeholder="https://node.example.com/v1/workloads"></label>
<div class="two"><label>CPU<input id="hostCpu" type="number" min="1" placeholder="2"></label><label>Memory GiB<input id="hostMem" type="number" min="0" step="0.1" placeholder="4"></label></div>
<button id="addHost">Add signed receiver candidate</button>
</details>
<div id="remoteHosts" class="stack"></div>

<h2>Movable services</h2>
<div id="servicePlacements" class="stack"></div>

<h2>VirtualBrain routing</h2>
<div id="routing" class="card"></div>

<h2>Executable resource pool</h2>
<div id="resources" class="grid"></div>

<h2>Resource requests & lifecycle</h2>
<p class="muted">Authorize a resource before provisioning its credential. Provider onboarding is scheduled immediately after approval or credential handoff.</p>
<div id="requests" class="stack"></div>

<h2>Protected credentials & limits</h2>
<p class="muted">Secret values are write-only. Quota, reset and expiry metadata are non-secret control data used by the Survival Router.</p>
<div id="secrets" class="grid"></div>
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
 root.append(metricCard('Resources',r.resource_count||0,(r.free_count||0)+' free · '+(r.paid_count||0)+' paid · '+(r.windfall_count||0)+' windfall'));
 const p=r.policy||{};root.append(metricCard('VB pressure',p.pressure||'UNKNOWN','mode '+fmt(p.routing_mode)));
 root.append(metricCard('Paid reserve',fmt(p.paid_reserve_floor), 'available '+fmt(p.paid_available_funds)));
 const latest=r.latest_route||{};root.append(metricCard('Latest route',latest.result||'NO ROUTE',fmt(latest.selected_resource_id)));
}
function renderDomains(data){
 const rows=((data.survival_resources||{}).domains||[]),root=el('domains');root.replaceChildren();
 if(!rows.length){root.append(node('div','No survival resource observations.','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card');const head=node('div',undefined,'row');head.append(node('h3',row.domain,'grow'));
  const kind=(row.status==='HEALTHY'||row.status==='ACTIVE'||row.status==='LOCAL_PRIMARY')?'ok':row.status==='PRESSURE'?'bad':'pending';head.append(badge(row.status||'UNKNOWN',kind));c.append(head);
  if(row.domain==='LLM')c.append(node('p',fmt(row.resource_count)+' resources · '+fmt(row.free_count)+' free · pressure '+fmt(row.pressure),'muted'));
  if(row.domain==='STORAGE')c.append(node('p','free '+fmt(row.free_gib)+' GiB / '+fmt(row.total_gib)+' GiB · used '+fmt(row.used_ratio),'muted'));
  if(row.domain==='COMPUTE')c.append(node('p','CPU '+fmt(row.cpu_count)+' · load '+fmt(row.load_1m)+' · memory free '+fmt(row.memory_available_gib)+' GiB · used '+fmt(row.memory_used_ratio),'muted'));
  if(row.domain==='HOSTING')c.append(node('p','Current body: '+fmt(row.location||row.status)+' · reliability '+fmt(row.reliability),'muted'));
  if(row.domain==='REMOTE_MACHINE')c.append(node('p',fmt(row.active_count)+' active / '+fmt(row.resource_count)+' known remote bodies','muted'));
  if(row.domain==='SERVICE')c.append(node('p',fmt(row.movable_count)+' movable / '+fmt(row.resource_count)+' managed workloads','muted'));
  root.append(c)}
}
function renderSurvivalControl(data){
 const ctl=data.survival_control||{},needs=ctl.needs||[],actions=ctl.actions||[];
 const root=el('survivalNeeds');root.replaceChildren();
 if(!needs.length)root.append(node('div','No survival-resource needs.','card muted'));
 for(const row of needs){
  const c=node('div',undefined,'card'),head=node('div',undefined,'row');
  head.append(node('h3',fmt(row.domain)+' · '+fmt(row.action),'grow'));
  head.append(badge(row.status||'UNKNOWN',row.status==='EXECUTED'||row.status==='SATISFIED'?'ok':row.status==='REJECTED'||row.status==='FAILED'?'bad':'pending'));
  c.append(head,node('p',row.reason||'','muted'));
  c.append(node('div','authorization '+fmt(row.authorization)+' · urgency '+fmt(row.urgency),'mono muted'));
  if(row.status==='PENDING'){
   const controls=node('div',undefined,'row');
   for(const decision of ['APPROVE','REJECT']){const b=node('button',decision,decision==='REJECT'?'ghost':'');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/needs/'+encodeURIComponent(row.need_id)+'/decision',{method:'POST',body:JSON.stringify({decision})});flash('Survival need '+decision.toLowerCase()+'.');await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};controls.append(b)}
   c.append(controls)
  }
  root.append(c)
 }
 const log=el('survivalActions');log.replaceChildren();
 if(actions.length){log.append(node('h3','Recent resource actions'));for(const row of actions.slice(-8).reverse()){const c=node('div',undefined,'card');c.append(node('div',fmt(row.action)+' · '+fmt(row.status),'mono'),node('p',fmt(row.observed_at)+' · '+fmt(row.need_id||row.host_id||row.service_id),'muted'));if(row.action==='OFFLOAD_NON_CORE_SERVICE'&&row.status==='PLANNED'&&row.action_id){const b=node('button','Execute signed offload');b.onclick=async()=>{b.disabled=true;try{const out=await api('/operator/api/survival/offload/'+encodeURIComponent(row.action_id)+'/execute',{method:'POST',body:'{}'});flash('Remote deployment '+fmt(out.status)+'.');await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(b)}log.append(c)}}
}
function renderRemoteHosts(data){
 const ctl=data.survival_control||{},rows=ctl.remote_hosts||[],root=el('remoteHosts');root.replaceChildren();
 if(!rows.length){root.append(node('div','No remote machines registered. Ferro will prefer local restructuring first.','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.name||row.host_id,'grow'),badge(row.status||'CANDIDATE',row.status==='ACTIVE'?'ok':row.status==='FAILED'?'bad':'pending'));if(row.management_transport==='SSH_KEY')head.append(badge('SSH KEY MANAGED','ok'));if(row.deployment_capable)head.append(badge('SIGNED DEPLOY','ok'));c.append(head);if(row.ssh_host)c.append(node('p',fmt(row.ssh_username)+'@'+fmt(row.ssh_host)+':'+fmt(row.ssh_port)+' · '+fmt(row.reliability),'muted'));else c.append(node('p',fmt(row.health_url)+' · '+fmt(row.reliability),'muted'));c.append(node('div',JSON.stringify(row.capacity||{}),'mono muted'));if(row.ssh_host_key_sha256)c.append(node('div','host key '+fmt(row.ssh_host_key_sha256),'mono muted'));if(row.receiver_status)c.append(node('div','receiver '+fmt(row.receiver_status),'mono muted'));if(row.control_url)c.append(node('div','control '+fmt(row.control_url),'mono muted'));if(row.agent_id)c.append(node('div','agent '+fmt(row.agent_id)+' · key '+fmt(row.key_id),'mono muted'));if(!row.ssh_host&&(row.status!=='ACTIVE'||(row.control_url&&!row.deployment_capable))){const b=node('button','Verify & activate');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/hosts/'+encodeURIComponent(row.host_id)+'/verify',{method:'POST',body:'{}'});await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(b)}root.append(c)}
}
function renderServicePlacements(data){
 const ctl=data.survival_control||{},services=ctl.services||[],hosts=(ctl.remote_hosts||[]).filter(x=>x.status==='ACTIVE'&&x.deployment_capable),root=el('servicePlacements');root.replaceChildren();
 for(const row of services){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.service_id,'grow'),badge(row.migration_class||'UNKNOWN',row.migration_class==='PRIMARY_STATEFUL'?'pending':'info'));c.append(head,node('p','placement '+fmt(row.placement)+' · role '+fmt(row.role),'muted'));if(row.migration_class!=='PRIMARY_STATEFUL'&&hosts.length){const select=document.createElement('select');for(const host of hosts){const o=node('option',host.name||host.host_id);o.value=host.host_id;select.append(o)}const b=node('button','Plan offload');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/offload',{method:'POST',body:JSON.stringify({service_id:row.service_id,host_id:select.value})});flash('Offload plan created.');await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(select,b)}root.append(c)}
}
function renderRouting(data){
 const r=data.llm_resources||{},p=r.policy||{},latest=r.latest_route||{},root=el('routing');root.replaceChildren();
 const top=node('div',undefined,'row');top.append(badge(p.pressure||'UNKNOWN',(p.pressure==='NORMAL'?'ok':p.pressure==='WATCH'?'info':'pending')),badge(p.routing_mode||'NO POLICY','info'));
 if(p.prefer_free)top.append(badge('FREE PREFERRED','ok'));
 if(p.windfall_active)top.append(badge('WINDFALL ACTIVE','success'));
 root.append(top);
 const body=node('p');body.textContent='Mind tick '+fmt(p.mind_tick)+' · minimum quality '+fmt(p.minimum_quality)+' · max failovers '+fmt(p.max_failovers)+' · available paid funds '+fmt(p.paid_available_funds)+' · reserve floor '+fmt(p.paid_reserve_floor)+' · windfall '+fmt(p.windfall_mode);root.append(body);
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
  if(row.reserve)head.append(badge('RESERVE','pending'));
  if(row.windfall_eligible)head.append(badge('WINDFALL','success'));
  if(row.capability_tier)head.append(badge(row.capability_tier,row.capability_tier==='FRONTIER'?'success':'info'));
  c.append(head);
  c.append(node('div',row.lifecycle+' · '+fmt(row.metrics_source)+' · capability '+fmt(num(row.capability_score)),'muted'));
  const stats=node('p');stats.textContent='quality '+fmt(num(row.quality_score))+' · empirical success '+fmt(num(row.success_rate))+' · latency '+fmt(num(row.mean_latency_ms))+' ms';c.append(stats);
  const tags=node('p');tags.textContent='capabilities '+fmt((row.capability_tags||[]).join(', '));c.append(tags);
  const health=node('p');health.textContent='runtime success '+row.successes+' · failures '+row.failures+' · consecutive '+row.consecutive_failures+' · last error '+fmt(row.last_error_code);c.append(health);
  if(row.cooldown_until)c.append(badge('COOLDOWN until '+row.cooldown_until,'bad'));
  const quota=node('p');quota.textContent='quota '+fmt(row.quota_remaining)+' / '+fmt(row.quota_limit)+' '+fmt(row.quota_unit)+' · consumed '+fmt(row.consumed_quota)+' · reset '+fmt(row.quota_reset_at)+' · expires '+fmt(row.expires_at);c.append(quota);
  c.append(node('div',row.resource_id,'mono muted'));root.append(c);
 }
}
function renderRequests(rows){
 const root=el('requests');root.replaceChildren();
 if(!rows.length){root.append(node('div','No resource requests yet.','card muted'));return}
 for(const row of rows){
  const c=node('div',undefined,'card'),head=node('div',undefined,'row');
  head.append(node('h3',row.provider_label+' · '+row.credential_env,'grow'));
  const auth=row.authorization_status||'PENDING';head.append(badge(auth,auth==='APPROVED'||auth==='LEGACY_APPROVED'?'ok':auth==='REJECTED'?'bad':'pending'));c.append(head,node('p',row.summary));
  const flow=node('div',undefined,'row');for(const stage of (row.stages||[])){const complete=stage.status==='COMPLETE'||stage.status==='APPROVED'||stage.status==='LEGACY_APPROVED';const bad=stage.status==='REJECTED';const b=badge(stage.stage+' · '+stage.status,bad?'bad':complete?'ok':'pending');if(stage.at)b.title=stage.at;flow.append(b)}c.append(flow);
  const detail=node('p');detail.textContent='trials '+fmt(row.completed_trial_count)+' · active models '+fmt((row.active_models||[]).join(', '))+' · used '+(row.used?'yes':'no');c.append(detail);
  const actions=node('div',undefined,'row');
  if(auth==='PENDING'){const approve=node('button','Approve');approve.onclick=async()=>{approve.disabled=true;try{const out=await api('/operator/api/requests/'+encodeURIComponent(row.request_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'APPROVE'})});flash(out.credential_configured?'Approved; onboarding scheduled.':'Approved. Configure the credential below; onboarding will start immediately after save.');await refresh()}catch(e){flash(e.message,true)}finally{approve.disabled=false}};
   const reject=node('button','Reject','ghost');reject.onclick=async()=>{reject.disabled=true;try{await api('/operator/api/requests/'+encodeURIComponent(row.request_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'REJECT'})});flash('Resource request rejected.');await refresh()}catch(e){flash(e.message,true)}finally{reject.disabled=false}};actions.append(approve,reject)}
  if(row.signup_url&&row.signup_url.startsWith('https://')){const a=node('a','Open official signup');a.href=row.signup_url;a.target='_blank';a.rel='noopener';actions.append(a)}
  c.append(actions);root.append(c)
 }
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
async function refresh(){const data=await api('/operator/api/status');showDash();renderSummary(data);renderDomains(data);renderSurvivalControl(data);renderRemoteHosts(data);renderServicePlacements(data);renderRouting(data);renderResources(data);renderSecrets(data.secrets||[]);renderRequests(data.resource_requests||[])}
el('bootstrapHost').onclick=async()=>{const btn=el('bootstrapHost'),status=el('bootstrapStatus'),password=el('sshPassword');const host=el('sshHost').value.trim();if(!host||!password.value){status.textContent='IP/hostname and password are required.';return}btn.disabled=true;status.textContent='Connecting, auditing and installing Ferro key…';try{const out=await api('/operator/api/survival/hosts/bootstrap',{method:'POST',body:JSON.stringify({host,port:Number(el('sshPort').value||22),username:el('sshUser').value.trim()||'root',password:password.value,name:el('sshName').value.trim()})});password.value='';status.textContent='Managed with SSH key. Password discarded. Receiver onboarding scheduled.';flash((out.name||host)+' is now a managed remote body.');await refresh()}catch(e){password.value='';status.textContent=e.message;flash(e.message,true)}finally{btn.disabled=false}};
el('addHost').onclick=async()=>{const btn=el('addHost');btn.disabled=true;try{const cpu=Number(el('hostCpu').value||0),memory=Number(el('hostMem').value||0);await api('/operator/api/survival/hosts',{method:'POST',body:JSON.stringify({name:el('hostName').value,health_url:el('hostHealth').value,control_url:el('hostControl').value,reliability:el('hostReliability').value,capacity:{cpu_count:cpu||null,memory_gib:memory||null}})});el('hostName').value='';el('hostHealth').value='';el('hostControl').value='';el('hostCpu').value='';el('hostMem').value='';flash('Remote host candidate registered. Verify it before use.');await refresh()}catch(e){flash(e.message,true)}finally{btn.disabled=false}};
el('wecomSignin').onclick=async()=>{const btn=el('wecomSignin'),status=el('loginStatus');btn.disabled=true;status.textContent='Sending…';try{await api('/operator/api/request-login',{method:'POST',body:'{}'});status.textContent='Sent. Open the one-time link in Enterprise WeChat.'}catch(e){status.textContent=e.message}finally{setTimeout(()=>{btn.disabled=false},45000)}};
el('signin').onclick=async()=>{const token=el('token').value.trim();if(!token)return;try{await api('/operator/api/session',{method:'POST',body:JSON.stringify({token})});el('token').value='';await refresh()}catch(e){flash(e.message,true)}};
el('refresh').onclick=()=>refresh().catch(e=>flash(e.message,true));
el('logout').onclick=async()=>{try{await api('/operator/api/logout',{method:'POST',body:'{}'})}catch{}showLogin()};
async function bootstrapFromHash(){
 const params=new URLSearchParams((location.hash||'').replace(/^#/,''));
 const token=params.get('bootstrap');
 if(!token)return false;
 history.replaceState(null,'',location.pathname+location.search);
 try{
  await api('/operator/api/bootstrap',{method:'POST',body:JSON.stringify({token})});
  flash('One-time access accepted.');
  await refresh();
  return true;
 }catch(e){flash(e.message,true);showLogin();return false}
}
(async()=>{if(!(await bootstrapFromHash()))refresh().catch(()=>showLogin())})();
</script></main></body></html>"""


__all__ = ["operator_console_document"]
