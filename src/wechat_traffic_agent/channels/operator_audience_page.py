from __future__ import annotations


def operator_audience_document() -> str:
    return r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audience Analytics · Ferro Operator</title>
<style>
:root{color-scheme:dark;--bg:#0b0f12;--panel:#11171c;--panel2:#151d23;--line:#27323b;--text:#edf4f7;--muted:#8797a3;--green:#78efb3;--cyan:#83d6e9;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}a{color:inherit}main{width:min(calc(100% - 28px),1180px);margin:auto;padding:22px 0 70px}.top{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:18px}.top h1{margin:0;font-size:1.25rem}.top p{margin:5px 0 0;color:var(--muted);font-size:.78rem}.actions{display:flex;gap:8px;flex-wrap:wrap}.actions a,.actions button{border:1px solid var(--line);background:#12191f;color:#dfe8ed;border-radius:9px;padding:9px 12px;text-decoration:none;cursor:pointer}.actions button.active{border-color:#4e9674;color:var(--green)}
.notice{padding:11px 13px;border:1px solid #26333a;background:#10171b;border-radius:10px;color:#9eafb8;font-size:.75rem;margin-bottom:14px}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}.metric,.card{border:1px solid var(--line);background:var(--panel);border-radius:12px}.metric{padding:14px}.metric span{display:block;color:var(--muted);font-size:.67rem}.metric b{display:block;margin-top:7px;font-size:1.5rem}.grid{display:grid;grid-template-columns:1.15fr .85fr;gap:10px;margin-top:10px}.card{padding:15px;min-width:0}.card h2{font-size:.9rem;margin:0 0 12px}.rows{display:grid;gap:8px}.row{display:grid;grid-template-columns:minmax(110px,1fr) 2fr 60px;gap:8px;align-items:center;font-size:.72rem}.bar{height:7px;border-radius:99px;background:#0a0e11;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,#39785d,var(--green))}.mono{font-family:ui-monospace,SFMono-Regular,monospace}.muted{color:var(--muted)}.trend{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(10px,1fr);align-items:end;height:150px;gap:3px;padding-top:10px}.trend i{display:block;min-height:2px;background:#5bd59a;border-radius:3px 3px 0 0}.funnel{display:grid;gap:8px}.funnel div{display:flex;justify-content:space-between;border-bottom:1px solid #202a31;padding:8px 0;font-size:.76rem}.empty{color:var(--muted);font-size:.75rem;padding:10px 0}
@media(max-width:900px){.metrics{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}}@media(max-width:560px){main{width:calc(100% - 16px);padding-top:12px}.top{align-items:flex-start;flex-direction:column}.metrics{grid-template-columns:repeat(2,1fr)}.row{grid-template-columns:minmax(90px,1fr) 1.4fr 48px}}
</style></head><body><main>
<div class="top"><div><h1>Audience Analytics</h1><p>LivingRuntime 公共站访问与转化。只展示聚合数据。</p></div><div class="actions"><a href="/operator/">返回后台</a><a href="/operator/chat/">和 Ferro 对话</a><button data-window="24h" class="active">24h</button><button data-window="7d">7d</button><button data-window="30d">30d</button></div></div>
<div id="notice" class="notice">正在读取匿名第一方访问数据…</div>
<div class="metrics" id="metrics"></div>
<div class="grid"><section class="card"><h2>访问趋势</h2><div id="trend" class="trend"></div></section><section class="card"><h2>转化路径</h2><div id="funnel" class="funnel"></div></section></div>
<div class="grid"><section class="card"><h2>热门页面</h2><div id="pages" class="rows"></div></section><section class="card"><h2>来源</h2><div id="acquisition" class="rows"></div></section></div>
<div class="grid"><section class="card"><h2>设备</h2><div id="devices" class="rows"></div></section><section class="card"><h2>隐私边界</h2><div id="privacy" class="rows"></div></section></div>
</main><script>
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>Number(n||0).toLocaleString();
function bars(id,rows,label='count'){const root=$(id);root.innerHTML='';const max=Math.max(1,...rows.map(x=>Number(x[label]||0)));if(!rows.length){root.innerHTML='<div class="empty">暂无数据</div>';return}for(const x of rows){const name=x.route||x.name||x.stage||'unknown',count=Number(x[label]??x.visitors??0);root.insertAdjacentHTML('beforeend','<div class="row"><span class="mono">'+esc(name)+'</span><span class="bar"><i style="width:'+Math.max(2,100*count/max)+'%"></i></span><b>'+fmt(count)+'</b></div>')}}
async function load(windowName){for(const b of document.querySelectorAll('[data-window]'))b.classList.toggle('active',b.dataset.window===windowName);const r=await fetch('/operator/api/audience?window='+encodeURIComponent(windowName),{credentials:'same-origin'});if(r.status===401){location.href='/operator/';return}const d=await r.json();if(!r.ok)throw new Error(d.error||'analytics_failed');const s=d.summary||{};$('notice').textContent=d.status==='NO_COVERAGE'?'还没有第一方访问覆盖；数据会从埋点启用后开始累计。':(d.coverage_partial?'当前窗口只有部分覆盖，以下数字按已覆盖时段统计。':'当前窗口覆盖完整。');
const metrics=[['访客',s.visitors],['浏览量',s.pageviews],['会话',s.sessions],['回访',s.returning_visitors],['30s 阅读',s.engaged_readers_30s],['站内点击',s.internal_link_clicks]];$('metrics').innerHTML=metrics.map(x=>'<div class="metric"><span>'+x[0]+'</span><b>'+fmt(x[1])+'</b></div>').join('');
bars('pages',d.top_pages||[],'pageviews');bars('acquisition',d.acquisition||[]);bars('devices',d.devices||[]);const funnel=$('funnel');funnel.innerHTML=(d.funnel||[]).map(x=>'<div><span>'+esc(x.stage)+'</span><b>'+fmt(x.visitors)+'</b></div>').join('')||'<div class="empty">暂无数据</div>';
const trend=$('trend'),rows=d.trend||[],max=Math.max(1,...rows.map(x=>x.pageviews||0));trend.innerHTML=rows.map(x=>'<i title="'+esc(x.bucket)+' · '+fmt(x.pageviews)+' PV" style="height:'+Math.max(2,100*(x.pageviews||0)/max)+'%"></i>').join('')||'<div class="empty">暂无数据</div>';
const p=d.privacy||{};$('privacy').innerHTML=[['原始 IP',p.raw_ip_persisted?'保存':'不保存'],['原始 User-Agent',p.raw_user_agent_persisted?'保存':'不保存'],['原始 Referrer',p.raw_referrer_persisted?'保存':'不保存'],['访客级明细',p.visitor_level_rows_exposed?'暴露':'不暴露']].map(x=>'<div class="row"><span>'+x[0]+'</span><span></span><b>'+x[1]+'</b></div>').join('')}
for(const b of document.querySelectorAll('[data-window]'))b.onclick=()=>load(b.dataset.window).catch(e=>$('notice').textContent=e.message);
load('24h').catch(e=>$('notice').textContent=e.message);
</script></body></html>"""


__all__ = ["operator_audience_document"]
