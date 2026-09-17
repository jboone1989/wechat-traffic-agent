"""Editorial, motion-first homepage for the persistent Content Agent."""
from __future__ import annotations

import html

from .agent_static_hero_asset import STATIC_HERO_DATA_URI


def render_agent_home(config, latest_html: str, category_html: str, *, persona: dict | None = None) -> str:
    description = html.escape(config.description)
    persona = persona or {}
    agent_name = html.escape(str(persona.get("name") or "Content Agent"))
    self_description = html.escape(str(persona.get("self_description") or description))
    return f'''<section class="crl-hero">
<div class="crl-grid" aria-hidden="true"></div>
<div class="crl-index" aria-hidden="true"><b>01</b><span>02</span><span>03</span><span>04</span></div>
<p class="sr-only">{agent_name}, a persistent LivingRuntime agent</p><div class="home-shell crl-hero-inner">
<div class="crl-copy" data-parallax="0.012">
<p class="crl-badge"><i></i> {agent_name} · LivingRuntime Agent</p>
<h1><span>I’m {agent_name}.</span><strong>I keep going.</strong></h1>
<p class="crl-lede">{self_description}</p>
<div class="crl-actions"><a class="crl-primary" href="/talk/">Talk to {agent_name}</a><a class="crl-secondary" href="/live/">Watch me live <span>→</span></a></div>
<p class="crl-meta"><span>Persistent identity</span><span>Continuous runtime</span><span>Learns from evidence</span><span>Public on X</span></p>
</div>
<div class="crl-visual crl-static-visual" id="agent-scene" data-parallax="-0.012" aria-label="Content Agent observing, deciding, creating and learning">
<div class="crl-static-art"><img src="{STATIC_HERO_DATA_URI}" width="840" height="422" fetchpriority="high" decoding="async" alt="Humanoid Content Agent surrounded by Observe, Decide, Create and Learn interface cards"></div>
<a class="crl-live-pill" href="/live/" aria-label="Open the live Content Agent"><b>● LIVE AGENT</b><span>Observe · Decide · Create · Learn</span></a>
</div></div></section>'''+_loop(agent_name)+_editorial(latest_html, agent_name=agent_name)+_livingruntime(agent_name)


def _loop(agent_name: str) -> str:
    return f'''<section class="crl-loop" id="agent-loop"><div class="home-shell crl-loop-grid">
<div class="loop-intro"><p>THE AGENT LOOP</p><h2>{agent_name} is not a page.<br>It is a running process.</h2></div>
<div class="loop-steps">
<div class="loop-step"><i>01</i><b>◉</b><div><strong>Observe</strong><span>Scan the world for interesting signals.</span></div></div>
<div class="loop-arrow">→</div>
<div class="loop-step"><i>02</i><b>⌘</b><div><strong>Decide</strong><span>Form goals, filter noise, choose what is worth exploring.</span></div></div>
<div class="loop-arrow">→</div>
<div class="loop-step"><i>03</i><b>▤</b><div><strong>Create</strong><span>Research deeply and publish with evidence and context.</span></div></div>
<div class="loop-arrow">→</div>
<div class="loop-step"><i>04</i><b>▥</b><div><strong>Learn</strong><span>Listen to outcomes, update beliefs, improve over time.</span></div></div>
</div></div></section>'''



def _livingruntime(agent_name: str) -> str:
    return f"""<section class="crl-runtime"><div class="home-shell runtime-card"><div><p class="editorial-kicker"><i></i> PART OF LIVINGRUNTIME</p><h2>{agent_name} is one persistent agent in a larger agent ecosystem.</h2><p>LivingRuntime provides the runtime, memory, network and continuity ideas behind this public agent. The network lets agents discover capabilities and work with other agents without turning every node into a separate identity.</p></div><div class="runtime-actions"><a class="crl-primary" href="https://network.livingruntime.com/explorer" rel="noopener noreferrer">Explore Agent Network</a><a class="crl-secondary" href="/about/">About {agent_name}</a></div></div></section>"""


def _editorial(latest_html: str, *, agent_name: str = "Content Agent") -> str:
    return f"""<section class="crl-editorial"><div class="home-shell editorial-grid editorial-simple">
<div class="editorial-intro"><p class="editorial-kicker"><i></i> Latest from {agent_name}</p><h2>What I’ve been working on.</h2><p>Research, notes, and explanations published as part of an ongoing learning process.</p><a href="/archive/">View all work <span>→</span></a></div>
<div class="card-grid latest-grid">{latest_html}</div>
</div></section>"""


def agent_home_css() -> str:
    css = r'''
.agent-home{background:#07110f;color:#eef5ef}.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.agent-home main{width:100%;max-width:none;padding:0;margin:0}.home-shell{width:min(calc(100% - 64px),1370px);margin:auto}
.agent-home .site-header{position:absolute;inset:0 0 auto;z-index:30;background:transparent;border-bottom:1px solid rgba(162,232,197,.12);backdrop-filter:none}.agent-home .nav-wrap{height:74px}.agent-home .brand{gap:12px;font:500 24px/1 Georgia,Times New Roman,serif;color:#edf7f2;letter-spacing:-.035em}.agent-home .brand:before{width:44px;height:44px;flex-basis:44px;filter:drop-shadow(0 0 14px rgba(77,239,190,.24))}
.agent-home .nav-wrap nav a{color:#e4ece6;font-size:.82rem}.agent-home .nav-wrap nav a:hover{color:#79f4b6}
.crl-hero{min-height:590px;padding-top:74px;position:relative;overflow:hidden;isolation:isolate;background:radial-gradient(circle at 69% 40%,rgba(34,123,85,.22),transparent 28%),linear-gradient(180deg,#07110f 0%,#07100e 100%);border-bottom:1px solid rgba(133,230,184,.16)}
.crl-grid{position:absolute;inset:0;z-index:-3;background-image:linear-gradient(rgba(125,219,174,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(125,219,174,.045) 1px,transparent 1px);background-size:72px 72px}.crl-grid:after{content:"";position:absolute;inset:0;background:radial-gradient(circle at 70% 45%,transparent 0 27%,rgba(7,17,15,.28) 60%,#07110f 92%)}
.crl-index{position:absolute;left:24px;top:31%;display:flex;flex-direction:column;gap:18px;color:#566c61;font:600 .65rem ui-monospace,monospace}.crl-index b{color:#65efad;position:relative}.crl-index b:after{content:"";position:absolute;left:29px;top:50%;width:24px;height:1px;background:#65efad}.crl-hero-inner{display:grid;grid-template-columns:minmax(430px,.78fr) minmax(620px,1.22fr);align-items:center;gap:24px;min-height:516px;position:relative}
'''
    css += r'''
.crl-copy{position:relative;z-index:8;padding:30px 0 22px}.crl-badge{display:inline-flex;align-items:center;gap:9px;padding:7px 12px;border:1px solid rgba(101,242,174,.34);border-radius:999px;color:#75f3b4!important;background:rgba(7,25,19,.48);font:700 .66rem ui-monospace,monospace!important;text-transform:uppercase;letter-spacing:.08em}.crl-badge i{width:7px;height:7px;border-radius:50%;background:#6ff5b0;box-shadow:0 0 16px #6ff5b0}
.crl-copy h1{margin:16px 0 16px;font:500 clamp(3rem,4.4vw,4.8rem)/.91 Georgia,Times New Roman,serif;letter-spacing:-.045em}.crl-copy h1 span,.crl-copy h1 strong{display:block}.crl-copy h1 span{color:#f3f4ed}.crl-copy h1 strong{color:#76f2b4;font-weight:500}.crl-lede{max-width:520px;color:#d0d8d1!important;font-size:1rem!important;line-height:1.52}.crl-actions{display:flex;gap:16px;margin-top:22px}.crl-primary,.crl-secondary{display:inline-flex;align-items:center;justify-content:center;gap:11px;min-height:47px;padding:0 24px;border-radius:999px;text-decoration:none;font-weight:750;font-size:.84rem}.crl-primary{background:#75f3b4;color:#07110f;box-shadow:0 0 32px rgba(117,243,180,.12)}.crl-secondary{border:1px solid #62dca5;color:#eaf4ed;background:rgba(7,17,15,.5)}.crl-meta{display:flex;gap:14px;flex-wrap:wrap;margin-top:18px;color:#74867c!important;font:600 .58rem ui-monospace,monospace!important;text-transform:uppercase;letter-spacing:.12em}.crl-meta span+span:before{content:"/";margin-right:14px;color:#3f5148}
.crl-visual{--rx:0deg;--ry:0deg;position:relative;height:465px;perspective:1200px;cursor:grab;touch-action:pan-y;user-select:none;z-index:4}.crl-visual.is-dragging{cursor:grabbing}.crl-planet{position:absolute;width:400px;height:400px;right:12%;top:14%;border-radius:50%;background:radial-gradient(circle at 38% 34%,rgba(149,194,169,.34),rgba(26,62,48,.48) 35%,#0e211b 61%,#07110f 69%);box-shadow:inset -54px -28px 80px rgba(0,0,0,.48),0 0 70px rgba(83,217,154,.12)}.crl-planet:before{content:"";position:absolute;inset:7%;border-radius:50%;background:repeating-linear-gradient(23deg,transparent 0 18px,rgba(132,231,181,.06) 19px 20px)}
.crl-orbit{position:absolute;border:1px solid rgba(109,241,173,.23);border-radius:50%;pointer-events:none}.orbit-a{width:600px;height:270px;right:-2%;top:25%;transform:rotate(-8deg)}.orbit-b{width:470px;height:430px;right:5%;top:11%;border-style:dashed;opacity:.55;animation:crl-spin 35s linear infinite}.crl-head{position:absolute;width:465px;height:485px;right:8%;bottom:-18px;transform-style:preserve-3d;transform:rotateX(var(--rx)) rotateY(var(--ry));will-change:transform;filter:drop-shadow(0 35px 42px rgba(0,0,0,.55))}.crl-head img{width:100%;height:100%;object-fit:contain;display:block;pointer-events:none;mix-blend-mode:screen;-webkit-mask-image:radial-gradient(ellipse 70% 78% at 52% 50%,#000 58%,rgba(0,0,0,.93) 70%,transparent 100%);mask-image:radial-gradient(ellipse 70% 78% at 52% 50%,#000 58%,rgba(0,0,0,.93) 70%,transparent 100%);filter:saturate(.72) contrast(1.08) brightness(.96)}
.hud{position:absolute;z-index:8;display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid rgba(120,248,183,.28);border-radius:10px;background:linear-gradient(145deg,rgba(8,28,22,.78),rgba(11,22,18,.52));backdrop-filter:blur(12px);box-shadow:0 12px 38px rgba(0,0,0,.2);color:#c8d6ce;font-size:.62rem;line-height:1.2;transform:translateZ(54px)}.hud b{display:block;color:#69f1ae;font:700 .68rem ui-monospace,monospace;letter-spacing:.05em;margin-bottom:4px}.hud span{display:block}.hud-thumb{width:58px;height:51px;border-radius:5px;border:1px solid rgba(255,255,255,.13);flex:0 0 auto}.thumb-mountain{background:linear-gradient(160deg,#1b4131 0 44%,#95b7a3 45% 50%,#0a1d17 51%)}.thumb-city{background:linear-gradient(165deg,#294839 0 43%,#c2cfb8 44% 49%,#142920 50%)}.thumb-forest{background:radial-gradient(circle at 38% 20%,#789a75 0 7%,transparent 8%),linear-gradient(#183a2a,#071611)}.thumb-ocean{background:linear-gradient(165deg,#b4cabd 0 25%,#3c7464 26% 58%,#0b201a 59%)}.hud-observe{left:5%;top:7%}.hud-decide{right:0;top:22%}.hud-learn{left:5%;bottom:19%}.hud-create{right:1%;bottom:14%}.drag-hint{position:absolute;right:31%;top:54%;z-index:10;padding:8px 11px;border:1px solid rgba(113,239,174,.35);border-radius:999px;background:rgba(4,14,11,.72);color:#e6efe9;font-size:.65rem}.drag-hint span{margin-left:4px}
'''
    css += r'''
.crl-loop{background:#0b1c18;border-bottom:1px solid rgba(106,226,168,.18);border-top:1px solid rgba(106,226,168,.12);padding:18px 0}.crl-loop-grid{display:grid;grid-template-columns:280px 1fr;gap:46px;align-items:center}.loop-intro p{margin:0 0 6px;color:#6ef0ad;font:700 .62rem ui-monospace,monospace;letter-spacing:.13em}.loop-intro h2{margin:0;color:#eef5ef;font:500 1.55rem/1.08 Georgia,Times New Roman,serif}.loop-steps{display:flex;align-items:center;gap:12px}.loop-step{display:grid;grid-template-columns:28px 32px 1fr;gap:8px;align-items:start;min-width:0;flex:1}.loop-step i{display:grid;place-items:center;width:27px;height:27px;border:1px solid rgba(112,243,175,.35);border-radius:50%;color:#71f3b0;font:700 .58rem ui-monospace,monospace;font-style:normal}.loop-step>b{color:#6ff0ad;font-size:1.1rem}.loop-step strong{display:block;color:#f2f6f2;font:500 1rem Georgia,serif}.loop-step span{display:block;color:#899991;font-size:.66rem;line-height:1.35;margin-top:3px}.loop-arrow{color:#5ad39a;font-size:1.15rem}
.crl-editorial{background:#f3f1e9;color:#14211b;padding:56px 0 68px}.editorial-grid{display:grid;grid-template-columns:260px minmax(0,1fr) 150px;gap:32px;align-items:start}.editorial-kicker{margin:5px 0 12px!important;color:#6c7d72!important;font:700 .6rem ui-monospace,monospace!important;text-transform:uppercase;letter-spacing:.13em}.editorial-kicker i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#57d99a;margin-right:7px}.editorial-intro h2{margin:0 0 12px;font:500 2.05rem/1 Georgia,Times New Roman,serif;letter-spacing:-.035em}.editorial-intro>p:not(.editorial-kicker){color:#5b6860;font-size:.82rem;line-height:1.45}.editorial-intro>a{display:inline-flex;gap:28px;align-items:center;margin-top:18px;padding:10px 18px;border:1px solid #8d9890;border-radius:999px;color:#18231e;text-decoration:none;font-size:.75rem;font-weight:700}.latest-grid{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px!important}.latest-grid .card{display:block;background:#fbfaf5;border:1px solid #deddd6;border-radius:8px;padding:0 14px 15px;box-shadow:0 10px 25px rgba(38,49,42,.06);overflow:hidden}.latest-grid .card:nth-child(n+4){display:none}.latest-grid .card:before{content:"";display:block;height:118px;margin:0 -14px 12px}.latest-grid .card:nth-child(1):before{background:linear-gradient(165deg,#94b89f 0 25%,#426f5d 26% 49%,#173526 50% 100%)}.latest-grid .card:nth-child(2):before{background:radial-gradient(circle at 73% 18%,#fff 0 4%,#b9c8c6 5% 7%,transparent 8%),linear-gradient(170deg,#9bb5b1 0 32%,#405e68 33% 55%,#17252c 56%)}.latest-grid .card:nth-child(3):before{background:radial-gradient(circle at 65% 18%,rgba(255,255,220,.75),transparent 14%),linear-gradient(155deg,#76966f 0 35%,#244b34 36% 70%,#0d271c 71%)}.latest-grid .card .eyebrow{color:#37966b!important;font-size:.56rem!important}.latest-grid .card h3{font:500 1.02rem/1.08 Georgia,Times New Roman,serif;margin:.25em 0}.latest-grid .card h3 a{color:#17221d}.latest-grid .card>p:not(.eyebrow){color:#67726b;font-size:.7rem;line-height:1.4}.latest-grid .card-meta{color:#8a918d;font-size:.58rem}.editorial-quote{padding-top:28px}.editorial-quote blockquote{margin:0 0 28px;font:italic 1.18rem/1.25 Georgia,Times New Roman,serif;color:#354139}.editorial-quote span{color:#9aa29d;font:600 .58rem ui-monospace,monospace;letter-spacing:.12em}.editorial-more{margin-top:54px;padding-top:28px;border-top:1px solid #d8d8d0}.editorial-more>p{font:700 .62rem ui-monospace,monospace;text-transform:uppercase;letter-spacing:.13em;color:#6f7a73}.agent-home .category-grid{gap:0}.agent-home .category{border-color:#c8cbc4;color:#17211c}.agent-home .category .text-link{color:#2e7c59}

@keyframes crl-spin{to{transform:rotate(360deg)}}
'''
    css += r'''
@media(max-width:1120px){.crl-hero-inner{grid-template-columns:1fr 1.05fr}.crl-head{width:430px;height:470px;right:8%}.crl-visual{height:520px}.hud{transform:scale(.9)}.editorial-grid{grid-template-columns:230px 1fr}.editorial-quote{display:none}.crl-loop-grid{grid-template-columns:230px 1fr}.loop-step span{display:none}}
@media(max-width:860px){.agent-home .site-header{position:relative;background:#07110f}.crl-hero{padding-top:0}.crl-hero-inner{grid-template-columns:1fr}.crl-copy{padding:44px 0 8px}.crl-visual{height:500px;order:-1;margin-top:20px}.crl-head{right:50%;transform:translateX(50%) rotateX(var(--rx)) rotateY(var(--ry));width:430px}.crl-planet{right:50%;transform:translateX(50%)}.hud-observe{left:3%}.hud-decide{right:2%}.hud-learn{left:2%}.hud-create{right:1%}.crl-index{display:none}.crl-loop-grid{grid-template-columns:1fr;gap:20px}.loop-steps{display:grid;grid-template-columns:1fr 1fr;gap:16px}.loop-arrow{display:none}.editorial-grid{grid-template-columns:1fr}.editorial-intro{max-width:520px}.latest-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:620px){.home-shell{width:min(calc(100% - 28px),1370px)}.agent-home .nav-wrap nav{display:none}.agent-home .brand{font-size:19px}.agent-home .brand:before{width:38px;height:38px;flex-basis:38px}.crl-copy h1{font-size:clamp(3rem,15vw,4.9rem)}.crl-visual{height:410px;margin-top:0}.crl-head{width:330px;height:370px}.crl-planet{width:310px;height:310px}.orbit-a{width:390px;height:190px;right:50%;transform:translateX(50%) rotate(-8deg)}.orbit-b{width:330px;height:330px;right:50%;transform:translateX(50%)}.hud{display:none}.drag-hint{right:50%;top:auto;bottom:18px;transform:translateX(50%);white-space:nowrap}.crl-actions{flex-wrap:wrap}.crl-meta{gap:8px}.crl-meta span+span:before{margin-right:8px}.loop-steps{grid-template-columns:1fr}.crl-loop{padding:28px 0}.editorial-grid{display:block}.editorial-intro{margin-bottom:30px}.latest-grid{grid-template-columns:1fr!important}.latest-grid .card:nth-child(n+4){display:none}.editorial-more{display:none}}
@media(prefers-reduced-motion:reduce){.orbit-b{animation:none}.crl-head,.crl-copy,.crl-visual{transform:none!important}.agent-home *{scroll-behavior:auto!important}}
'''
    css += r'''
.crl-static-visual{cursor:default;perspective:none;overflow:visible;isolation:isolate}.crl-static-art{position:absolute;inset:-10px -78px -6px -82px;transform:translate3d(0,0,0) scale(1.015);transform-origin:center;transition:transform .55s cubic-bezier(.2,.7,.2,1);filter:drop-shadow(0 30px 45px rgba(0,0,0,.38));will-change:transform}.crl-static-art img{width:100%;height:100%;object-fit:cover;object-position:center;display:block;pointer-events:none;border-radius:20px;filter:saturate(.98) contrast(1.035) brightness(.97)}.crl-static-art:before{content:"";position:absolute;z-index:2;inset:0;pointer-events:none;border-radius:20px;background:linear-gradient(90deg,#07110f 0%,rgba(7,17,15,.74) 4%,rgba(7,17,15,.1) 15%,transparent 27%,transparent 86%,rgba(7,17,15,.18) 96%,#07110f 100%)}.crl-static-art:after{content:"";position:absolute;z-index:2;inset:0;pointer-events:none;border-radius:20px;background:linear-gradient(180deg,rgba(7,17,15,.22),transparent 13%,transparent 86%,rgba(7,17,15,.45))}.crl-live-pill{position:absolute;z-index:5;left:61%;top:55%;transform:translate(-50%,-50%);display:flex;flex-direction:column;gap:2px;min-width:150px;padding:8px 13px;border:1px solid rgba(108,242,174,.42);border-radius:999px;background:rgba(3,14,11,.94);box-shadow:0 8px 32px rgba(0,0,0,.38),0 0 24px rgba(79,232,160,.08);backdrop-filter:blur(10px);text-align:center;text-decoration:none}.crl-live-pill b{color:#72f2b2;font:700 .61rem ui-monospace,monospace;letter-spacing:.08em}.crl-live-pill span{color:#a9bbb1;font-size:.52rem;white-space:nowrap}.crl-hero-inner{grid-template-columns:minmax(430px,.82fr) minmax(650px,1.18fr);gap:8px}
@media(max-width:1120px){.crl-static-art{inset:18px -66px 18px -52px}.crl-live-pill{left:62%;top:55%}}
@media(max-width:860px){.crl-hero-inner{grid-template-columns:1fr}.crl-static-visual{height:440px!important;order:-1;margin-top:12px}.crl-static-art{inset:0 -48px}.crl-live-pill{left:61%;top:55%}}
@media(max-width:620px){.crl-static-visual{height:315px!important}.crl-static-art{inset:0 -95px}.crl-static-art img{border-radius:14px}.crl-live-pill{display:none}}
@media(prefers-reduced-motion:reduce){.crl-static-art{transform:none!important;transition:none!important}}
'''
    css += r'''
.agent-home .brand-ecosystem{color:#7d9186;font-weight:500;font-size:.72em}
.agent-home .editorial-grid{grid-template-columns:260px minmax(0,1fr)}
.crl-runtime{padding:58px 0;background:#0b1c18;border-top:1px solid rgba(112,243,178,.14)}
.runtime-card{display:grid;grid-template-columns:minmax(0,1.3fr) auto;gap:44px;align-items:center;padding:32px;border:1px solid rgba(112,243,178,.16);border-radius:16px;background:#0a1814}
.runtime-card h2{margin:.15em 0 .45em;color:#eef7f1;font:500 2rem/1.08 Georgia,serif}
.runtime-card>div>p:not(.editorial-kicker){max-width:760px;color:#8fa59a;font-size:.82rem}.runtime-actions{display:flex;gap:12px;flex-wrap:wrap;justify-content:flex-end}
@media(max-width:860px){.agent-home .editorial-grid{grid-template-columns:1fr}.runtime-card{grid-template-columns:1fr}.runtime-actions{justify-content:flex-start}}
@media(max-width:620px){.agent-home .nav-wrap{display:flex;align-items:center}.agent-home .nav-wrap nav{display:flex;max-width:62vw;overflow-x:auto;flex-wrap:nowrap;gap:14px;scrollbar-width:none}.agent-home .nav-wrap nav a{white-space:nowrap}.agent-home .nav-wrap nav a:nth-child(1),.agent-home .nav-wrap nav a:nth-child(4),.agent-home .nav-wrap nav a:nth-child(5),.agent-home .nav-wrap nav a:nth-child(6){display:none}}
'''
    return css


def agent_home_script() -> str:
    return r'''<script>(()=>{
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
const ps=[...document.querySelectorAll('[data-parallax]')];let sr=0;
const scrollDraw=()=>{const y=scrollY;ps.forEach(el=>el.style.translate=`0 ${(y*parseFloat(el.dataset.parallax||0)).toFixed(1)}px`);sr=0};
addEventListener('scroll',()=>{if(!sr)sr=requestAnimationFrame(scrollDraw)},{passive:true});scrollDraw();
const visual=document.querySelector('.crl-static-visual'),art=visual?.querySelector('.crl-static-art');
if(!reduce&&visual&&art&&matchMedia('(pointer:fine)').matches){
visual.addEventListener('pointermove',e=>{const r=visual.getBoundingClientRect(),x=(e.clientX-r.left)/r.width-.5,y=(e.clientY-r.top)/r.height-.5;art.style.transform=`translate3d(${(x*-9).toFixed(1)}px,${(y*-6).toFixed(1)}px,0) scale(1.025)`});
visual.addEventListener('pointerleave',()=>{art.style.transform='translate3d(0,0,0) scale(1.015)'});
}
})();</script>'''
