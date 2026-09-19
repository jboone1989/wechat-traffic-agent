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
.browser-live{width:100%;height:auto;aspect-ratio:1120/780;display:block;border-radius:8px;border:1px solid #34414f;background:#05080a;touch-action:none;cursor:crosshair}.browser-controls{display:grid;gap:8px;margin-top:10px}.browser-keys{display:flex;gap:8px;flex-wrap:wrap}.live-indicator{display:inline-flex;align-items:center;gap:6px}.live-indicator:before{content:"";width:7px;height:7px;border-radius:50%;background:#5b6872}.live-indicator.live:before{background:#8ee1b2}
.account-card{display:grid;gap:10px}.account-meta,.account-actions{display:flex;gap:7px;flex-wrap:wrap}.account-actions button{flex:0 1 auto}.account-login-shell{margin-top:12px}.account-binding{display:grid;gap:5px;padding:11px 0;border-top:1px solid #263038}.account-binding:first-of-type{border-top:0}.account-binding-title{display:flex;align-items:center;gap:8px}.account-binding-title strong{flex:1}.account-binding p{margin:0}.account-tech{margin-top:2px}.account-tech summary{font-size:11px;color:#71818d;cursor:pointer}
.divider{height:1px;background:#263038;margin:12px 0}.mobile-note{display:none}
@media(max-width:760px){main{width:calc(100% - 16px);padding-top:14px}.topbar{align-items:flex-start;flex-direction:column}.toolbar{width:100%}.toolbar>*{flex:1;text-align:center}.nav{top:5px}.nav button{font-size:12px}.metrics{grid-template-columns:repeat(2,1fr)}.dashboard-grid{grid-template-columns:1fr}.page-head{align-items:flex-start;flex-direction:column}.two{grid-template-columns:1fr}.metric-value{font-size:22px}.resource-row{grid-template-columns:1fr auto}.resource-row>:nth-child(2){grid-column:1/2}.row>button,.row>a{flex:1 1 130px;text-align:center}.account-card{gap:8px;padding:12px}.account-actions{gap:5px}.account-actions button{flex:0 0 auto!important;width:auto;padding:7px 9px;font-size:12px}.account-login-shell{padding:10px;margin-top:8px}.account-login-shell>.row:first-child{align-items:flex-start;gap:6px}.account-login-shell>.row:first-child>button{flex:0 0 auto!important;width:auto;padding:6px 9px;font-size:12px}.account-login-shell h3{font-size:15px}.account-login-shell h3+p{font-size:11px;line-height:1.45;margin:3px 0}.browser-live{border-radius:6px}.browser-controls{gap:6px;margin-top:7px}.browser-controls label{font-size:11px}.browser-controls input{padding:8px}.browser-controls>.row{gap:5px;flex-wrap:wrap}.browser-controls>.row>button{flex:0 0 auto!important;width:auto;padding:7px 9px;font-size:12px}.browser-keys{gap:4px}.browser-keys button{flex:0 0 auto!important;width:auto;padding:6px 8px;font-size:11px;min-width:0}.live-indicator{font-size:10px;line-height:1.35}.mobile-note{display:block;font-size:12px;color:#71818d}}
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
   <div id="localBrowserStatus" class="mono muted live-indicator">未连接实时会话。</div>
   <canvas id="liveBrowserCanvas" class="browser-live hidden" width="1120" height="780" aria-label="Ferro live browser session"></canvas>
   <div class="browser-controls">
    <label>输入内容<input id="localBrowserText" type="password" autocomplete="off" placeholder="先点击实时浏览器里的输入框"></label>
    <div class="row"><button id="localBrowserSend" class="primary">发送</button><button id="liveBrowserReconnect" class="ghost">重新连接实时画面</button></div>
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
 const ctl=data.survival_control||{},needs=(ctl.needs||[]).filter(x=>['PENDING','APPROVED','AWAITING_RESOURCE'].includes(x.status)),actions=ctl.actions||[],root=el('survivalNeeds'),log=el('survivalActions');
 root.replaceChildren();log.replaceChildren();
 if(!needs.length)root.append(node('div','暂无需要处理的操作','card muted'));
 for(const row of needs){
  const c=node('div',undefined,'card'),head=node('div',undefined,'row'),kind=row.status==='APPROVED'?'ok':row.status==='AWAITING_RESOURCE'?'info':'pending';
  head.append(node('h3',fmt(row.action),'grow'),badge(fmt(row.status),kind),badge(fmt(row.urgency||'NORMAL'),'pending'));c.append(head,node('p',row.reason||'','muted'));
  if(row.authorization)c.append(node('div',fmt(row.authorization),'mono muted'));
  if(row.retry_count)c.append(node('div','重试 '+fmt(row.retry_count)+' · 下次 '+fmt(row.next_retry_at)+' · 最近错误 '+fmt(row.last_error_type||row.last_result_status),'mono muted'));
  if(row.status==='PENDING'&&row.authorization!=='AUTONOMOUS_SAFE_POLICY'){
   const controls=node('div',undefined,'row'),approve=node('button','批准','primary'),reject=node('button','拒绝','ghost');
   approve.onclick=async()=>{approve.disabled=true;try{await api('/operator/api/survival/needs/'+encodeURIComponent(row.need_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'APPROVE'})});flash('已批准');await refresh()}catch(e){flash(e.message,true)}finally{approve.disabled=false}};
   reject.onclick=async()=>{reject.disabled=true;try{await api('/operator/api/survival/needs/'+encodeURIComponent(row.need_id)+'/decision',{method:'POST',body:JSON.stringify({decision:'REJECT'})});flash('已拒绝');await refresh()}catch(e){flash(e.message,true)}finally{reject.disabled=false}};
   controls.append(approve,reject);c.append(controls)
  }else if(row.authorization==='AUTONOMOUS_SAFE_POLICY'){c.append(node('p','Ferro 自动处理，无需人工批准','muted'))}
  root.append(c)}
 if(!actions.length)log.append(node('div','暂无历史操作','muted'));
 for(const row of actions.slice(-8).reverse()){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('strong',fmt(row.action),'grow'),badge(fmt(row.status),row.status==='FAILED'?'bad':'info'));c.append(head,node('p',fmt(row.observed_at),'muted'));
  if(row.action==='OFFLOAD_NON_CORE_SERVICE'&&row.status==='PLANNED'&&row.action_id){const b=node('button','执行迁移');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/offload/'+encodeURIComponent(row.action_id)+'/execute',{method:'POST',body:'{}'});await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(b)}log.append(c)}
}
function renderRemoteHosts(data){
 const rows=((data.survival_control||{}).remote_hosts||[]),root=el('remoteHosts');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无远程主机','card muted'));return}
 for(const row of rows){const c=node('div',undefined,'card'),head=node('div',undefined,'row');head.append(node('h3',row.name||row.host_id,'grow'),badge(row.status||'UNKNOWN',row.status==='ACTIVE'?'ok':row.status==='FAILED'?'bad':'pending'));if(row.receiver_status)head.append(badge('receiver '+fmt(row.receiver_status),row.receiver_status==='VERIFIED'?'ok':row.receiver_status==='DEGRADED'||row.receiver_status==='PROVISION_FAILED'?'bad':'pending'));if(row.active_receiver_transport)head.append(badge(fmt(row.active_receiver_transport),'info'));c.append(head);
  c.append(node('p',row.ssh_host?fmt(row.ssh_username)+'@'+fmt(row.ssh_host)+':'+fmt(row.ssh_port):fmt(row.health_url),'muted'));
  if(row.ssh_receiver_status)c.append(node('div','SSH receiver '+fmt(row.ssh_receiver_status)+' · '+fmt(row.ssh_receiver_release_sha),'mono muted'));
  if(row.ssh_receiver_last_error)c.append(node('div','最近安装错误 '+fmt(row.ssh_receiver_last_error),'mono bad'));
  const detail=document.createElement('details'),summary=node('summary','查看详情','muted'),body=node('div',undefined,'mono muted');body.textContent='可靠性 '+fmt(row.reliability)+' · '+JSON.stringify(row.capacity||{})+' · host '+fmt(row.host_instance_id)+' · receiver '+fmt(row.receiver_transport);detail.append(summary,body);c.append(detail);
  if(!row.ssh_host&&(row.status!=='ACTIVE'||(row.control_url&&!row.deployment_capable))){const b=node('button','验证并启用');b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/hosts/'+encodeURIComponent(row.host_id)+'/verify',{method:'POST',body:'{}'});await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};c.append(b)}root.append(c)}
}
function renderServicePlacements(data){
 const ctl=data.survival_control||{},services=ctl.services||[],hosts=ctl.remote_hosts||[],candidates=ctl.placement_candidates||[],root=el('servicePlacements');root.replaceChildren();
 const hostById=new Map(hosts.map(x=>[x.host_id,x]));
 if(!services.length){root.append(node('div','暂无服务','card muted'));return}
 for(const row of services){
  const policy=row.placement_policy||{},requirements=row.resource_requirements||{},replicas=row.replicas||[];
  const c=node('div',undefined,'card'),head=node('div',undefined,'row');
  head.append(node('h3',row.service_id,'grow'),badge('主实例 '+fmt(row.primary_placement||row.placement||'LOCAL'),'info'));
  c.append(head,node('p',fmt(row.role)+' · '+fmt(row.migration_class)+' · '+fmt(policy.mode||'MANAGED'),'muted'));
  c.append(node('p','最低资源：CPU '+fmt(requirements.cpu_count)+' · 可用内存 '+fmt(requirements.memory_available_gib)+' GiB · 可用磁盘 '+fmt(requirements.disk_free_gib)+' GiB','muted'));
  if(replicas.length){
   const box=node('div',undefined,'stack');
   for(const replica of replicas){const h=hostById.get(replica.host_id)||{},line=node('div',undefined,'row');line.append(node('span','副本 '+fmt(h.name||replica.host_id),'grow'),badge(fmt(replica.status),replica.status==='ACTIVE'?'ok':'bad'));box.append(line)}
   c.append(box)
  }else if(policy.mode==='REPLICA')c.append(node('p','当前没有远程副本','muted'));
  const options=candidates.filter(x=>x.service_id===row.service_id&&x.eligible);
  const rejected=candidates.filter(x=>x.service_id===row.service_id&&!x.eligible);
  if(row.migration_class!=='PRIMARY_STATEFUL'&&options.length){
   const controls=node('div',undefined,'row'),select=document.createElement('select');
   for(const fit of options){const o=node('option',(fit.host_name||fit.host_id)+' · fit '+fmt(fit.score));o.value=fit.host_id;select.append(o)}
   const label=policy.mode==='REPLICA'?'复制到主机':'迁移到主机',b=node('button',label);
   b.onclick=async()=>{b.disabled=true;try{await api('/operator/api/survival/offload',{method:'POST',body:JSON.stringify({service_id:row.service_id,host_id:select.value})});flash((policy.mode==='REPLICA'?'复制':'迁移')+'计划已创建');await refresh()}catch(e){flash(e.message,true)}finally{b.disabled=false}};
   controls.append(select,b);c.append(controls)
  }else if(row.migration_class!=='PRIMARY_STATEFUL'){
   c.append(node('p','当前无符合资源约束的远程主机','muted'));
   for(const fit of rejected.slice(0,3))c.append(node('div',fmt(fit.host_name||fit.host_id)+' · '+(fit.reasons||[]).join(', '),'mono muted'))
  }
  root.append(c)
 }
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
function accountStatusLabel(status){
 return ({CONNECTED:'已连接',NEEDS_LOGIN:'未登录',NEEDS_AUTHORIZATION:'未授权',AUTHENTICATING:'登录中',DEGRADED:'异常',ADAPTER_NOT_INSTALLED:'待接入'})[status]||status||'未知'
}
function renderAccounts(data){
 const rows=((data.accounts||{}).accounts||[]),root=el('accounts');root.replaceChildren();
 if(!rows.length){root.append(node('div','暂无账户提供方','card muted'));return}
 for(const row of rows){
  const c=node('div',undefined,'card account-card'),head=node('div',undefined,'row'),bindings=row.bindings||[];
  head.append(node('h3',row.label,'grow'),badge(accountStatusLabel(row.status),accountStatusKind(row.status)));c.append(head);
  if(row.identity)c.append(node('div','@'+row.identity,'mono'));
  if(row.provider_id==='x'){
   const apiBinding=bindings.find(x=>x.binding_id==='api_oauth')||{},web=bindings.find(x=>x.binding_id==='browser_session')||{};
   const apiBox=node('div',undefined,'account-binding'),apiTitle=node('div',undefined,'account-binding-title');
   apiTitle.append(node('strong','API 连接'),badge(accountStatusLabel(apiBinding.status),accountStatusKind(apiBinding.status)));
   apiBox.append(apiTitle,node('p','用于读取、发布、提及和数据统计。','muted'));
   if(apiBinding.identity)apiBox.append(node('div','@'+apiBinding.identity,'mono muted'));
   c.append(apiBox);
   const webBox=node('div',undefined,'account-binding'),webTitle=node('div',undefined,'account-binding-title');
   webTitle.append(node('strong','网页自动化'),badge(accountStatusLabel(web.status),accountStatusKind(web.status)));
   webBox.append(webTitle);
   if(web.status==='CONNECTED'){
    webBox.append(node('p','服务器浏览器已登录 X，可供 Ferro 执行网页自动化。','muted'))
   }else if(web.status==='DEGRADED'){
    webBox.append(node('p','服务器浏览器会话异常，不影响上面的 API 连接。','muted'))
   }else{
    webBox.append(node('p','服务器浏览器尚未登录 X。这不影响上面的 API 连接。只有需要操作网页时才需要登录。','muted'))
   }
   if(web.last_error&&web.status==='DEGRADED')webBox.append(node('p','上次检查：'+web.last_error,'error'));
   c.append(webBox);
   const actions=node('div',undefined,'account-actions');
   const connect=node('button',web.status==='CONNECTED'?'打开服务器浏览器':'登录服务器浏览器','ghost');
   connect.onclick=()=>beginAccountConnect(row).catch(e=>flash(e.message,true));
   const verify=node('button','检查网页登录','ghost');
   verify.onclick=async()=>{verify.disabled=true;try{const out=await api('/operator/api/accounts/'+encodeURIComponent(row.provider_id)+'/verify',{method:'POST',body:'{}'});flash(out.status==='CONNECTED'?'服务器浏览器已登录':'服务器浏览器尚未登录');await refresh()}catch(e){flash(e.message,true)}finally{verify.disabled=false}};
   actions.append(connect,verify);c.append(actions);
   const tech=document.createElement('details');tech.className='account-tech';tech.append(node('summary','技术信息'));
   const meta=node('div',undefined,'account-meta');meta.append(badge(row.auth_surface||'AUTH','info'));
   for(const cap of (row.capabilities||[]))meta.append(badge(cap,'info'));
   tech.append(meta,node('div','API: '+fmt(apiBinding.auth_mode)+' · Browser: '+fmt(web.auth_mode),'mono muted'));c.append(tech)
  }else{
   const meta=node('div',undefined,'account-meta');meta.append(badge(row.auth_surface||'AUTH','info'));
   for(const cap of (row.capabilities||[]))meta.append(badge(cap,'info'));c.append(meta);
   if(row.last_error)c.append(node('p',row.last_error,'error'));
   const actions=node('div',undefined,'account-actions');
   if(row.implemented){const connect=node('button','连接','primary');connect.onclick=()=>beginAccountConnect(row).catch(e=>flash(e.message,true));actions.append(connect)}
   else actions.append(badge('待接入适配器','info'));
   c.append(actions)
  }
  root.append(c)
 }
}
let accountStream=null,accountStreamGeneration=0,streamFramePending=null,streamFrameDrawing=false;
function closeAccountStream(){
 accountStreamGeneration++;
 if(accountStream){accountStream.onclose=null;try{accountStream.close()}catch{}}
 accountStream=null;
 const status=el('localBrowserStatus');status.classList.remove('live')
}
function accountStreamSend(payload){
 if(!accountStream||accountStream.readyState!==WebSocket.OPEN)throw new Error('实时浏览器会话未连接');
 accountStream.send(JSON.stringify(payload))
}
function drawAccountFrame(data){
 streamFramePending=data;
 if(streamFrameDrawing)return;
 const drawNext=()=>{
  const frame=streamFramePending;streamFramePending=null;
  if(!frame){streamFrameDrawing=false;return}
  streamFrameDrawing=true;
  const img=new Image();
  img.onload=()=>{
   const canvas=el('liveBrowserCanvas'),ctx=canvas.getContext('2d');
   ctx.drawImage(img,0,0,canvas.width,canvas.height);
   canvas.classList.remove('hidden');
   drawNext()
  };
  img.onerror=()=>drawNext();
  img.src='data:image/jpeg;base64,'+frame
 };
 drawNext()
}
function accountStreamUrl(provider){
 const scheme=location.protocol==='https:'?'wss:':'ws:';
 return scheme+'//'+location.host+'/operator/api/accounts/'+encodeURIComponent(provider)+'/login/stream'
}
async function openAccountStream(provider,recovery=0){
 closeAccountStream();
 activeAccountProvider=provider;
 const generation=accountStreamGeneration,ws=new WebSocket(accountStreamUrl(provider));
 accountStream=ws;
 const status=el('localBrowserStatus');
 status.textContent='正在连接实时浏览器…';
 return await new Promise((resolve,reject)=>{
  let settled=false;
  const timer=setTimeout(()=>{if(!settled){settled=true;reject(new Error('实时浏览器连接超时'))}},10000);
  ws.onopen=()=>{if(generation!==accountStreamGeneration)return;status.textContent='实时浏览器已连接，等待画面…';status.classList.add('live')};
  ws.onmessage=async ev=>{
   if(generation!==accountStreamGeneration)return;
   let msg;try{msg=JSON.parse(ev.data)}catch{return}
   if(msg.type==='frame'){
    drawAccountFrame(msg.data);
    if(!settled){settled=true;clearTimeout(timer);resolve()}
   }else if(msg.type==='status'){
    const prefix=msg.authenticated?'已登录'+(msg.identity?' · @'+msg.identity:''):'实时';
    status.textContent=prefix+' · '+fmt(msg.url)+' · '+fmt(msg.title);
    status.classList.add('live');
    if(msg.authenticated)refresh().catch(()=>{})
   }else if(msg.type==='error'){
    status.textContent=msg.error||'实时浏览器错误'
   }
  };
  ws.onerror=()=>{if(!settled){settled=true;clearTimeout(timer);reject(new Error('实时浏览器连接失败'))}};
  ws.onclose=async ev=>{
   if(generation!==accountStreamGeneration)return;
   accountStream=null;status.classList.remove('live');
   if(ev.code===4401){showLogin();status.textContent='管理员会话已过期';return}
   if(ev.code===4409&&recovery<1){
    status.textContent='网页会话已重建，正在恢复实时连接…';
    try{
     await api('/operator/api/accounts/'+encodeURIComponent(provider)+'/connect',{method:'POST',body:'{}'});
     await openAccountStream(provider,recovery+1)
    }catch(e){status.textContent=e.message;flash(e.message,true)}
    return
   }
   status.textContent='实时浏览器连接已断开'
  }
 })
}
async function beginAccountConnect(row){
 activeAccountProvider=row.provider_id;selectPanel('accounts');
 el('accountLoginShell').classList.remove('hidden');el('accountLoginTitle').textContent=row.label+' · 实时服务器网页会话';
 el('localBrowserStatus').textContent='正在创建 Ferro 服务器上的网页会话…';
 await api('/operator/api/accounts/'+encodeURIComponent(row.provider_id)+'/connect',{method:'POST',body:'{}'});
 await openAccountStream(row.provider_id);
 await refresh()
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
el('accountLoginClose').onclick=async()=>{
 const provider=activeAccountProvider;closeAccountStream();activeAccountProvider='';
 try{if(provider)await api('/operator/api/accounts/'+encodeURIComponent(provider)+'/login/stop',{method:'POST',body:'{}'})}catch{}
 el('accountLoginShell').classList.add('hidden');el('liveBrowserCanvas').classList.add('hidden');
 el('localBrowserStatus').textContent='已关闭。网页登录态仍保存在 Ferro 的持久化浏览器中。'
};
el('liveBrowserReconnect').onclick=async()=>{
 if(!activeAccountProvider)return;
 try{await openAccountStream(activeAccountProvider)}catch(e){flash(e.message,true)}
};
const liveCanvas=el('liveBrowserCanvas');
let livePointer=null;
liveCanvas.onpointerdown=ev=>{
 const r=liveCanvas.getBoundingClientRect();livePointer={x:ev.clientX,y:ev.clientY,rx:(ev.clientX-r.left)/r.width,ry:(ev.clientY-r.top)/r.height};
 try{liveCanvas.setPointerCapture(ev.pointerId)}catch{}
};
liveCanvas.onpointerup=ev=>{
 if(!livePointer)return;
 const r=liveCanvas.getBoundingClientRect(),dx=ev.clientX-livePointer.x,dy=ev.clientY-livePointer.y;
 try{
  if(Math.abs(dx)<12&&Math.abs(dy)<12)accountStreamSend({type:'click',x_ratio:livePointer.rx,y_ratio:livePointer.ry});
  else accountStreamSend({type:'wheel',x_ratio:(ev.clientX-r.left)/r.width,y_ratio:(ev.clientY-r.top)/r.height,delta_x:-dx*3,delta_y:-dy*3})
 }catch(e){flash(e.message,true)}
 livePointer=null
};
liveCanvas.onpointercancel=()=>{livePointer=null};
liveCanvas.onwheel=ev=>{
 ev.preventDefault();const r=liveCanvas.getBoundingClientRect();
 try{accountStreamSend({type:'wheel',x_ratio:(ev.clientX-r.left)/r.width,y_ratio:(ev.clientY-r.top)/r.height,delta_x:ev.deltaX,delta_y:ev.deltaY})}catch(e){flash(e.message,true)}
};
el('localBrowserSend').onclick=()=>{
 const field=el('localBrowserText'),value=field.value;field.value='';if(!value)return;
 try{accountStreamSend({type:'text',text:value})}catch(e){flash(e.message,true)}
};
el('localBrowserText').onkeydown=ev=>{if(ev.key==='Enter'){ev.preventDefault();el('localBrowserSend').click()}};
for(const b of document.querySelectorAll('[data-browser-key]'))b.onclick=()=>{try{accountStreamSend({type:'key',key:b.dataset.browserKey})}catch(e){flash(e.message,true)}};
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
