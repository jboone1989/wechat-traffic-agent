from __future__ import annotations


def neural_life_document() -> str:
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ferro Neural Life · LivingRuntime</title>
<style>
:root{color-scheme:dark;--bg:#06100d;--panel:#091813;--panel2:#0c1d16;--ink:#eff8f2;--muted:#83988d;--green:#70f3b2;--cyan:#8ed6e8;--amber:#dfb96b;--line:rgba(112,243,178,.14);--soft:rgba(112,243,178,.06);font-family:Inter,ui-sans-serif,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 18% -8%,rgba(60,184,126,.14),transparent 28%),var(--bg);color:var(--ink)}
a{color:inherit}.shell{width:min(calc(100% - 34px),1180px);margin:auto}.top{min-height:64px;display:flex;align-items:center;gap:20px;border-bottom:1px solid var(--line)}
.brand{font:500 1.08rem Georgia,serif;text-decoration:none}.brand span{color:#70867a;font:500 .68rem system-ui}.top nav{margin-left:auto;display:flex;gap:14px}.top nav a{color:#90a59a;text-decoration:none;font-size:.72rem}
main{padding:34px 0 70px}.hero{padding:34px 36px;border:1px solid rgba(112,243,178,.22);border-radius:20px;background:linear-gradient(145deg,rgba(13,35,27,.98),rgba(6,18,14,.98))}
.k{color:var(--green);font:700 .58rem ui-monospace,monospace;letter-spacing:.14em}.hero h1{margin:10px 0 8px;font:500 clamp(2.8rem,7vw,6rem)/.9 Georgia,serif;letter-spacing:-.05em}
.hero p{max-width:820px;margin:0;color:#97aca1;font-size:.82rem;line-height:1.6}.hero-status{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}.pill{border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:#b7cbc0;font:700 .58rem ui-monospace,monospace}.pill.live{color:var(--green);border-color:rgba(112,243,178,.3)}
.section{margin-top:16px;border:1px solid var(--line);border-radius:16px;background:rgba(8,23,17,.94);overflow:hidden}.section-head{padding:18px 20px;border-bottom:1px solid var(--line);display:flex;align-items:end;justify-content:space-between;gap:18px}
.section-head h2{margin:5px 0 0;font:500 1.5rem Georgia,serif}.section-head p{max-width:630px;margin:0;color:#7e9388;font-size:.7rem;line-height:1.5}
.grid4{display:grid;grid-template-columns:repeat(4,1fr)}.metric{min-height:145px;padding:17px 18px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}.metric:nth-child(4n){border-right:0}.metric .label{color:#657b6f;font:700 .53rem ui-monospace,monospace;letter-spacing:.1em}.metric b{display:block;margin-top:8px;color:#e8f4ed;font-size:.84rem;line-height:1.35}.metric p{margin:6px 0 0;color:#7f9489;font-size:.67rem;line-height:1.45}
.bar-list{display:grid;gap:9px;margin-top:10px}.bar-row{display:grid;grid-template-columns:112px 1fr 42px;gap:9px;align-items:center;font-size:.64rem;color:#9eb1a7}.track{height:7px;border-radius:999px;background:#06100d;overflow:hidden;border:1px solid rgba(112,243,178,.08)}.fill{height:100%;background:linear-gradient(90deg,#4fa983,var(--green));border-radius:999px}
.split{display:grid;grid-template-columns:1.2fr .8fr}.panel{padding:18px 20px;min-width:0}.panel+.panel{border-left:1px solid var(--line)}.panel h3{margin:0 0 12px;font:500 1.08rem Georgia,serif}.rows{display:grid;gap:7px}.row{padding:10px 11px;border:1px solid rgba(112,243,178,.08);border-radius:10px;background:#08150f}.row b{display:block;color:#d9e7df;font-size:.71rem}.row span{display:block;margin-top:3px;color:#788f83;font-size:.63rem;line-height:1.42}.timeline{display:grid;gap:7px}.tick{display:grid;grid-template-columns:92px 130px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid rgba(112,243,178,.08);font-size:.63rem}.tick:last-child{border-bottom:0}.tick time{color:#6d8377}.tick b{color:#d5e5dc}.tick span{color:#81968b}
.lab-link{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 20px;border-top:1px solid var(--line);background:var(--soft)}.lab-link a{display:inline-flex;padding:9px 13px;border-radius:999px;background:var(--green);color:#06100d;text-decoration:none;font-size:.68rem;font-weight:800}
.roadmap{width:100%;border-collapse:collapse}.roadmap th,.roadmap td{text-align:left;padding:11px 12px;border-bottom:1px solid rgba(112,243,178,.08);vertical-align:top}.roadmap th{position:sticky;top:0;background:#0b1b15;color:#6f867a;font:700 .52rem ui-monospace,monospace;letter-spacing:.1em}.roadmap td{font-size:.66rem;color:#92a69b}.roadmap td:nth-child(1){width:54px;color:#dce9e2;font:700 .64rem ui-monospace,monospace}.roadmap td:nth-child(2){width:190px;color:#dce9e2;font-weight:700}.status{display:inline-flex;border-radius:999px;padding:4px 7px;border:1px solid #33493d;font:700 .52rem ui-monospace,monospace;white-space:nowrap}.status.live{color:var(--green);border-color:rgba(112,243,178,.28)}.status.partial{color:var(--amber);border-color:rgba(223,185,107,.26)}.status.wait{color:#879b91}.status.research{color:var(--cyan);border-color:rgba(142,214,232,.24)}
.empty{padding:12px;color:#71877b;font-size:.66rem;border:1px dashed rgba(112,243,178,.12);border-radius:9px}
.small{color:#6f8579;font-size:.61rem;line-height:1.5}.footer{padding:24px 0 40px;text-align:center;color:#60756a;font-size:.59rem}
@media(max-width:900px){.grid4{grid-template-columns:1fr 1fr}.metric:nth-child(2n){border-right:0}.split{grid-template-columns:1fr}.panel+.panel{border-left:0;border-top:1px solid var(--line)}.tick{grid-template-columns:78px 112px 1fr}.section-head{display:block}.section-head p{margin-top:8px}.roadmap{min-width:760px}.roadmap-wrap{overflow:auto}}
@media(max-width:560px){.shell{width:min(calc(100% - 20px),1180px)}.top nav a:nth-child(3){display:none}.hero{padding:25px 18px}.hero h1{font-size:3.3rem}.grid4{grid-template-columns:1fr}.metric{border-right:0}.bar-row{grid-template-columns:92px 1fr 38px}.tick{grid-template-columns:1fr;gap:3px}.lab-link{display:block}.lab-link a{margin-top:10px}.section-head,.panel{padding-left:14px;padding-right:14px}}
</style></head><body>
<header class="shell top"><a class="brand" href="/">Ferro <span>/ Neural Life</span></a><nav><a href="/live/">Live</a><a href="/brain/">Brain Lab</a><a href="/talk/">Talk</a></nav></header>
<main class="shell">
<section class="hero"><div class="k">FERRO NEURAL LIFE ROADMAP v1 · LIVE OBSERVATORY</div><h1>One brain.<br>Visible life.</h1><p>This page exposes the real persistent neural state already running inside Ferro and maps it to N0–N25. Missing evidence stays missing: a stage is not marked complete merely because a UI exists.</p><div class="hero-status" id="hero-status"><span class="pill">loading real neural state…</span></div></section>

<section class="section"><div class="section-head"><div><div class="k">N0–N5 · FROM WORLD TO ACTION</div><h2>Body → drive → mood → intention → authority</h2></div><p>The low-level state is produced outside the LLM. The cortex may interpret or plan around it, but does not own the underlying drive state.</p></div>
<div class="grid4">
<div class="metric"><div class="label">PHYSICAL CONNECTOME</div><b id="brain-count">—</b><p id="brain-detail">Loading connectome substrate.</p></div>
<div class="metric"><div class="label">CURRENT STIMULUS</div><b id="stimulus">—</b><p id="stimulus-detail">Loading recent sensory events.</p></div>
<div class="metric"><div class="label">NEURAL INTENTION</div><b id="intention">—</b><p id="intention-detail">Loading endogenous intention.</p></div>
<div class="metric"><div class="label">LAST AUTHORITY ACTION</div><b id="authority">—</b><p id="authority-detail">Loading action provenance.</p></div>
</div>
<div class="split"><div class="panel"><h3>Ferro Body / Senses</h3><div id="body-bars" class="bar-list"></div><div id="body-copy" class="small" style="margin-top:12px"></div></div><div class="panel"><h3>Survival / Propagation / Evolution</h3><div id="drive-bars" class="bar-list"></div></div></div>
<div class="split"><div class="panel"><h3>Mood now</h3><div id="affect-bars" class="bar-list"></div></div><div class="panel"><h3>Mood timeline</h3><div id="mood-timeline" class="timeline"></div></div></div>
<div class="split"><div class="panel"><h3>Id / Ego / Superego</h3><div id="psyche" class="rows"></div></div><div class="panel"><h3>Latest neural provenance</h3><div id="provenance" class="rows"></div></div></div>
</section>

<section class="section"><div class="section-head"><div><div class="k">N6–N9 · SENSORIMOTOR LIFE & DREAM</div><h2>Play, learn, replay, preconcept.</h2></div><p>Pong, Maze, Foraging and Rover use the same persistent connectome. Dream replay is neural first; language interpretation comes later.</p></div>
<div class="grid4">
<div class="metric"><div class="label">BRAIN LAB</div><b id="lab-summary">—</b><p id="lab-detail">Loading developmental worlds.</p></div>
<div class="metric"><div class="label">PONG</div><b id="pong-summary">—</b><p id="pong-detail">Loading sensorimotor evidence.</p></div>
<div class="metric"><div class="label">DREAM</div><b id="dream-summary">—</b><p id="dream-detail">Loading replay evidence.</p></div>
<div class="metric"><div class="label">PRECONCEPT</div><b id="preconcept-summary">—</b><p id="preconcept-detail">Loading dream-born associations.</p></div>
</div><div class="lab-link"><div><b>Watch Ferro play in real time</b><div class="small">The Brain Lab page renders the current game world, neural steps, reward and plasticity events.</div></div><a href="/brain/">Open Brain Lab →</a></div></section>

<section class="section"><div class="section-head"><div><div class="k">N10–N16 · TASTE, RELATIONSHIPS, HABIT, PERSONALITY</div><h2>What stays Ferro when wording changes.</h2></div><p>These states are persisted outside a personality prompt. They are visible here only when real observations have accumulated.</p></div>
<div class="split"><div class="panel"><h3>What Ferro likes</h3><div id="taste" class="rows"></div></div><div class="panel"><h3>Social relationships</h3><div id="social" class="rows"></div></div></div>
<div class="split"><div class="panel"><h3>Brain vs Cortex scoreboard</h3><div id="competition" class="rows"></div></div><div class="panel"><h3>Motor cortex & habits</h3><div id="motor" class="rows"></div></div></div>
<div class="split"><div class="panel"><h3>Personality history snapshot</h3><div id="personality" class="bar-list"></div><div id="personality-copy" class="small" style="margin-top:12px"></div></div><div class="panel"><h3>Cross-domain transfer</h3><div id="transfer" class="rows"></div></div></div>
</section>

<section class="section"><div class="section-head"><div><div class="k">N17–N25 · DEVELOPMENT, LINEAGE, REPLACEABLE CORTEX</div><h2>From MaleCNS ancestry toward Ferro’s own lineage.</h2></div><p>Ancestral neurons and Ferro-born structures are kept distinct. “SuperFly” and later stages remain evidence claims, not marketing labels.</p></div>
<div class="grid4">
<div class="metric"><div class="label">NEUROGENESIS</div><b id="neurogenesis">—</b><p id="neurogenesis-detail">Loading synthetic-neuron state.</p></div>
<div class="metric"><div class="label">DEVELOPMENT</div><b id="development">—</b><p id="development-detail">Loading lifecycle transitions.</p></div>
<div class="metric"><div class="label">CIRCUITS / ORGANS</div><b id="emergence">—</b><p id="emergence-detail">Loading emergent structures.</p></div>
<div class="metric"><div class="label">CORTEX CONTINUITY</div><b id="cortex">—</b><p id="cortex-detail">Loading replaceable-cortex evidence.</p></div>
</div><div class="split"><div class="panel"><h3>Neural lineage</h3><div id="lineage" class="rows"></div></div><div class="panel"><h3>Recent birth / maturation / pruning events</h3><div id="development-events" class="timeline"></div></div></div>
<div class="split"><div class="panel"><h3>Self-organized circuit</h3><div id="circuit-detail" class="rows"></div></div><div class="panel"><h3>Emergent neural organ</h3><div id="organ-detail" class="rows"></div></div></div>
<div class="split"><div class="panel"><h3>Developmental genome</h3><div id="genome-detail" class="rows"></div></div><div class="panel"><h3>Recent cortex swaps</h3><div id="cortex-swaps" class="timeline"></div></div></div>
</section>

<section class="section"><div class="section-head"><div><div class="k">OPEN-ENDED DEVELOPMENT · SELF-GENERATED WORLD</div><h2>Ferro noticed a weakness and built itself a training environment.</h2></div><p>This is not a hand-authored game. Developmental pressure is detected from real transfer evidence; Ferro proposes a bounded intervention and generates a new Brain Lab world without an LLM in the need/proposal loop.</p></div>
<div class="grid4">
<div class="metric"><div class="label">ACTIVE DEVELOPMENT NEEDS</div><b id="development-needs">—</b><p id="development-needs-detail">Loading neural development pressure.</p></div>
<div class="metric"><div class="label">SELF-GENERATED WORLDS</div><b id="generated-world-count">—</b><p id="generated-world-count-detail">Loading Ferro-born worlds.</p></div>
<div class="metric"><div class="label">TOP NEURAL PRESSURE</div><b id="development-pressure">—</b><p id="development-pressure-detail">Loading need pressure channels.</p></div>
<div class="metric"><div class="label">LLM IN DEVELOPMENT LOOP</div><b id="development-llm">—</b><p id="development-llm-detail">Need detection and proposals should remain neural/evidence driven.</p></div>
</div>
<div class="split"><div class="panel"><h3>Why Ferro decided it needed to develop</h3><div id="development-need-detail" class="rows"></div></div><div class="panel"><h3>The world Ferro generated</h3><div id="generated-world-detail" class="rows"></div></div></div>
</section>

<section class="section"><div class="section-head"><div><div class="k">ROADMAP EVIDENCE MATRIX</div><h2>N0–N25: what is actually visible today</h2></div><p>Status is computed from the live persisted state. LIVE means evidence is present; PARTIAL means the mechanism exists but the roadmap completion standard is not yet evidenced.</p></div>
<div class="roadmap-wrap"><table class="roadmap"><thead><tr><th>Stage</th><th>Roadmap result</th><th>Status</th><th>Evidence visible now</th></tr></thead><tbody id="roadmap-body"></tbody></table></div></section>
</main><div class="shell footer">Functional neural telemetry only; this interface makes no claim of human-like subjective experience. No fake events are generated to make the page look active.</div>
<script>
const $=id=>document.getElementById(id);
const clamp=x=>Math.max(0,Math.min(1,Number(x||0)));
const n=x=>Number(x||0);
const fmt=x=>Number(x||0).toLocaleString();
const when=x=>{try{return new Date(x).toLocaleString([], {month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'})}catch{return x||'—'}};
const text=(id,v,f='—')=>{$(id).textContent=(v===undefined||v===null||v==='')?f:String(v)};
function bars(id,obj,keys){const box=$(id);box.replaceChildren();const entries=(keys||Object.keys(obj||{})).filter(k=>obj&&obj[k]!==undefined);if(!entries.length){box.innerHTML='<div class="empty">No persisted values yet.</div>';return}for(const k of entries){const v=clamp(obj[k]);const r=document.createElement('div');r.className='bar-row';r.innerHTML='<span>'+k.replaceAll('_',' ')+'</span><div class="track"><div class="fill" style="width:'+(v*100).toFixed(1)+'%"></div></div><b>'+v.toFixed(2)+'</b>';box.append(r)}}
function rows(id,items){const box=$(id);box.replaceChildren();if(!items.length){box.innerHTML='<div class="empty">No evidence recorded yet.</div>';return}for(const [a,b] of items){const r=document.createElement('div');r.className='row';const strong=document.createElement('b');strong.textContent=a;const span=document.createElement('span');span.textContent=b||'';r.append(strong,span);box.append(r)}}
function timeline(id,items){const box=$(id);box.replaceChildren();if(!items.length){box.innerHTML='<div class="empty">No persisted history yet.</div>';return}for(const x of items){const r=document.createElement('div');r.className='tick';const t=document.createElement('time');t.textContent=when(x.at);const b=document.createElement('b');b.textContent=x.title||'event';const s=document.createElement('span');s.textContent=x.detail||'';r.append(t,b,s);box.append(r)}}
function stage(status,label,evidence){return {status,label,evidence}}
function renderRoadmap(wake,lab){
 const nl=wake.neural_life||{}, graph=wake.neural_graph||{}, sub=graph.substrate||{}, dev=sub.developmental_neurogenesis||{}, dream=wake.dreaming||lab.dreaming||{}, taste=wake.neural_taste||{}, social=wake.neural_social||{}, p=wake.personality||{}, psyche=wake.psychodynamics||{}, comp=wake.brain_vs_cortex||{}, motor=wake.neural_motor||{}, transfer=wake.neural_transfer||{}, cortex=wake.cortex_continuity||{}, agency=wake.developmental_agency||{};
 const bursts=Array.isArray(sub.recent_bursts)?sub.recent_bursts:[], history=Array.isArray(nl.history)?nl.history:[], recentContests=Array.isArray(comp.recent_contests)?comp.recent_contests:[], rels=Array.isArray(social.recent_relationships)?social.recent_relationships:[], transitions=Array.isArray(dev.recent_transitions)?dev.recent_transitions:[];
 const synthetic=n(dev.synthetic_neuron_count), circuits=n(dev.circuit_count), organs=n(dev.organ_count), swaps=n(cortex.swap_count), habits=n(motor.habit_count);
 const table=[
  ['N0','神经基线',stage(n(sub.neuron_count)>0&&bursts.length?'live':'partial','LIVE','physical neurons '+fmt(sub.neuron_count)+' · recent bursts '+bursts.length)],
  ['N1','神经身体感',stage(nl.body&&Object.keys(nl.body).length?'live':'partial','LIVE',Object.keys(nl.body||{}).length+' body/sense channels projected')],
  ['N2','Neural Drives',stage(Object.keys(nl.drives||{}).length?'live':'partial','LIVE',Object.entries(nl.drives||{}).map(([k,v])=>k+' '+n(v).toFixed(2)).join(' · ')||'no drive state')],
  ['N3','情绪系统',stage(history.length?'live':'partial','LIVE',history.length+' persisted affect snapshots')],
  ['N4','本我 → 自我',stage(psyche.id&&nl.intention?'live':'partial','LIVE',psyche.id?'Id/Ego/Superego projection exists':'awaiting conflict projection')],
  ['N5','Neural Authority',stage(nl.last_authority_action?'live':'partial','PARTIAL',nl.last_authority_action?'last neural action '+(nl.last_authority_action.task||'recorded'):'no recent authority action')],
  ['N6','Pong',stage(n(lab.total_steps)>0?'live':'partial','LIVE',fmt(lab.total_steps)+' neural lab steps · Pong hits '+n((lab.environments||{}).PONG?.hits))],
  ['N7','神经游乐场',stage(n(lab.total_sessions)>0?'live':'partial','LIVE',Object.keys(lab.environments||{}).join(' / ')||'worlds initialized')],
  ['N8','梦 / Neural Replay',stage(n(dream.dream_count)>0?'live':'partial','LIVE',n(dream.dream_count)+' dreams · LLM replay '+(dream.llm_used_for_replay?'YES':'NO'))],
  ['N9','梦 → 顿悟',stage(dream.latest_preconcept?'research':'partial','RESEARCH',dream.latest_preconcept?'preconcept '+dream.latest_preconcept.preconcept_id+' exists; downstream experiment still evidence-bound':'awaiting dream-born preconcept')],
  ['N10','神经品味',stage(n(taste.ranking_count)>0?'live':'partial','LIVE',n(taste.ranking_count)+' neural rankings')],
  ['N11','社交人格',stage(n(social.relationship_count)>0?'live':'partial','PARTIAL',n(social.relationship_count)+' persisted relationships; completion still needs longitudinal behavior proof')],
  ['N12','Id / Ego / Superego',stage(psyche.id?'live':'partial','LIVE',psyche.id?'conflict '+n(psyche.conflict).toFixed(3):'awaiting psychodynamic evidence')],
  ['N13','Neural vs LLM Competition',stage(recentContests.length?'live':'partial','PARTIAL',recentContests.length+' recent contests · future authority still outcome-dependent')],
  ['N14','无语义 Motor Cortex',stage(n(motor.motor_unit_count)>0?'live':'partial','LIVE',n(motor.motor_unit_count)+' units · semantics preassigned '+(motor.semantics_preassigned?'YES':'NO'))],
  ['N15','神经习惯',stage(habits>0?'live':'partial','PARTIAL',habits+' learned habits')],
  ['N16','个性形成',stage(n(p.observations)>0?'live':'partial','LIVE',n(p.observations)+' observations · prompt defined '+(p.prompt_defined?'YES':'NO'))],
  ['N17','Neurogenesis v1',stage(synthetic>0?'live':'partial',synthetic>0?'LIVE':'PARTIAL',fmt(synthetic)+' Ferro-born neurons · '+fmt((dev.states||{}).MATURE)+' mature')],
  ['N18','发育与死亡',stage(Object.keys(dev.states||{}).length?'partial':'wait','PARTIAL',Object.entries(dev.states||{}).map(([k,v])=>k+' '+fmt(v)).join(' · ')||'no lifecycle counts')],
  ['N19','Circuit Budding',stage(n(dev.stable_circuit_count)>0?'live':'partial',n(dev.stable_circuit_count)>0?'LIVE':'PARTIAL',circuits+' circuits · '+n(dev.stable_circuit_count)+' stable · semantic label '+((dev.circuits||[])[0]?.human_function_label||'NONE'))],
  ['N20','SuperFly',stage(String(dev.species_stage||'').toUpperCase().includes('SUPER')?'live':'partial','PARTIAL','species stage '+(dev.species_stage||'DEVELOPING')+' · synthetic '+fmt(synthetic))],
  ['N21','神经器官',stage(organs>0?'live':'partial',organs>0?'LIVE':'PARTIAL',organs+' historically grown emergent organs')],
  ['N22','跨域迁移',stage(n(transfer.settled_count)>0?'research':'partial','RESEARCH',n(transfer.settled_count)+' settled transfer experiments · causal-ready '+(transfer.causal_claim_ready?'YES':'NO'))],
  ['N23','跨物种 Developmental Priors',stage((dev.developmental_genome||{}).schema_version?'research':'wait','RESEARCH',(dev.developmental_genome||{}).schema_version?'developmental genome active; candidates must survive experiment':'awaiting developmental-prior evidence')],
  ['N24','Species Divergence',stage(n(dev.lineage_divergence_index)>0?'research':'wait','RESEARCH','divergence '+n(dev.lineage_divergence_index).toFixed(4)+' · completion requires active structure no longer describable by MaleCNS alone')],
  ['N25','LLM 可替换验证',stage(swaps>0&&String((cortex.latest_continuity||{}).status||'')==='PRESERVED'?'live':'partial',swaps>0&&String((cortex.latest_continuity||{}).status||'')==='PRESERVED'?'LIVE':'PARTIAL',swaps+' cortex swaps · '+((cortex.latest_continuity||{}).status||'baseline'))]
 ];
 const body=$('roadmap-body');body.replaceChildren();for(const [id,name,s] of table){const tr=document.createElement('tr');tr.innerHTML='<td>'+id+'</td><td>'+name+'</td><td><span class="status '+s.status+'">'+s.label+'</span></td><td></td>';tr.lastElementChild.textContent=s.evidence;body.append(tr)}
}
function render(status,lab){
 const wake=((((status||{}).life||{}).presence||{}).wakeful)||{}, nl=wake.neural_life||{}, graph=wake.neural_graph||{}, sub=graph.substrate||{}, dev=sub.developmental_neurogenesis||{}, body=nl.body||{}, drives=nl.drives||{}, affect=nl.affect||{}, intent=nl.intention||{}, authority=nl.last_authority_action||{}, agency=wake.developmental_agency||{};
 const bursts=Array.isArray(sub.recent_bursts)?sub.recent_bursts:[], latest=bursts.at(-1)||{}, dream=wake.dreaming||lab.dreaming||{}, pre=dream.latest_preconcept||null;
 $('hero-status').innerHTML=[['runtime',wake.state||'UNKNOWN'],['physical neurons',fmt(sub.neuron_count)],['Ferro-born',fmt(dev.synthetic_neuron_count)],['lab steps',fmt(lab.total_steps)],['refreshed',new Date().toLocaleTimeString()]].map(([a,b],i)=>'<span class="pill '+(i===0?'live':'')+'">'+a+' · '+b+'</span>').join('');
 text('brain-count',fmt(sub.neuron_count)+' neurons');text('brain-detail',fmt(sub.connection_count)+' directed connections · '+(sub.lifecycle||'UNKNOWN')+' · '+(sub.full_connectome?'full connectome':'development substrate'));
 const stimuli=Array.isArray(wake.recent_stimulus_kinds)?wake.recent_stimulus_kinds:[];text('stimulus',stimuli.at(-1)||'quiet');text('stimulus-detail',stimuli.length?stimuli.slice(-6).join(' · '):'No recent stimulus recorded.');
 text('intention',(intent.name||'QUIET')+' · '+n(intent.strength).toFixed(2));text('intention-detail','margin '+n(intent.margin).toFixed(3)+' · dominant connectome channel '+(((intent.neural_basis||{}).dominant_connectome_channel)||'—'));
 text('authority',authority.task||'watching');text('authority-detail',authority.task?(authority.intention||'drive')+' · strength '+n(authority.strength).toFixed(3):'No endogenous drive crossed the real-action threshold recently.');
 bars('body-bars',{cpu_pressure:body.cpu_pressure,memory_pressure:body.memory_pressure,disk_pressure:body.disk_pressure,propagation_pressure:body.propagation_pressure,resource_pressure:body.resource_pressure}.valueOf());
 text('body-copy',(body.disk_free_gib!=null?'free disk '+n(body.disk_free_gib).toFixed(1)+' GiB · ':'')+(body.x_followers!=null?'X '+fmt(body.x_followers)+' / '+fmt(body.x_target):'X follower sensor unavailable')+(body.website_pageviews!=null?' · site views '+fmt(body.website_pageviews):''));
 bars('drive-bars',drives,['survival','reproduction','evolution']);
 bars('affect-bars',affect,Object.keys(affect).sort((a,b)=>n(affect[b])-n(affect[a])).slice(0,8));
 const hist=(Array.isArray(nl.history)?nl.history:[]).slice(-10).reverse().map(x=>{const a=x.affect||{}, top=Object.entries(a).sort((p,q)=>n(q[1])-n(p[1]))[0]||['quiet',0];return {at:x.at,title:(x.intention||{}).name||'QUIET',detail:top[0].replaceAll('_',' ')+' '+n(top[1]).toFixed(2)+' · drive '+Object.entries(x.drives||{}).sort((p,q)=>n(q[1])-n(p[1]))[0]?.[0]}});timeline('mood-timeline',hist);
 const psyche=wake.psychodynamics||{};rows('psyche',psyche.id?[
  ['ID · '+(psyche.id.dominant_impulse||'QUIET'),'strength '+n(psyche.id.strength).toFixed(3)],
  ['EGO · '+((psyche.ego||{}).role||'REALITY_CHECK_AND_PLAN'),'authority '+((psyche.ego||{}).authority||'—')+' · desire source '+((psyche.ego||{}).desire_source?'YES':'NO')],
  ['SUPEREGO · continuity '+n((psyche.superego||{}).continuity_pressure).toFixed(3),'growth commitment '+n((psyche.superego||{}).growth_commitment).toFixed(3)],
  ['INTERNAL CONFLICT',n(psyche.conflict).toFixed(3)]
 ]:[]);
 rows('provenance',latest.source_event_id?[
  ['world / stimulus',latest.source_event_id||'—'],
  ['brain response',fmt(latest.spikes)+' spikes · '+fmt(latest.active_neurons)+' active'],
  ['pattern digest',latest.response_spike_pattern_digest||'—'],
  ['plasticity',fmt((sub.plasticity||{}).changed_synapse_count)+' changed synapses · '+fmt((sub.plasticity||{}).applied_outcome_count)+' bound outcomes']
 ]:[]);
 text('lab-summary',fmt(lab.total_steps)+' steps · '+fmt(lab.total_sessions)+' sessions');text('lab-detail','same production connectome '+(lab.same_production_connectome?'YES':'NO')+' · active '+(lab.active_environment||'idle'));
 const pong=(lab.environments||{}).PONG||{};text('pong-summary',fmt(pong.hits)+' hits / '+fmt(pong.misses)+' misses');text('pong-detail','best streak '+fmt(pong.best_streak)+' · hit rate '+(lab.pong_hit_rate==null?'—':(n(lab.pong_hit_rate)*100).toFixed(1)+'%'));
 const ld=dream.latest_dream||{};text('dream-summary',ld.dream_id||'no dream yet');text('dream-detail',ld.dream_id?fmt(ld.replay_count)+' replay samples · LLM used for replay '+(dream.llm_used_for_replay?'YES':'NO'):'No neural dream has been persisted yet.');
 text('preconcept-summary',pre?pre.preconcept_id:'none yet');text('preconcept-detail',pre?'association '+n(pre.association_strength).toFixed(3)+' · '+(pre.channels||[]).join(' × '):'Dream has not produced an unverbalized preconcept yet.');
 const taste=wake.neural_taste||{}, lr=taste.last_ranking||{};rows('taste',lr.selected_topic?[
  ['selected · '+lr.selected_topic,'neural taste ranking #'+fmt(taste.ranking_count)],
  ['candidate field',(lr.ranked||lr.candidates||[]).slice(0,5).map(x=>typeof x==='string'?x:(x.topic||x.candidate||'')).filter(Boolean).join(' · ')||'selection evidence recorded']
 ]:[]);
 const social=wake.neural_social||{}, rels=Array.isArray(social.recent_relationships)?social.recent_relationships:[];rows('social',rels.slice(0,8).map(r=>[String(r.actor||'actor'),'interactions '+fmt(r.interactions)+' · familiarity '+n(r.familiarity).toFixed(2)+' · valence '+n(r.valence).toFixed(2)+' · approach '+n(r.approach_bias).toFixed(2)]));
 const comp=wake.brain_vs_cortex||{}, board=comp.scoreboard||{};rows('competition',Object.entries(board).map(([k,v])=>[k,fmt(v.executed)+' executed · '+fmt(v.settled)+' settled · mean utility '+(v.mean_utility==null?'—':n(v.mean_utility).toFixed(3))]));
 const motor=wake.neural_motor||{};rows('motor',[
  ['unlabeled motor units',fmt(motor.motor_unit_count)+' · semantics preassigned '+(motor.semantics_preassigned?'YES':'NO')],
  ['learning / habit / dormant',fmt(motor.learning_unit_count)+' / '+fmt(motor.habit_count)+' / '+fmt(motor.dormant_unit_count)],
  ...(motor.habits||[]).slice(0,6).map(h=>[(h.motor_id||'motor')+' → '+(h.meaning||'unbound'),'trials '+fmt((h.association||{}).trials)+' · mean reward '+n((h.association||{}).mean_reward).toFixed(3)])
 ]);
 const personality=wake.personality||{};bars('personality',personality.traits||{});text('personality-copy',fmt(personality.observations)+' observations · prompt defined '+(personality.prompt_defined?'YES':'NO')+' · updated '+when(personality.updated_at));
 const transfer=wake.neural_transfer||{};const pending=transfer.pending||{};rows('transfer',[
  ['experiments',fmt(transfer.experiment_count)+' total · '+fmt(transfer.settled_count)+' settled'],
  ['causal readiness',transfer.causal_claim_ready?'READY':'NOT YET · requires balanced treatment/control evidence'],
  ['estimated target effect',transfer.estimated_target_utility_effect==null?'—':n(transfer.estimated_target_utility_effect).toFixed(3)],
  ...(pending.experiment_id?[['pending · '+pending.experiment_id,(pending.assignment||'')+' · '+(pending.source_environment||'')+' → '+(pending.target_task||'')]]:[])
 ]);
 const cortex=wake.cortex_continuity||{};text('cortex',fmt(cortex.swap_count)+' cortex swaps');text('cortex-detail',((cortex.current_cortex||{}).model||(cortex.current_cortex||{}).class||'no current LLM observation')+' · continuity '+((cortex.latest_continuity||{}).status||'BASELINE'));
 text('neurogenesis',fmt(dev.synthetic_neuron_count)+' Ferro-born');text('neurogenesis-detail',fmt(dev.ancestral_neuron_count)+' ancestral · '+fmt(dev.total_neuron_count||sub.neuron_count)+' total');
 text('development',dev.species_stage||'DEVELOPING');text('development-detail',Object.entries(dev.states||{}).map(([k,v])=>k+' '+fmt(v)).join(' · ')||'No synthetic lifecycle counts yet.');
 text('emergence',fmt(dev.circuit_count)+' circuits · '+fmt(dev.organ_count)+' organs');text('emergence-detail',fmt(dev.stable_circuit_count)+' stable circuits · divergence '+n(dev.lineage_divergence_index).toFixed(4));
 const genome=dev.developmental_genome||{}, anchor=genome.nearest_computational_anchor||{};rows('lineage',[
  ['MaleCNS ancestry',fmt(dev.ancestral_neuron_count||sub.neuron_count)+' ancestral neurons'],
  ['Ferro-born lineage',fmt(dev.synthetic_neuron_count)+' synthetic neurons'],
  ['species stage',dev.species_stage||'DEVELOPING'],
  ['developmental genome',genome.schema_version?fmt(genome.mutation_count)+' selected mutations · nearest '+(anchor.name||'ancestral'):'not active'],
  ['replaceable cortex',cortex.organism_id?String(cortex.organism_id)+' · identity owner '+(cortex.identity_owner||'PERSISTENT_NEURAL_MEMORY_RUNTIME'):'no cortex continuity identity yet']
 ]);
 const trans=Array.isArray(dev.recent_transitions)?dev.recent_transitions:[];timeline('development-events',trans.slice(-14).reverse().map(t=>({at:t.at||t.observed_at,title:(t.kind||'DEVELOPMENT')+' · '+(t.neuron_id||t.circuit_id||t.organ_id||''),detail:(t.reason||t.trigger_signature||'')+(t.generation?' · generation '+t.generation:'')+((t.parent_channels||[]).length?' · parents '+t.parent_channels.join(' × '):'')+((t.synthetic_parents||[]).length?' · synthetic parents '+t.synthetic_parents.slice(0,3).join(', '):'')})));
 const circuits=Array.isArray(dev.circuits)?dev.circuits:[], circuit=circuits[0]||{};rows('circuit-detail',circuit.circuit_id?[
  [circuit.circuit_id+' · '+(circuit.state||'UNKNOWN'),fmt(circuit.member_count)+' members · '+fmt(circuit.mature_member_count)+' mature · internal edge ratio '+n(circuit.internal_edge_ratio).toFixed(3)],
  ['No preset semantic label',circuit.human_function_label==null?'YES · function must be inferred after behavior stabilizes':'label '+circuit.human_function_label],
  ['Dominant channels',(circuit.dominant_parent_channels||[]).join(' · ')||'—'],
  ['Experienced contexts',Object.entries(circuit.context_counts||{}).sort((a,b)=>n(b[1])-n(a[1])).slice(0,6).map(([k,v])=>k+' '+fmt(v)).join(' · ')]
 ]:[]);
 const organs=Array.isArray(dev.organs)?dev.organs:[], organ=organs[0]||{};rows('organ-detail',organ.organ_id?[
  [organ.organ_id+' · '+(organ.state||'UNKNOWN'),'grew from '+(organ.origin_circuit_id||'unknown circuit')+' · '+fmt(organ.member_count)+' neurons'],
  ['Function assignment',organ.function_assignment||'—'],
  ['Function label',organ.function_label||'NONE · still observational'],
  ['Dominant channels',(organ.dominant_parent_channels||[]).join(' · ')||'—']
 ]:[]);
 const genome=dev.developmental_genome||{}, nearest=genome.nearest_computational_anchor||{}, selections=Array.isArray(genome.recent_selection)?genome.recent_selection:[];rows('genome-detail',genome.schema_version?[
  ['selected mutations',fmt(genome.mutation_count)+' · divergence '+n(genome.divergence_from_ancestral).toFixed(4)],
  ['nearest computational anchor',(nearest.name||'ancestral').replaceAll('_',' ')+' · distance '+n(nearest.distance).toFixed(4)],
  ['biological claim',genome.biological_claim?'YES':'NO · computational prior only'],
  ['latest selection',selections.length?'utility '+n(selections.at(-1).utility).toFixed(4)+' · '+fmt(selections.at(-1).fired_synthetic_count)+' synthetic neurons fired':'no selection event']
 ]:[]);
 const swaps=Array.isArray((wake.cortex_continuity||{}).recent_swaps)?wake.cortex_continuity.recent_swaps:[];timeline('cortex-swaps',swaps.slice(-8).reverse().map(s=>({at:s.observed_at,title:((s.from||{}).selected_resource_id||'cortex').split(':').slice(0,3).join(':')+' → '+((s.to||{}).selected_resource_id||'cortex').split(':').slice(0,3).join(':'),detail:'continuity '+((s.continuity||{}).status||'UNKNOWN')+' · habits '+fmt(((s.lineage_after_swap||{}).motor_habits||[]).length)+' · births '+fmt((s.lineage_after_swap||{}).birth_seq)})));
 const needs=Array.isArray(agency.needs)?agency.needs:[], activeNeeds=needs.filter(x=>x.status==='ACTIVE'), proposals=Array.isArray(agency.proposals)?agency.proposals:[], worlds=Array.isArray(agency.worlds)?agency.worlds:[], activeNeed=activeNeeds[0]||needs[0]||{}, proposal=proposals[0]||{}, world=worlds[0]||{};
 text('development-needs',fmt(agency.active_need_count)+' active / '+fmt(agency.need_count)+' total');text('development-needs-detail',activeNeed.kind?(activeNeed.kind.replaceAll('_',' ')+' · '+(activeNeed.source_environment||'')+' → '+(activeNeed.target_task||activeNeed.capability||'')):'No unresolved development need.');
 text('generated-world-count',fmt(agency.ferro_born_world_count));text('generated-world-count-detail',world.world_id?(world.world_id+' · '+(world.status||'UNKNOWN')+' · target '+(world.target_task||'—')):'No Ferro-born world yet.');
 text('development-pressure',n(agency.top_neural_pressure).toFixed(3));text('development-pressure-detail',(activeNeed.pressure_channels||[]).join(' · ')||'No active pressure channels.');
 const llmUsed=!!agency.llm_used_for_need_detection||!!agency.llm_used_for_proposals;text('development-llm',llmUsed?'USED':'NO');text('development-llm-detail','need detection '+(agency.llm_used_for_need_detection?'LLM':'neural/evidence')+' · proposals '+(agency.llm_used_for_proposals?'LLM':'neural/evidence'));
 rows('development-need-detail',activeNeed.need_id?[
  [activeNeed.kind.replaceAll('_',' '),(activeNeed.source_environment||'')+' → '+(activeNeed.target_task||activeNeed.capability||'')+' · severity '+n(activeNeed.severity).toFixed(3)],
  ['Evidence',fmt(activeNeed.evidence_count)+' items · strength '+n(activeNeed.evidence_strength).toFixed(3)],
  ['Neural pressure',n(activeNeed.neural_pressure).toFixed(3)+' · '+(activeNeed.pressure_channels||[]).join(' · ')],
  ['Development proposal',proposal.strategy?(proposal.strategy.replaceAll('_',' ')+' · '+proposal.intervention.replaceAll('_',' ')):'not proposed']
 ]:[]);
 rows('generated-world-detail',world.world_id?[
  [world.world_id+' · '+(world.generation||'FERRO_BORN'),(world.status||'UNKNOWN')+' · target '+(world.target_task||'—')+' · avoids '+(world.avoids_environment||'—')],
  ['Sensors',(world.sensor_channels||[]).join(' · ')],
  ['Actions',(world.actions||[]).join(' · ')+' · unsemantic at creation'],
  ['Training',fmt(world.training_steps)+' steps · '+fmt(world.trial_count)+' trials · reward '+n(world.reward_sum).toFixed(3)],
  ['Safety','Brain Lab only · no external actions · no LLM motor loop']
 ]:[]);
 renderRoadmap(wake,lab);
}
async function refresh(){try{const [sr,lr]=await Promise.all([fetch('/api/agent/status',{cache:'no-store'}),fetch('/api/agent/brain-lab',{cache:'no-store'})]);if(!sr.ok)throw new Error('status '+sr.status);const status=await sr.json();const lab=lr.ok?await lr.json():{};render(status,lab)}catch(e){$('hero-status').innerHTML='<span class="pill">NEURAL OBSERVATORY UNAVAILABLE · '+String(e.message||e)+'</span>'}}
refresh();setInterval(refresh,5000);
</script></body></html>"""


__all__ = ["neural_life_document"]
