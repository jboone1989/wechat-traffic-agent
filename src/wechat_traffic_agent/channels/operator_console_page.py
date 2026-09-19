"""Private Ferro operator console page."""
from __future__ import annotations


def operator_console_document() -> str:
    return r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Ferro 管理后台</title>
<style>

:root{color-scheme:dark;background:#0a0d10;color:#edf2f6;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}body{margin:0;background:#0a0d10;min-height:100vh}main{width:min(1180px,calc(100% - 32px));margin:auto;padding:24px 0 64px}
h1{font-size:30px;line-height:1.1;margin:0}h2{font-size:22px;margin:0}h3{font-size:16px;margin:0}p{margin:8px 0}.muted{color:#91a0ad;line-height:1.5}
.topbar{display:flex;align-items:center;gap:16px;margin-bottom:18px}.brand{flex:1}.brand p{margin:5px 0 0}.toolbar{display:flex;gap:8px;flex-wrap:wrap}
button,a{border:1px solid #34414c;border-radius:9px;background:#182027;color:#eef4f8;padding:9px 12px;font:inherit;font-weight:650;text-decoration:none;cursor:pointer}
button.primary,a.primary{background:#edf3f7;color:#111;border-color:#edf3f7}button.ghost{background:transparent}button:disabled{opacity:.5;cursor:not-allowed}
.card{border:1px solid #252f38;background:#10151a;border-radius:12px;padding:15px;min-width:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:10px}.stack{display:grid;gap:9px}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:0}.two{display:grid;grid-template-columns:1fr 1fr;gap:9px}
label{display:grid;gap:6px;font-size:12px;color:#9baab6}input,select{width:100%;min-width:0;border:1px solid #34414c;border-radius:8px;background:#090d11;color:#fff;padding:10px;font:inherit}
.badge{display:inline-block;font-size:11px;padding:3px 7px;border:1px solid #46535e;border-radius:99px}.ok{color:#8ee1b2;border-color:#2d7052}.pending{color:#e8c875;border-color:#806a32}.bad{color:#ffaaa7;border-color:#7a3c3b}.info{color:#9ac8ff;border-color:#315b7e}
.hidden{display:none!important}.error{color:#ffaaa7}.success{color:#8ee1b2}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}
.nav{position:sticky;top:8px;z-index:20;display:grid;grid-template-columns:repeat(5,1fr);gap:5px;padding:5px;margin-bottom:18px;border:1px solid #252f38;border-radius:11px;background:rgba(10,13,16,.95);backdrop-filter:blur(10px)}
.nav button{background:transparent;border-color:transparent;color:#91a0ad;padding:10px 6px}.nav button.active{background:#202a32;color:#fff;border-color:#33414d}.tab-count{margin-left:4px;padding:1px 5px;border-radius:99px;background:#7a3c3b;color:white;font-size:10px}
.panel{display:none}.panel.active{display:block}.page-head{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:12px}.page-head p{margin:4px 0 0}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}.metric-card{border:1px solid #252f38;background:#10151a;border-radius:12px;padding:14px}.metric-label{font-size:12px;color:#8e9ca8}.metric-value{font-size:26px;font-weight:760;margin-top:5px}.metric-detail{font-size:12px;color:#7f8d99;margin-top:3px}
.dashboard-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:10px;margin-top:10px}.panel-card{border:1px solid #252f38;background:#10151a;border-radius:12px;padding:15px}.panel-title{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.health-list,.activity-list{display:grid;gap:11px}.health-row{display:grid;gap:6px}.health-line{display:flex;justify-content:space-between;gap:12px;font-size:13px}.bar{height:7px;border-radius:99px;background:#202930;overflow:hidden}.bar>span{display:block;height:100%;background:#8aa3b5;border-radius:99px}
.activity-item{display:grid;grid-template-columns:8px 1fr;gap:10px;align-items:start}.activity-dot{width:8px;height:8px;border-radius:50%;background:#6f8797;margin-top:6px}.activity-item p{margin:2px 0;font-size:13px}.activity-item small{color:#71818d}
.section{margin-top:16px}.section-title{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 9px}.section-title p{margin:3px 0 0}
details.fold{border:1px solid #252f38;background:#10151a;border-radius:12px;margin-top:10px}details.fold>summary{cursor:pointer;padding:13px 15px;font-weight:700}details.fold>.fold-body{padding:0 15px 15px}
.resource-row{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(100px,.6fr) auto;gap:10px;align-items:center}.resource-row+.resource-row{border-top:1px solid #232c34;padding-top:10px;margin-top:10px}
.browser-shot{width:100%;height:auto;display:block;border-radius:8px;border:1px solid #34414f;background:#05080a;touch-action:manipulation}.browser-controls{display:grid;gap:8px;margin-top:10px}.browser-keys{display:flex;gap:8px;flex-wrap:wrap}
.account-card{display:grid;gap:10px}.account-meta,.account-actions{display:flex;gap:7px;flex-wrap:wrap}.account-actions button{flex:0 1 auto}.account-login-shell{margin-top:12px}
.divider{height:1px;background:#263038;margin:12px 0}.mobile-note{display:none}
@media(max-width:760px){main{width:calc(100% - 16px);padding-top:14px}.topbar{align-items:flex-start;flex-direction:column}.toolbar{width:100%}.toolbar>*{flex:1;text-align:center}.nav{top:5px}.nav button{font-size:12px}.metrics{grid-template-columns:repeat(2,1fr)}.dashboard-grid{grid-template-columns:1fr}.page-head{align-items:flex-start;flex-direction:column}.two{grid-template-columns:1fr}.metric-value{font-size:22px}.resource-row{grid-template-columns:1fr auto}.resource-row>:nth-child(2){grid-column:1/2}.row>button,.row>a{flex:1 1 130px;text-align:center}.mobile-note{display:block;font-size:12px;color:#71818d}}
</style></head><body><main>

<section id="login" class="card">
 <h2>管理员登录</h2>
 <p class="muted">通过企业微信获取一次性登录链接。</p>
 <div class="row"><button id="wecomSignin" class="primary">发送登录链接</button><span id="loginStatus" class="muted"></span></div>
 <div class="divider"></div>
 <details><summary class="muted">备用 Token 登录</summary>
  <div class="row"><input id="token" class="grow" type="password" autocomplete="off" placeholder="Operator token"><button id="signin">登录</button></div>
 </details>
</section>

<section id="dash" class="hidden">
<div class="topbar">
 <div class="brand"><h1>Ferro 管理后台</h1><p class="muted">资源、配置和待办事项统一管理。</p></div>
 <div class="toolbar"><a href="/operator/chat/">和 Ferro 对话</a><button id="refresh" class="ghost">刷新</button><button id="logout" class="ghost">退出</button></div>
</div>

 <nav id="consoleNav" class="nav" aria-label="后台导航">
  <button class="nav-tab active" data-panel-target="dashboard">仪表盘</button>
  <button class="nav-tab" data-panel-target="todos">待办 <span id="actionTabCount" class="tab-count hidden">0</span></button>
  <button class="nav-tab" data-panel-target="accounts">账户</button>
  <button class="nav-tab" data-panel-target="resources">资源</button>
  <button class="nav-tab" data-panel-target="config">配置</button>
 </nav>
 <div id="flash"></div>

 <section class="panel active" data-panel="dashboard">
  <div class="page-head"><div><h2>仪表盘</h2><p class="muted">关键资源和运行状态。</p></div></div>
  <div id="summary" class="metrics"></div>
  <div class="dashboard-grid">
   <div class="panel-card"><div class="panel-title"><h3>资源使用</h3><span class="muted">当前</span></div><div id="dashboardHealth" class="health-list"></div></div>
   <div class="panel-card"><div class="panel-title"><h3>最近活动</h3><span class="muted">最新 6 条</span></div><div id="dashboardActivity" class="activity-list"></div></div>
  </div>
  <div class="section"><div class="section-title"><div><h3>服务状态</h3><p class="muted">各类资源是否可用。</p></div></div><div id="domains" class="grid"></div></div>
 </section>

 <section class="panel" data-panel="todos">
  <div class="page-head"><div><h2>待办事项</h2><p class="muted">只显示需要管理员处理的事项。</p></div></div>
  <div class="section"><div class="section-title"><h3>审批与操作</h3></div><div id="survivalNeeds" class="stack"></div></div>
  <div class="section"><div class="section-title"><h3>资源申请</h3></div><div id="requests" class="stack"></div></div>
  <details class="fold"><summary>历史操作</summary><div class="fold-body"><div id="survivalActions" class="stack"></div></div></details>
 </section>

 <section class="panel" data-panel="accounts">
  <div class="page-head"><div><h2>账户管理</h2><p class="muted">统一管理 X、YouTube、邮箱等外部身份。账户是长期资源，登录方式只是适配器。</p></div></div>
  <div id="accounts" class="grid"></div>
  <div id="accountLoginShell" class="card account-login-shell hidden">
   <div class="row"><div class="grow"><h3 id="accountLoginTitle">服务器网页会话</h3><p class="muted">这是网页自动化的高级回退，不是普通账号登录。OAuth/API 身份应优先使用跳转授权；这里仅用于让 Ferro 自己的服务器浏览器获得网页登录态。</p></div><button id="accountLoginClose" class="ghost">关闭</button></div>
   <div id="localBrowserStatus" class="mono muted">未启动。</div>
   <img id="localBrowserShot" class="browser-shot hidden" alt="Ferro account login browser">
   <div class="browser-controls">
    <label>输入内容<input id="localBrowserText" type="password" autocomplete="off" placeholder="先点击截图中的输入框"></label>
    <div class="row"><button id="localBrowserSend" class="primary">发送</button><button id="localBrowserRefresh" class="ghost">刷新画面</button></div>
    <div class="browser-keys"><button data-browser-key="Tab" class="ghost">Tab</button><button data-browser-key="Enter" class="ghost">Enter</button><button data-browser-key="Backspace" class="ghost">Backspace</button><button data-browser-key="Escape" class="ghost">Esc</button></div>
   </div>
  </div>
 </section>

 <section class="panel" data-panel="resources">
  <div class="page-head"><div><h2>资源管理</h2><p class="muted">查看主机、服务、浏览器和模型资源。</p></div></div>
  <div class="section"><div class="section-title"><h3>主机</h3></div><div id="remoteHosts" class="stack"></div></div>
  <div class="section"><div class="section-title"><h3>服务</h3></div><div id="servicePlacements" class="stack"></div></div>
  <div class="section"><div class="section-title"><h3>浏览器</h3></div><div id="browserRuntime" class="grid"></div></div>
  <div class="section"><div class="section-title"><h3>模型资源</h3></div><div id="resources" class="stack"></div></div>
  <details class="fold"><summary>路由策略</summary><div class="fold-body"><div id="routing"></div></div></details>
 </section>

 <section class="panel" data-panel="config">
  <div class="page-head"><div><h2>配置管理</h2><p class="muted">服务器、API 密钥和账号配置。</p></div></div>
  <div class="section"><div class="section-title"><div><h3>添加服务器</h3><p class="muted">普通 VPS 填 IP 和密码即可。</p></div></div>
   <div class="card">
    <div class="two"><label>IP 或主机名<input id="sshHost" autocomplete="off" placeholder="207.246.99.253"></label><label>一次性 SSH 密码<input id="sshPassword" type="password" autocomplete="new-password"></label></div>
    <details><summary class="muted">高级 SSH 设置</summary><div class="two"><label>名称<input id="sshName" placeholder="vultr-edge-1"></label><label>SSH 用户<input id="sshUser" value="root" autocomplete="off"></label></div><label>SSH 端口<input id="sshPort" type="number" min="1" max="65535" value="22"></label></details>
    <div class="row"><button id="bootstrapHost" class="primary">连接并添加</button><span id="bootstrapStatus" class="muted"></span></div>
   </div>
  </div>
  <details class="fold"><summary>高级主机注册</summary><div class="fold-body">
   <div class="two"><label>名称<input id="hostName" placeholder="edge-node-1"></label><label>可靠性<select id="hostReliability"><option>EXPERIMENTAL</option><option>BEST_EFFORT</option><option>RELIABLE</option></select></label></div>
   <label>HTTPS 健康检查 URL<input id="hostHealth" placeholder="https://node.example.com/health"></label><label>部署控制 URL<input id="hostControl" placeholder="https://node.example.com/v1/workloads"></label>
   <div class="two"><label>CPU<input id="hostCpu" type="number" min="1" placeholder="2"></label><label>内存 GiB<input id="hostMem" type="number" min="0" step="0.1" placeholder="4"></label></div><button id="addHost">添加候选主机</button>
  </div></details>
  <div class="section"><div class="section-title"><div><h3>API 与密钥</h3><p class="muted">密钥只写入，不会在页面回显。</p></div></div><div id="secrets" class="grid"></div></div>
 </section>
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
 if(!r.ok){const e=new Error(body.error||'request failed');e.status=r.status;e.body=body;throw e}return body;
}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function badge(text,kind='info'){const b=node('span',text,'badge '+kind);return b}
function metricCard(label,value,detail){
 const c=node('div',undefined,'metric-card');c.append(node('div',label,'metric-label'),node('div',fmt(value),'metric-value'));if(detail)c.append(node('div',detail,'metric-detail'));return c
}
function operatorItems(data){
 const ctl=data.survival_control||{},requests=data.resource_requests||[];
 const survival=(ctl.needs||[]).filter(x=>x.status==='PENDING');
 const approvals=requests.filter(x=>(x.authorization_status||'PENDING')==='PENDING');
 const credentials=requests.filter(x=>['APPROVED','LEGACY_APPROVED'].includes(x.authorization_status)&&!x.credential_configured);
 return {survival,approvals,credentials,total:survival.length+approvals.length+credentials.length}
}
function rememberedPanel(){try{return localStorage.getItem('ferro.operator.panel')||'dashboard'}catch{return 'dashboard'}}
function selectPanel(name,remember=true){
 const allowed=['dashboard','todos','accounts','resources','config'];if(!allowed.includes(name))name='dashboard';
 for(const panel of document.querySelectorAll('[data-panel]'))panel.classList.toggle('active',panel.dataset.panel===name);
 for(const tab of document.querySelectorAll('.nav-tab'))tab.classList.toggle('active',tab.dataset.panelTarget===name);
 if(remember){try{localStorage.setItem('ferro.operator.panel',name)}catch{}}
}
function clamp01(v){v=Number(v);return Number.isFinite(v)?Math.max(0,Math.min(1,v)):0}
function healthBar(label,value,detail){
 const row=node('div',undefined,'health-row'),line=node('div',undefined,'health-line'),bar=node('div',undefined,'bar'),fill=node('span');
 fill.style.width=(clamp01(value)*100).toFixed(0)+'%';bar.append(fill);line.append(node('span',label),node('span',detail||Math.round(clamp01(value)*100)+'%','muted'));row.append(line,bar);return row
}
function renderSummary(data){
 const r=data.llm_resources||{},items=operatorItems(data),ctl=data.survival_control||{},browser=data.browser_runtime||{},count=el('actionTabCount');
 const hosts=ctl.remote_hosts||[],activeHosts=hosts.filter(x=>x.status==='ACTIVE').length,readyBrowser=(browser.providers||[]).filter(x=>x.status==='READY').length,root=el('summary');
 count.textContent=String(items.total);count.classList.toggle('hidden',items.total===0);root.replaceChildren();
 root.append(metricCard('待办事项',items.total,items.total?'需要处理':'暂无待办'));
 root.append(metricCard('模型资源',r.resource_count||0,(r.free_count||0)+' 免费 · '+(r.paid_count||0)+' 付费'));
 root.append(metricCard('在线主机',activeHosts,hosts.length+' 台已登记'));
 root.append(metricCard('浏览器服务',readyBrowser,(browser.providers||[]).length+' 个提供方'));
}
function renderDashboardHealth(data){
 const root=el('dashboardHealth'),domains=((data.survival_resources||{}).domains||[]),llm=data.llm_resources||{},secrets=data.secrets||[];
 root.replaceChildren();
 const storage=domains.find(x=>x.domain==='STORAGE')||{},compute=domains.find(x=>x.domain==='COMPUTE')||{};
 const storageUsed=Number(storage.used_ratio||0),memoryUsed=Number(compute.memory_used_ratio||0);
 const freeRatio=llm.resource_count?Number(llm.free_count||0)/Number(llm.resource_count):0;
 const configured=secrets.length?secrets.filter(x=>x.configured).length/secrets.length:0;
 root.append(healthBar('磁盘使用',storageUsed,Math.round(storageUsed*100)+'%'));
 root.append(healthBar('内存使用',memoryUsed,Math.round(memoryUsed*100)+'%'));
 root.append(healthBar('免费模型占比',freeRatio,(llm.free_count||0)+' / '+(llm.resource_count||0)));
 root.append(healthBar('密钥配置完成',configured,secrets.filter(x=>x.configured).length+' / '+secrets.length));
}
function renderDashboardActivity(data){
 const root=el('dashboardActivity'),actions=((data.survival_control||{}).actions||[]).slice(-5).reverse(),latest=(data.llm_resources||{}).latest_route||{};
 root.replaceChildren();
 const rows=[];
 if(latest.result||latest.selected_resource_id)rows.push({title:'模型路由 · '+fmt(latest.result||'完成'),detail:fmt(latest.selected_resource_id),at:latest.observed_at});
 for(const x of actions)rows.push({title:fmt(x.action||'资源操作')+' · '+fmt(x.status),detail:fmt(x.need_id||x.host_id||x.service_id||''),at:x.observed_at});
 if(!rows.length){root.append(node('div','暂无活动记录','muted'));return}
 for(const x of rows.slice(0,6)){const item=node('div',undefined,'activity-item'),body=node('div');body.append(node('p',x.title),node('small',x.detail+(x.at?' · '+x.at:'')));item.append(node('span',undefined,'activity-dot'),body);root.append(item)}
}
function renderDomains(data){
 const rows=((data.survival_resources||{}).domains||[]),root=el('domains'),names={LLM:'模型',STORAGE:'磁盘',COMPUTE:'计算',HOSTING:'当前主机',REMOTE_MACHINE:'远程主机',SERVICE:'服务'};
 root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无状态数据','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row'),kind=(row.status==='HEALTHY'||row.status==='ACTIVE'||row.status==='LOCAL_PRIMARY')?'ok':row.status==='PRESSURE'?'bad':'pending';
  head.append(node('h3',names[row.domain]||row.domain,'grow'),badge(row.status||'UNKNOWN',kind));c.append(head);
  if(row.domain==='LLM')c.append(node('p',fmt(row.resource_count)+' 个模型资源 · '+fmt(row.free_count)+' 个免费','muted'));
  if(row.domain==='STORAGE')c.append(node('p','剩余 '+fmt(row.free_gib)+' GiB / '+fmt(row.total_gib)+' GiB','muted'));
  if(row.domain==='COMPUTE')c.append(node('p','CPU '+fmt(row.cpu_count)+' · 可用内存 '+fmt(row.memory_available_gib)+' GiB','muted'));
  if(row.domain==='HOSTING')c.append(node('p',fmt(row.location||row.status)+' · '+fmt(row.reliability),'muted'));
  if(row.domain==='REMOTE_MACHINE')c.append(node('p',fmt(row.active_count)+' 在线 / '+fmt(row.resource_count)+' 已登记','muted'));
  if(row.domain==='SERVICE')c.append(node('p',fmt(row.movable_count)+' 可迁移 / '+fmt(row.resource_count)+' 个服务','muted'));
  root.append(c)}
}
function renderSurvivalControl(data){
 const ctl=data.survival_control||{},needs=(ctl.needs||[]).filter(x=>x.status==='PENDING'),actions=ctl.actions||[],root=el('survivalNeeds'),log=el('survivalActions');
 root.replaceChildren();log.replaceChildren();
 if(!needs.length)root.append(node('div','暂无需要处理的操作','card muted'));
 for(const row of needs){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',fmt(row.action),'grow'),badge(fmt(row.urgency||'NORMAL'),'pending'));c.append(head,node('p',row.reason||'','muted'));
  const controls=node('div',undefined,'row');
  const approve=node('button','批准','primary'),reject=node('button','拒绝','ghost');
  approve.onclick=async()=>{approve.disabled=true;try{await api('/operator/api/survival/needs/'+encodeURIComponent(row.need_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'APPROVE'})});flash('已批准');await refresh()}catch(e){flash(e.message,true)}finally{approve.disabled=false}};
  reject.onclick=async()=>{reject.disabled=true;try{await api('/operator/api/survival/needs/'+encodeURIComponent(row.need_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'REJECT'})});flash('已拒绝');await refresh()}catch(e){flash(e.message,true)}finally{reject.disabled=false}};
  controls.append(approve,reject);c.append(controls);root.append(c)}
 if(!actions.length)log.append(node('div','暂无历史操作','muted'));
 for(const row of actions.slice(-8).reverse()){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('strong',fmt(row.action),'grow'),badge(fmt(row.status),row.status==='FAILED'?'bad':'info'));c.append(head,node('p',fmt(row.observed_at),'muted'));
  if(row.action==='OFFLOAD_NON_CORE_SERVICE'&&row.status==='PLANNED'&&row.action_id){const b=node('button','执行迁移');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/offload/'+encodeURIComponent(row.action_id)+'/execute',{method:'POST',body:'{}'});await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(b)}log.append(c)}
}
function renderRemoteHosts(data){
 const rows=((data.survival_control||{}).remote_hosts||[]),root=el('remoteHosts');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无远程主机','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.name||row.host_id,'grow'),badge(row.status||'UNKNOWN',row.status==='ACTIVE'?'ok':row.status==='FAILED'?'bad':'pending'));c.append(head);
  c.append(node('p',row.ssh_host?fmt(row.ssh_username)+'@'+fmt(row.ssh_host)+':'+fmt(row.ssh_port):fmt(row.health_url),'muted'));
  const detail=document.createElement('details'),summary=node('summary','查看详情','muted'),body=node('div',undefined,'mono muted');body.textContent='可靠性 '+fmt(row.reliability)+' · '+JSON.stringify(row.capacity||{});detail.append(summary,body);c.append(detail);
  if(!row.ssh_host&&(row.status!=='ACTIVE'||(row.control_url&&!row.deployment_capable))){const b=node('button','验证并启用');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/hosts/'+encodeURIComponent(row.host_id)+'/verify',{method:'POST',body:'{}'});await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(b)}root.append(c)}
}
function renderServicePlacements(data){
 const ctl=data.survival_control||{},services=ctl.services||[],hosts=(ctl.remote_hosts||[]).filter(x=>x.status==='ACTIVE'&&x.deployment_capable),root=el('servicePlacements');root.replaceChildren();
 if(!services.length){root.append(node('div','暂无服务','card muted'));return}
 for(const row of services){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.service_id,'grow'),badge(fmt(row.placement||'LOCAL'),'info'));c.append(head,node('p',fmt(row.role)+' · '+fmt(row.migration_class),'muted'));
  if(row.migration_class!=='PRIMARY_STATEFUL'&&hosts.length){const controls=node('div',undefined,'row'),select=document.createElement('select');for(const host of hosts){const o=node('option',host.name||host.host_id);o.value=host.host_id;select.append(o)}
   const b=node('button','迁移到主机');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/offload',{method:'POST',body:JSON.stringify({service_id:row.service_id,host_id:select.value})});flash('迁移计划已创建');await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};controls.append(select,b);c.append(controls)}root.append(c)}
}
function renderRouting(data){
 const r=data.llm_resources||{},p=r.policy||{},latest=r.latest_route||{},root=el('routing');root.replaceChildren();
 const top=node('div',undefined,'row');top.append(badge(p.pressure||'UNKNOWN',(p.pressure==='NORMAL'?'ok':p.pressure==='WATCH'?'info':'pending')),badge(p.routing_mode||'NO POLICY','info'));
 if(p.prefer_free)top.append(badge('优先免费','ok'));if(p.windfall_active)top.append(badge('临时免费资源','ok'));root.append(top);
 root.append(node('p','最低质量 '+fmt(p.minimum_quality)+' · 最大故障切换 '+fmt(p.max_failovers)+' · 可用付费额度 '+fmt(p.paid_available_funds)+' · 保留额度 '+fmt(p.paid_reserve_floor),'muted'));
 root.append(node('p','最近路由：'+fmt(latest.result)+' · '+fmt(latest.selected_resource_id),'mono'));
}
function renderBrowserRuntime(data){
 const rows=((data.browser_runtime||{}).providers||[]),root=el('browserRuntime');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无浏览器状态','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row'),kind=row.status==='READY'?'ok':row.status==='NOT_CONFIGURED'||row.status==='DISABLED'?'pending':'bad';
  head.append(node('h3',row.provider||'UNKNOWN','grow'),badge(row.status||'UNKNOWN',kind));c.append(head);
  if(row.provider==='LOCAL_CDP')c.append(node('p','本地持久化 Chrome','muted'));
  if(row.provider==='JEV'){c.append(node('p','用于自适应网页操作','muted'));const result=node('div','可在配置页保存 Jev 密钥后测试','mono muted'),test=node('button','测试 Jev');test.onclick=async()=>{test.disabled=true;result.textContent='正在测试…';try{const out=await api('/operator/api/browser/jev/test',{method:'POST',body:'{}'});result.textContent=out.status+' · '+fmt(out.elapsed_ms)+' ms · '+fmt(out.action_count)+' actions';flash('Jev 测试通过')}catch(e){result.textContent=e.message;flash(e.message,true)}finally{test.disabled=false}};c.append(test,result)}
  if(row.provider==='TINYFISH')c.append(node('p','兼容性备用浏览器','muted'));if(row.reason)c.append(node('p',row.reason,'muted'));root.append(c)}
}
let activeAccountProvider='';
function accountStatusKind(status){
 if(status==='CONNECTED')return 'ok';
 if(status==='DEGRADED')return 'bad';
 if(status==='AUTHENTICATING'||status==='NEEDS_LOGIN'||status==='NEEDS_AUTHORIZATION')return 'pending';
 return 'info'
}
function renderAccounts(data){
 const rows=((data.accounts||{}).accounts||[]),root=el('accounts');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无账户提供方','card muted'));return}
 for(const row of rows){
  const c=node('div',undefined,'card account-card'),head=node('div',undefined,'row');
  head.append(node('h3',row.label,'grow'),badge(row.status||'UNKNOWN',accountStatusKind(row.status)));c.append(head);
  if(row.identity)c.append(node('div','@'+row.identity,'mono'));
  const meta=node('div',undefined,'account-meta');meta.append(badge(row.auth_surface||'AUTH','info'));
  for(const cap of (row.capabilities||[]))meta.append(badge(cap,'info'));c.append(meta);
  const bindings=row.bindings||[];
  for(const binding of bindings){
   const line=node('div',undefined,'row'),copy=node('div',undefined,'grow');
   copy.append(node('strong',binding.label||binding.binding_id),node('div',(binding.purpose||'')+' · '+(binding.auth_mode||''),'muted'));
   line.append(copy,badge(binding.status||'UNKNOWN',accountStatusKind(binding.status)));
   if(binding.identity)line.append(node('span','@'+binding.identity,'mono muted'));
   c.append(line)
  }
  if(row.note)c.append(node('p',row.note,'muted'));if(row.last_error)c.append(node('p',row.last_error,'error'));
  const actions=node('div',undefined,'account-actions');
  if(row.provider_id==='x'){
   const web=bindings.find(x=>x.binding_id==='browser_session');
   const connect=node('button',web&&web.status==='CONNECTED'?'重新连接网页会话':'网页登录（网页自动化）','ghost');
   connect.onclick=()=>beginAccountConnect(row).catch(e=>flash(e.message,true));
   const verify=node('button','验证网页会话','ghost');
   verify.onclick=async()=>{verify.disabled=true;try{const out=await api('/operator/api/accounts/'+encodeURIComponent(row.provider_id)+'/verify',{method:'POST',body:'{}'});flash('Browser session · '+out.status);await refresh()}catch(e){flash(e.message,true)}finally{verify.disabled=false}};
   actions.append(connect,verify)
  }else if(row.implemented){
   const connect=node('button','连接','primary');connect.onclick=()=>beginAccountConnect(row).catch(e=>flash(e.message,true));actions.append(connect)
  }else actions.append(badge('OAuth adapter slot','info'));
  c.append(actions);root.append(c)
 }
}
async function beginAccountConnect(row){
 activeAccountProvider=row.provider_id;selectPanel('accounts');
 el('accountLoginShell').classList.remove('hidden');el('accountLoginTitle').textContent=row.label+' · 服务器网页会话';
 el('localBrowserStatus').textContent='正在打开 Ferro 服务器上的网页会话…';
 const out=await api('/operator/api/accounts/'+encodeURIComponent(row.provider_id)+'/connect',{method:'POST',body:'{}'});
 if(out.account&&out.account.status==='CONNECTED')flash(row.label+' 已连接');
 await new Promise(r=>setTimeout(r,250));await accountLoginSnapshot(5);await refresh()
}
async function accountLoginSnapshot(retries=3){
 if(!activeAccountProvider)throw new Error('没有正在进行的账户登录');
 const shot=el('localBrowserShot'),status=el('localBrowserStatus');let last;
 for(let i=0;i<retries;i++){
  try{
   const out=await api('/operator/api/accounts/'+encodeURIComponent(activeAccountProvider)+'/login/snapshot');
   shot.src='data:image/jpeg;base64,'+out.screenshot_b64;shot.classList.remove('hidden');
   status.textContent=(out.authenticated?'已连接'+(out.identity?' · @'+out.identity:'')+' · ':'')+fmt(out.url)+' · '+fmt(out.title);
   if(out.authenticated){flash('账户已连接');await refresh()}return out
  }catch(e){
   last=e;
   if(e.status===409&&i<retries-1){
    await api('/operator/api/accounts/'+encodeURIComponent(activeAccountProvider)+'/connect',{method:'POST',body:'{}'});
    await new Promise(r=>setTimeout(r,300));continue
   }
   if(e.status!==502||i===retries-1)throw e;
   await new Promise(r=>setTimeout(r,350))
  }
 }throw last
}
async function accountLoginAction(action,body={}){
 if(!activeAccountProvider)throw new Error('没有正在进行的账户登录');
 const provider=activeAccountProvider;
 try{
  await api('/operator/api/accounts/'+encodeURIComponent(provider)+'/login/'+action,{method:'POST',body:JSON.stringify(body)})
 }catch(e){
  if(e.status!==409)throw e;
  await api('/operator/api/accounts/'+encodeURIComponent(provider)+'/connect',{method:'POST',body:'{}'});
  await new Promise(r=>setTimeout(r,250));await accountLoginSnapshot(5);
  throw new Error('服务器网页会话刚刚恢复，请再操作一次')
 }
 await new Promise(r=>setTimeout(r,250));return accountLoginSnapshot(5)
}
function renderResources(data){
 const rows=((data.llm_resources||{}).resources||[]),root=el('resources');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无模型资源','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),line=node('div',undefined,'resource-row'),name=node('div'),status=node('div'),actions=node('div');
  name.append(node('h3',row.provider_label+' · '+row.model),node('div','质量 '+fmt(num(row.quality_score))+' · 成功率 '+fmt(num(row.success_rate)),'muted'));
  status.append(badge(row.pricing_class||'UNKNOWN',row.pricing_class==='FREE'?'ok':'pending'));
  const detail=document.createElement('details');detail.append(node('summary','详情','muted'));const p=node('p','延迟 '+fmt(num(row.mean_latency_ms))+' ms · 成功 '+row.successes+' · 失败 '+row.failures+' · 配额 '+fmt(row.quota_remaining)+' / '+fmt(row.quota_limit),'mono muted');detail.append(p);actions.append(detail);
  line.append(name,status,actions);c.append(line);root.append(c)}
}
function renderRequests(rows){
 const root=el('requests'),actionable=rows.filter(row=>{const a=row.authorization_status||'PENDING';return a==='PENDING'||(['APPROVED','LEGACY_APPROVED'].includes(a)&&!row.credential_configured)});root.replaceChildren();
 if(!actionable.length){root.append(node('div','暂无资源申请','card muted'));return}
 for(const row of actionable){const c=node('div',undefined,'card'),head=node('div',undefined,'row'),auth=row.authorization_status||'PENDING';head.append(node('h3',row.provider_label||row.credential_env,'grow'),badge(auth,auth==='PENDING'?'pending':'info'));c.append(head,node('p',row.summary||'','muted'));
  const controls=node('div',undefined,'row');
  if(auth==='PENDING'){const approve=node('button','批准','primary'),reject=node('button','拒绝','ghost');approve.onclick=async()=>{approve.disabled=true;try{await api('/operator/api/requests/'+encodeURIComponent(row.request_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'APPROVE'})});await refresh()}catch(e){flash(e.message,true)}finally{approve.disabled=false}};reject.onclick=async()=>{reject.disabled=true;try{await api('/operator/api/requests/'+encodeURIComponent(row.request_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'REJECT'})});await refresh()}catch(e){flash(e.message,true)}finally{reject.disabled=false}};controls.append(approve,reject)}
  else{const config=node('button','去配置密钥');config.onclick=()=>selectPanel('config');controls.append(config)}
  if(row.signup_url&&row.signup_url.startsWith('https://')){const a=node('a','打开官网');a.href=row.signup_url;a.target='_blank';a.rel='noopener';controls.append(a)}c.append(controls);root.append(c)}
}
function field(labelText,input){const l=node('label');l.append(node('span',labelText),input);return l}
function input(type,placeholder,value=''){const x=document.createElement('input');x.type=type;x.placeholder=placeholder;x.value=value??'';return x}
function renderSecrets(rows){
 const root=el('secrets');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无可配置密钥','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.label,'grow'),badge(row.configured?'已配置':'未配置',row.configured?'ok':'pending'));c.append(head,node('div',row.env_name,'mono muted'));
  const secret=input('password','粘贴新的密钥');secret.autocomplete='new-password';const save=node('button',row.configured?'更新密钥':'保存密钥');
  save.onclick=async()=>{const value=secret.value.trim();if(!value)return;save.disabled=true;try{await api('/operator/api/secrets/'+encodeURIComponent(row.env_name),{method:'POST',body:JSON.stringify({value})});secret.value='';flash(row.label+' 已保存');await refresh()}catch(e){flash(e.message,true)}finally{save.disabled=false}};c.append(field('密钥（只写）',secret),save);
  if(row.quota_supported){const details=document.createElement('details');details.append(node('summary','额度设置','muted'));const box=node('div',undefined,'stack'),rem=input('number','未知',row.quota_remaining??''),lim=input('number','未知',row.quota_limit??'');rem.min='0';lim.min='0';rem.step='any';lim.step='any';
   const unit=document.createElement('select');for(const u of ['REQUESTS','TOKENS','CREDITS']){const o=node('option',u);o.value=u;if(u===row.quota_unit)o.selected=true;unit.append(o)}
   const reset=input('text','ISO 时间',row.quota_reset_at??''),expires=input('text','ISO 时间',row.expires_at??''),pair=node('div',undefined,'two');pair.append(field('剩余额度',rem),field('额度上限',lim));box.append(pair,field('额度单位',unit),field('重置时间',reset),field('过期时间',expires));
   const meta=node('button','保存额度','ghost');meta.onclick=async()=>{meta.disabled=true;try{await api('/operator/api/resources/'+encodeURIComponent(row.env_name),{method:'POST',body:JSON.stringify({quota_remaining:rem.value,quota_limit:lim.value,quota_unit:unit.value,quota_reset_at:reset.value,expires_at:expires.value})});flash(row.label+' 额度已更新');await refresh()}catch(e){flash(e.message,true)}finally{meta.disabled=false}};box.append(meta);details.append(box);c.append(details)}
  root.append(c)}
}
async function refresh(){const data=await api('/operator/api/status');showDash();renderSummary(data);renderDashboardHealth(data);renderDashboardActivity(data);renderDomains(data);renderSurvivalControl(data);renderRemoteHosts(data);renderServicePlacements(data);renderRouting(data);renderAccounts(data);renderBrowserRuntime(data);renderResources(data);renderSecrets(data.secrets||[]);renderRequests(data.resource_requests||[])}
for(const tab of document.querySelectorAll('.nav-tab'))tab.onclick=()=>selectPanel(tab.dataset.panelTarget);
selectPanel(rememberedPanel(),false);
el('bootstrapHost').onclick=async()=>{const btn=el('bootstrapHost'),status=el('bootstrapStatus'),password=el('sshPassword');const host=el('sshHost').value.trim();if(!host||!password.value){status.textContent='IP/hostname and password are required.';return}btn.disabled=true;status.textContent='Connecting, auditing and installing Ferro key…';try{const out=await api('/operator/api/survival/hosts/bootstrap',{method:'POST',body:JSON.stringify({host,port:Number(el('sshPort').value||22),username:el('sshUser').value.trim()||'root',password:password.value,name:el('sshName').value.trim()})});password.value='';status.textContent='Managed with SSH key. Password discarded. Receiver onboarding scheduled.';flash((out.name||host)+' is now a managed remote body.');await refresh()}catch(e){password.value='';status.textContent=e.message;flash(e.message,true)}finally{btn.disabled=false}};
el('addHost').onclick=async()=>{const btn=el('addHost');btn.disabled=true;try{const cpu=Number(el('hostCpu').value||0),memory=Number(el('hostMem').value||0);await api('/operator/api/survival/hosts',{method:'POST',body:JSON.stringify({name:el('hostName').value,health_url:el('hostHealth').value,control_url:el('hostControl').value,reliability:el('hostReliability').value,capacity:{cpu_count:cpu||null,memory_gib:memory||null}})});el('hostName').value='';el('hostHealth').value='';el('hostControl').value='';el('hostCpu').value='';el('hostMem').value='';flash('Remote host candidate registered. Verify it before use.');await refresh()}catch(e){flash(e.message,true)}finally{btn.disabled=false}};
el('accountLoginClose').onclick=async()=>{try{if(activeAccountProvider)await api('/operator/api/accounts/'+encodeURIComponent(activeAccountProvider)+'/login/stop',{method:'POST',body:'{}'})}catch{}activeAccountProvider='';el('accountLoginShell').classList.add('hidden');el('localBrowserShot').classList.add('hidden');el('localBrowserShot').removeAttribute('src');el('localBrowserStatus').textContent='已关闭。账户会话仍保存在对应的持久化认证存储中。'};
el('localBrowserRefresh').onclick=()=>accountLoginSnapshot(5).catch(e=>{el('localBrowserStatus').textContent=e.message;flash(e.message,true)});
el('localBrowserShot').onclick=async ev=>{const img=el('localBrowserShot'),r=img.getBoundingClientRect();if(!r.width||!r.height)return;const x=(ev.clientX-r.left)/r.width,y=(ev.clientY-r.top)/r.height;try{await accountLoginAction('click',{x_ratio:x,y_ratio:y})}catch(e){flash(e.message,true)}};
el('localBrowserSend').onclick=async()=>{const field=el('localBrowserText'),value=field.value;field.value='';if(!value)return;try{await accountLoginAction('type',{text:value})}catch(e){flash(e.message,true)}};
for(const b of document.querySelectorAll('[data-browser-key]'))b.onclick=async()=>{try{await accountLoginAction('key',{key:b.dataset.browserKey})}catch(e){flash(e.message,true)}};
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
