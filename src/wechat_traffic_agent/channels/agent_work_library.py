"""Interactive, client-side navigation for Ferro's static Work archive."""
from __future__ import annotations


def work_library_css() -> str:
    return r'''
.work-page{background:#f4f5f1}.work-page main{width:min(calc(100% - 40px),1240px);padding-top:34px}.work-library{padding-bottom:28px}
.work-hero{position:relative;overflow:hidden;padding:38px 42px;margin-bottom:22px;border-radius:18px;background:linear-gradient(135deg,#07130f,#0d241b);color:#eef7f1;border:1px solid rgba(112,243,178,.18)}
.work-hero:after{content:"";position:absolute;width:360px;height:360px;border-radius:50%;right:-120px;top:-190px;background:radial-gradient(circle,rgba(112,243,178,.12),transparent 67%)}
.work-hero .eyebrow{color:#70f3b2!important}.work-hero h1{position:relative;z-index:1;max-width:820px;margin:.15em 0;font:500 clamp(2.5rem,5vw,4.6rem)/.96 Georgia,serif;letter-spacing:-.045em}
.work-hero>p:last-child{position:relative;z-index:1;max-width:760px;margin:12px 0 0;color:#9fb4a9;font-size:.95rem;line-height:1.55}
.work-search-sticky{position:sticky;top:78px;z-index:4;margin-bottom:10px;padding:10px 12px;border:1px solid #d8ded9;border-radius:14px;background:rgba(251,252,248,.96);backdrop-filter:blur(14px);box-shadow:0 8px 28px rgba(25,44,34,.07)}
.work-controls{display:grid;grid-template-columns:minmax(220px,1fr) minmax(160px,.36fr);gap:10px;padding:0 2px}
.work-search,.work-select{display:grid;gap:5px}.work-search>span,.work-select>span{color:#607067;font:700 .56rem ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase}
.work-search input,.work-select select{width:100%;height:44px;padding:0 13px;border:1px solid #cfd7d1;border-radius:9px;background:#fff;color:#142019;font:600 .78rem system-ui;outline:none}
.work-search input:focus,.work-select select:focus{border-color:#4acb8d;box-shadow:0 0 0 3px rgba(74,203,141,.1)}
.work-kind-row{display:flex;gap:8px;flex-wrap:wrap;padding:17px 0 12px}.work-kind{border:1px solid #cfd8d2;border-radius:999px;background:#fff;color:#536159;padding:8px 13px;font:700 .68rem system-ui;cursor:pointer}
.work-kind span{margin-left:5px;color:#89978f}.work-kind.active{border-color:#153c2c;background:#0b231a;color:#ecf8f1}.work-kind.active span{color:#7deeb5}
.work-result-bar{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:5px 2px 14px;border-bottom:1px solid #dbe1dc;color:#65736a;font-size:.76rem}
.work-result-bar strong{color:#1e3027}.work-result-bar button{border:0;background:none;color:#277653;font-weight:750;cursor:pointer}
.work-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding-top:18px}
.work-item{display:flex;flex-direction:column;min-height:270px;padding:22px;border:1px solid #d9dfda;border-radius:14px;background:#fff;box-shadow:0 8px 26px rgba(23,45,33,.04)}
.work-item[hidden]{display:none}.work-item-meta{display:flex;justify-content:space-between;align-items:center;gap:15px;color:#829087;font:650 .58rem ui-monospace,monospace;letter-spacing:.04em}.work-type{color:#238158}
.work-item h2{margin:12px 0 9px;font:500 1.48rem/1.12 Georgia,serif;letter-spacing:-.02em}.work-item h2 a{color:#17251e;text-decoration:none}
.work-item h2 a:hover{text-decoration:underline;text-decoration-color:#62dca5}.work-item>p{margin:0;color:#68766e;font-size:.78rem;line-height:1.52}
.work-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}.work-tag,.work-tag-more{border:1px solid #dbe3dd;border-radius:999px;background:#f7f9f6;color:#607068;padding:4px 8px;font:650 .56rem system-ui}
.work-tag{cursor:pointer}.work-tag:hover{border-color:#79cda3;color:#196843}.work-tag-more{border-style:dashed}
.work-open{display:flex;justify-content:space-between;align-items:center;margin-top:auto;padding-top:18px;color:#176c47;text-decoration:none;font-size:.72rem;font-weight:800}
.work-empty{margin:28px 0;padding:50px 24px;border:1px dashed #bdc9c1;border-radius:14px;text-align:center;color:#65766c;background:#f9faf7}.work-empty strong{display:block;color:#203029;font:500 1.5rem Georgia,serif}.work-empty p{margin:.5em 0}
.work-noscript{padding:12px;color:#7b887f;font-size:.72rem}
@media(max-width:820px){.work-page main{width:min(calc(100% - 28px),1240px)}.work-search-sticky{top:92px}.work-controls{grid-template-columns:1fr 1fr}.work-grid{grid-template-columns:1fr}.work-hero{padding:28px}.work-item{min-height:230px}}
@media(max-width:560px){.work-search-sticky{top:76px;margin-left:-4px;margin-right:-4px}.work-controls{grid-template-columns:1fr}.work-hero{padding:24px 20px}.work-hero h1{font-size:2.7rem}.work-kind-row{overflow:auto;flex-wrap:nowrap;padding-bottom:10px}.work-kind{white-space:nowrap}.work-item{padding:18px}}
'''


def work_library_script() -> str:
    return r'''<script>(()=>{
const root=document.querySelector('[data-work-library]');if(!root)return;
const search=document.getElementById('work-search'),topic=document.getElementById('work-topic'),sort=document.getElementById('work-sort'),grid=document.getElementById('work-grid'),count=document.getElementById('work-count'),empty=document.getElementById('work-empty'),clear=document.getElementById('work-clear');
const kindButtons=[...document.querySelectorAll('[data-kind]')],items=[...document.querySelectorAll('[data-work-item]')];let kind='all';
const params=new URLSearchParams(location.search);if(params.get('q'))search.value=params.get('q');if(params.get('topic')&&[...topic.options].some(o=>o.value===params.get('topic')))topic.value=params.get('topic');if(params.get('sort')==='oldest')sort.value='oldest';if(params.get('kind')&&kindButtons.some(b=>b.dataset.kind===params.get('kind')))kind=params.get('kind');
function syncKinds(){kindButtons.forEach(b=>{const on=b.dataset.kind===kind;b.classList.toggle('active',on);b.setAttribute('aria-pressed',on?'true':'false')})}
function apply(){const q=search.value.trim().toLocaleLowerCase(),t=topic.value;let visible=0;
items.forEach(item=>{const okKind=kind==='all'||item.dataset.kind===kind,okTopic=t==='all'||(' '+item.dataset.topics+' ').includes(' '+t+' '),okSearch=!q||(item.dataset.search||'').includes(q),show=okKind&&okTopic&&okSearch;item.hidden=!show;if(show)visible++});
const ordered=[...items].sort((a,b)=>{const d=Date.parse(a.dataset.date)-Date.parse(b.dataset.date);return sort.value==='oldest'?d:-d});ordered.forEach(el=>grid.append(el));count.textContent=visible+(visible===1?' result':' results');empty.hidden=visible!==0;
const p=new URLSearchParams();if(search.value.trim())p.set('q',search.value.trim());if(kind!=='all')p.set('kind',kind);if(topic.value!=='all')p.set('topic',topic.value);if(sort.value!=='newest')p.set('sort',sort.value);history.replaceState(null,'',location.pathname+(p.size?'?'+p.toString():''))}
let timer;search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(apply,90)});topic.addEventListener('change',apply);sort.addEventListener('change',apply);
kindButtons.forEach(b=>b.addEventListener('click',()=>{kind=b.dataset.kind;syncKinds();apply()}));
document.querySelectorAll('[data-pick-topic]').forEach(b=>b.addEventListener('click',()=>{topic.value=b.dataset.pickTopic;apply();scrollTo({top:root.offsetTop-90,behavior:'smooth'})}));
clear.addEventListener('click',()=>{search.value='';topic.value='all';sort.value='newest';kind='all';syncKinds();apply();search.focus()});
syncKinds();apply();
})();</script>'''


__all__ = ["work_library_css", "work_library_script"]
