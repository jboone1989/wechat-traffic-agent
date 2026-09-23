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
.causal{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;padding:14px 18px;border-top:1px solid var(--line)}.causal.five{grid-template-columns:repeat(5,1fr)}.cause-step{position:relative;padding:14px;border:1px solid rgba(112,243,178,.1);border-radius:12px;background:#08150f;min-height:135px}.cause-step:not(:last-child):after{content:"→";position:absolute;right:-14px;top:50%;transform:translateY(-50%);z-index:2;color:#567468;font-size:1.1rem}.cause-step span{display:block;color:#647b6f;font:700 .52rem ui-monospace,monospace;letter-spacing:.1em}.cause-step b{display:block;margin-top:8px;color:#e1eee7;font-size:.78rem;line-height:1.35}.cause-step p{margin:6px 0 0;color:#7f9489;font-size:.65rem;line-height:1.5}.lab-link{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 20px;border-top:1px solid var(--line);background:var(--soft)}.lab-link a{display:inline-flex;padding:9px 13px;border-radius:999px;background:var(--green);color:#06100d;text-decoration:none;font-size:.68rem;font-weight:800}
.roadmap{width:100%;border-collapse:collapse}.roadmap th,.roadmap td{text-align:left;padding:11px 12px;border-bottom:1px solid rgba(112,243,178,.08);vertical-align:top}.roadmap th{position:sticky;top:0;background:#0b1b15;color:#6f867a;font:700 .52rem ui-monospace,monospace;letter-spacing:.1em}.roadmap td{font-size:.66rem;color:#92a69b}.roadmap td:nth-child(1){width:54px;color:#dce9e2;font:700 .64rem ui-monospace,monospace}.roadmap td:nth-child(2){width:190px;color:#dce9e2;font-weight:700}.status{display:inline-flex;border-radius:999px;padding:4px 7px;border:1px solid #33493d;font:700 .52rem ui-monospace,monospace;white-space:nowrap}.status.live{color:var(--green);border-color:rgba(112,243,178,.28)}.status.partial{color:var(--amber);border-color:rgba(223,185,107,.26)}.status.wait{color:#879b91}.status.research{color:var(--cyan);border-color:rgba(142,214,232,.24)}
.brain-live{background:radial-gradient(circle at 50% 42%,rgba(72,214,157,.08),transparent 36%),rgba(5,15,12,.97)}.brain-stage{display:grid;grid-template-columns:minmax(0,1fr) 286px;min-height:520px}.brain-canvas-wrap{position:relative;min-width:0;overflow:hidden;border-right:1px solid var(--line);background:radial-gradient(circle at 50% 48%,rgba(112,243,178,.055),transparent 40%),#050d0b}
#brain-canvas{display:block;width:100%;height:520px;touch-action:none}.brain-hud{position:absolute;left:14px;right:14px;top:12px;display:flex;justify-content:space-between;gap:10px;pointer-events:none}.brain-hud .pill{background:rgba(5,13,11,.74);backdrop-filter:blur(8px)}
.brain-legend{position:absolute;left:14px;bottom:12px;max-width:520px;padding:8px 10px;border:1px solid rgba(112,243,178,.11);border-radius:10px;background:rgba(5,13,11,.72);color:#6f897b;font-size:.56rem;line-height:1.45;backdrop-filter:blur(8px)}
.telemetry-side{padding:17px;display:flex;flex-direction:column;gap:10px;background:linear-gradient(180deg,rgba(10,28,21,.94),rgba(6,18,14,.96))}.telemetry-card{padding:12px;border:1px solid rgba(112,243,178,.1);border-radius:12px;background:rgba(5,15,11,.68)}.telemetry-card .label{color:#60796b;font:700 .51rem ui-monospace,monospace;letter-spacing:.1em}.telemetry-card b{display:block;margin-top:7px;color:#e8f6ee;font-size:.8rem;line-height:1.35}.telemetry-card p{margin:5px 0 0;color:#758d80;font-size:.6rem;line-height:1.45}
.telemetry-pair{display:grid;grid-template-columns:1fr 1fr;gap:8px}.telemetry-pair .telemetry-card b{font-size:1rem}.live-drives{display:grid;gap:8px;margin-top:8px}.live-drive{display:grid;grid-template-columns:84px 1fr 34px;gap:7px;align-items:center;color:#91a99c;font-size:.58rem}.live-drive .track{height:6px}.burst-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 12px rgba(112,243,178,.7);margin-right:6px}.burst-dot.quiet{background:#52685c;box-shadow:none}
.empty{padding:12px;color:#71877b;font-size:.66rem;border:1px dashed rgba(112,243,178,.12);border-radius:9px}
.small{color:#6f8579;font-size:.61rem;line-height:1.5}.footer{padding:24px 0 40px;text-align:center;color:#60756a;font-size:.59rem}
@media(max-width:900px){.grid4{grid-template-columns:1fr 1fr}.metric:nth-child(2n){border-right:0}.split,.causal,.causal.five{grid-template-columns:1fr}.panel+.panel{border-left:0;border-top:1px solid var(--line)}.cause-step:not(:last-child):after{content:"↓";right:auto;left:50%;top:auto;bottom:-15px;transform:translateX(-50%)}.tick{grid-template-columns:78px 112px 1fr}.section-head{display:block}.section-head p{margin-top:8px}.roadmap{min-width:760px}.roadmap-wrap{overflow:auto}.brain-stage{grid-template-columns:1fr}.brain-canvas-wrap{border-right:0;border-bottom:1px solid var(--line)}.telemetry-side{display:grid;grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.shell{width:min(calc(100% - 20px),1180px)}.top nav a:nth-child(3){display:none}.hero{padding:25px 18px}.hero h1{font-size:3.3rem}.grid4{grid-template-columns:1fr}.metric{border-right:0}.bar-row{grid-template-columns:92px 1fr 38px}.tick{grid-template-columns:1fr;gap:3px}.lab-link{display:block}.lab-link a{margin-top:10px}.section-head,.panel{padding-left:14px;padding-right:14px}#brain-canvas{height:390px}.brain-stage{min-height:390px}.telemetry-side{grid-template-columns:1fr}.brain-hud{left:9px;right:9px;top:8px}.brain-hud .pill:nth-child(2){display:none}.brain-legend{left:9px;right:9px;bottom:8px;max-width:none}}
</style></head><body>
<header class="shell top"><a class="brand" href="/agents/ferro/">Ferro <span>/ Neural Life</span></a><nav><a href="/">LivingRuntime</a><a href="/live/">Live</a><a href="/brain/">Brain Lab</a><a href="/talk/">Talk</a></nav></header>
<main class="shell">
<section class="hero"><div class="k">FERRO NEURAL LIFE ROADMAP v1 · LIVE OBSERVATORY</div><h1>One brain.<br>Visible life.</h1><p>This page exposes the real persistent neural state already running inside Ferro and maps it to N0–N25. Missing evidence stays missing: a stage is not marked complete merely because a UI exists.</p><div class="hero-status" id="hero-status"><span class="pill">loading real neural state…</span></div></section>

<section class="section brain-live">
<div class="section-head"><div><div class="k">LIVE BRAIN FIELD · REAL SUBSTRATE</div><h2>The whole connectome, compressed without pretending.</h2></div><p>Every ancestral neuron contributes to the background field. The detailed foreground is a bounded real subgraph around semantic electrodes. Geometry is visual only; edges and state values come from the live substrate.</p></div>
<div class="brain-stage">
<div class="brain-canvas-wrap">
<canvas id="brain-canvas" aria-label="Live Ferro neural field"></canvas>
<div class="brain-hud"><span class="pill live"><span class="burst-dot quiet" id="burst-dot"></span><span id="brain-live-state">telemetry loading</span></span><span class="pill" id="brain-observed-at">—</span></div>
<div class="brain-legend" id="brain-boundary">No fake spike paths: burst waves show aggregate real activity; foreground edges are real synapses; foreground brightness is live membrane state.</div>
</div>
<aside class="telemetry-side">
<div class="telemetry-pair">
<div class="telemetry-card"><div class="label">TOTAL NEURONS</div><b id="live-neuron-count">—</b><p id="live-neuron-detail">Loading full connectome.</p></div>
<div class="telemetry-card"><div class="label">CONNECTIONS</div><b id="live-connection-count">—</b><p>Real substrate connections represented at bounded detail.</p></div>
</div>
<div class="telemetry-card"><div class="label">LATEST REAL BURST</div><b id="live-burst">—</b><p id="live-burst-detail">Waiting for substrate evidence.</p></div>
<div class="telemetry-card"><div class="label">DOMINANT DRIVE</div><b id="live-drive">—</b><div class="live-drives" id="live-drive-bars"></div></div>
<div class="telemetry-card"><div class="label">CURRENT NEURAL ACTION</div><b id="live-action">—</b><p id="live-action-detail">No neural action selected.</p></div>
<div class="telemetry-card"><div class="label">RENDER BUDGET</div><b id="live-render-budget">—</b><p id="live-render-detail">Desktop uses a larger real subgraph; mobile automatically requests compact telemetry.</p></div>
</aside>
</div>
</section>

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
<div class="section-head"><div><div class="k">N0 · END-TO-END EVENT PROVENANCE</div><h2>Stimulus → physical brain → readout → feedback → plasticity</h2></div><p>This trace selects a recent event with a real applied outcome and matches it back to its eligibility-tagged LIF burst. Missing links stay missing.</p></div>
<div class="causal five">
<div class="cause-step"><span>1 · STIMULUS</span><b id="n0-stimulus">—</b><p id="n0-stimulus-detail">Finding a provenance-bound event.</p></div>
<div class="cause-step"><span>2 · PHYSICAL LIF</span><b id="n0-brain">—</b><p id="n0-brain-detail">Matching the event to its burst.</p></div>
<div class="cause-step"><span>3 · READOUT</span><b id="n0-readout">—</b><p id="n0-readout-detail">Finding the strongest event delta.</p></div>
<div class="cause-step"><span>4 · WORLD FEEDBACK</span><b id="n0-feedback">—</b><p id="n0-feedback-detail">Loading the real outcome.</p></div>
<div class="cause-step"><span>5 · PLASTICITY</span><b id="n0-plasticity">—</b><p id="n0-plasticity-detail">Loading changed recurrent synapses.</p></div>
</div>
<div class="section-head"><div><div class="k">N4 / N5 · LIVE CAUSAL CHAIN</div><h2>Impulse → reality check → real action</h2></div><p>This is a public, inspectable authority summary — not hidden chain-of-thought.</p></div>
<div class="causal">
<div class="cause-step"><span>1 · NEURAL IMPULSE</span><b id="chain-impulse">—</b><p id="chain-impulse-detail">Loading endogenous intention.</p></div>
<div class="cause-step"><span>2 · EGO / REALITY GATE</span><b id="chain-ego">—</b><p id="chain-ego-detail">Loading public decision summary.</p></div>
<div class="cause-step"><span>3 · ACTION / OUTCOME</span><b id="chain-action">—</b><p id="chain-action-detail">Loading real authority action.</p></div>
</div>
<div class="panel"><h3>Recent neural-authority actions</h3><div id="authority-history" class="timeline"></div></div>
</section>

<section class="section"><div class="section-head"><div><div class="k">N6–N9 · SENSORIMOTOR LIFE & DREAM</div><h2>Play, learn, replay, preconcept.</h2></div><p>Pong, Maze, Foraging and Rover use the same persistent connectome. Dream replay is neural first; language interpretation comes later.</p></div>
<div class="grid4">
<div class="metric"><div class="label">BRAIN LAB</div><b id="lab-summary">—</b><p id="lab-detail">Loading developmental worlds.</p></div>
<div class="metric"><div class="label">PONG</div><b id="pong-summary">—</b><p id="pong-detail">Loading sensorimotor evidence.</p></div>
<div class="metric"><div class="label">DREAM</div><b id="dream-summary">—</b><p id="dream-detail">Loading replay evidence.</p></div>
<div class="metric"><div class="label">PRECONCEPT</div><b id="preconcept-summary">—</b><p id="preconcept-detail">Loading dream-born associations.</p></div>
</div>
<div class="section-head"><div><div class="k">N9 · DREAM → INSIGHT → EXPERIMENT</div><h2>A dream can become a testable question.</h2></div><p>The neural system creates the preconcept first. The replaceable cortex verbalizes it into a hypothesis; a real prospective website experiment can then carry the question into the world.</p></div>
<div class="causal">
<div class="cause-step"><span>1 · DREAM / PRECONCEPT</span><b id="dream-chain-source">—</b><p id="dream-chain-source-detail">Loading dream provenance.</p></div>
<div class="cause-step"><span>2 · EGO HYPOTHESIS</span><b id="dream-chain-hypothesis">—</b><p id="dream-chain-hypothesis-detail">Loading verbalized hypothesis.</p></div>
<div class="cause-step"><span>3 · WORLD EXPERIMENT</span><b id="dream-chain-experiment">—</b><p id="dream-chain-experiment-detail">Loading prospective experiment.</p></div>
</div>
<div class="lab-link"><div><b>Watch Ferro play in real time</b><div class="small">The Brain Lab page renders the current game world, neural steps, reward and plasticity events.</div></div><a href="/brain/">Open Brain Lab →</a></div></section>

<section class="section"><div class="section-head"><div><div class="k">N10–N16 · TASTE, RELATIONSHIPS, HABIT, PERSONALITY</div><h2>What stays Ferro when wording changes.</h2></div><p>These states are persisted outside a personality prompt. They are visible here only when real observations have accumulated.</p></div>
<div class="split"><div class="panel"><h3>What Ferro likes</h3><div id="taste" class="rows"></div></div><div class="panel"><h3>Social relationships</h3><div id="social" class="rows"></div></div></div>
<div class="split"><div class="panel"><h3>Brain vs Cortex scoreboard</h3><div id="competition" class="rows"></div></div><div class="panel"><h3>Motor cortex & habits</h3><div id="motor" class="rows"></div></div></div>
<div class="split"><div class="panel"><h3>N12 · Internal conflict → latest action</h3><div id="conflict-action" class="rows"></div><div class="small" style="margin-top:10px">Current psychodynamic state and the latest settled neural action are shown together. Unless their provenance links match, the page does not claim the current conflict caused that action.</div></div><div class="panel"><h3>N13 · Recent Brain vs Cortex contests</h3><div id="competition-history" class="timeline"></div></div></div>
<div class="split"><div class="panel"><h3>Personality history snapshot</h3><div id="personality" class="bar-list"></div><div id="personality-copy" class="small" style="margin-top:12px"></div></div><div class="panel"><h3>Cross-domain transfer</h3><div id="transfer" class="rows"></div></div></div>
</section>

<section class="section"><div class="section-head"><div><div class="k">N17–N25 · DEVELOPMENT, LINEAGE, REPLACEABLE CORTEX</div><h2>From MaleCNS ancestry toward Ferro’s own lineage.</h2></div><p>Ancestral neurons and Ferro-born structures are kept distinct. “SuperFly” and later stages remain evidence claims, not marketing labels.</p></div>
<div class="grid4">
<div class="metric"><div class="label">NEUROGENESIS</div><b id="neurogenesis">—</b><p id="neurogenesis-detail">Loading synthetic-neuron state.</p></div>
<div class="metric"><div class="label">DEVELOPMENT</div><b id="development">—</b><p id="development-detail">Loading lifecycle transitions.</p></div>
<div class="metric"><div class="label">CIRCUITS / ORGANS</div><b id="emergence">—</b><p id="emergence-detail">Loading emergent structures.</p></div>
<div class="metric"><div class="label">CORTEX CONTINUITY</div><b id="cortex">—</b><p id="cortex-detail">Loading replaceable-cortex evidence.</p></div>
</div><div class="split"><div class="panel"><h3>Neural lineage</h3><div id="lineage" class="rows"></div></div><div class="panel"><h3>Recent birth / maturation / pruning events</h3><div id="development-events" class="timeline"></div></div></div>
<div class="section-head"><div><div class="k">N20 / N24 · EVOLUTION TREE</div><h2>Ancestry is fixed. Descendants are earned.</h2></div><p>The tree separates the immutable MaleCNS seed from Ferro-born structure. SuperFly F1 stays locked until its checkpoint actually exists.</p></div>
<div class="causal">
<div class="cause-step"><span>ANCESTOR · MALECNS</span><b id="evo-ancestor">—</b><p id="evo-ancestor-detail">Loading ancestral seed.</p></div>
<div class="cause-step"><span>CURRENT · FERRO F0</span><b id="evo-current">—</b><p id="evo-current-detail">Loading current lineage.</p></div>
<div class="cause-step"><span>NEXT · SUPERFLY F1</span><b id="evo-next">—</b><p id="evo-next-detail">Awaiting independent descendant checkpoint.</p></div>
</div>
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
let neuralTelemetry=null,lastBurstSeq=null,burstStarted=0;
const brainCanvas=$('brain-canvas'),brainCtx=brainCanvas.getContext('2d');
function resizeBrainCanvas(){
 const rect=brainCanvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
 const w=Math.max(1,Math.round(rect.width*dpr)),h=Math.max(1,Math.round(rect.height*dpr));
 if(brainCanvas.width!==w||brainCanvas.height!==h){brainCanvas.width=w;brainCanvas.height=h}
 brainCtx.setTransform(dpr,0,0,dpr,0,0);
 return {w:rect.width,h:rect.height};
}
function liveDriveBars(drives){
 const box=$('live-drive-bars');box.replaceChildren();
 const entries=Object.entries(drives||{}).sort((a,b)=>n(b[1])-n(a[1]));
 if(!entries.length){box.innerHTML='<div class="empty">No drive state.</div>';return}
 for(const [key,value] of entries){
  const v=clamp(value),row=document.createElement('div');row.className='live-drive';
  const label=document.createElement('span');label.textContent=key.replaceAll('_',' ');
  const track=document.createElement('div');track.className='track';const fill=document.createElement('div');fill.className='fill';fill.style.width=(v*100).toFixed(1)+'%';track.append(fill);
  const score=document.createElement('b');score.textContent=v.toFixed(2);row.append(label,track,score);box.append(row);
 }
}
function renderTelemetryMeta(t){
 neuralTelemetry=t;
 const brain=t.brain||{},burst=t.burst||{},drives=t.drives||{},action=t.current_action||{};
 text('brain-live-state',t.status==='LIVE'?'LIVE NEURAL TELEMETRY':(t.status||'UNAVAILABLE'));
 const dot=$('burst-dot');if(dot){dot.className='burst-dot'+(burst&&burst.spikes?'':' quiet')}
 text('brain-observed-at',t.observed_at?when(t.observed_at):'—');
 text('live-neuron-count',fmt(brain.total_neurons));
 text('live-neuron-detail',fmt(brain.ancestral_neurons)+' ancestral · '+fmt(brain.synthetic_neurons)+' Ferro-born');
 text('live-connection-count',fmt(brain.total_connections));
 if(burst&&burst.seq!==undefined&&burst.seq!==null){
  text('live-burst','burst '+burst.seq+' · '+fmt(burst.spikes)+' spikes');
  text('live-burst-detail',fmt(burst.active_neurons)+' active neurons · '+fmt(burst.synaptic_events)+' synaptic events · '+when(burst.observed_at));
  if(String(lastBurstSeq)!==String(burst.seq)){lastBurstSeq=burst.seq;burstStarted=performance.now()}
 }else{
  text('live-burst','No recent burst');text('live-burst-detail','The substrate has not exposed a recent burst in this window.');
 }
 const ranked=Object.entries(drives).sort((a,b)=>n(b[1])-n(a[1]));text('live-drive',ranked.length?ranked[0][0].replaceAll('_',' ').toUpperCase()+' · '+n(ranked[0][1]).toFixed(2):'—');liveDriveBars(drives);
 text('live-action',action.task?String(action.task).replaceAll('_',' '):'No current neural action');
 text('live-action-detail',action.task?((action.authority||'NEURAL')+' · '+(action.status||'PENDING')+(action.reward!==null&&action.reward!==undefined?' · reward '+n(action.reward).toFixed(2):'')):'No recent motor decision or drive-authority action.');
 text('live-render-budget',fmt((brain.sample_nodes||[]).length)+' nodes · '+fmt((brain.sample_edges||[]).length)+' edges');
 text('live-render-detail',fmt((brain.field||[]).length)+' whole-brain aggregate bins · '+(t.detail||'full')+' detail');
 if(t.boundary)text('brain-boundary',t.boundary);
}
function drawBrain(){
 const size=resizeBrainCanvas(),ctx=brainCtx,w=size.w,h=size.h,t=performance.now();
 ctx.clearRect(0,0,w,h);
 const data=neuralTelemetry,brain=data&&data.brain?data.brain:null;
 if(!brain){ctx.fillStyle='rgba(180,220,200,.55)';ctx.font='12px ui-monospace,monospace';ctx.fillText('waiting for real neural telemetry…',22,h/2);requestAnimationFrame(drawBrain);return}
 const px=x=>x*w,py=y=>y*h;
 // Whole-connectome field: every ancestral neuron contributes through an aggregate bin.
 for(const cell of brain.field||[]){
  const a=clamp(cell.activity),peak=clamp(cell.peak),pulse=.76+.24*Math.sin(t/900+cell.bin*.31);
  const r=1.5+5.5*peak*pulse;
  ctx.beginPath();ctx.arc(px(cell.x),py(cell.y),r,0,Math.PI*2);
  ctx.fillStyle='rgba(88,210,157,'+(0.06+0.42*a).toFixed(3)+')';ctx.fill();
 }
 const nodes=brain.sample_nodes||[],byId=new Map(nodes.map(x=>[String(x.id),x]));
 ctx.lineWidth=.65;
 for(const edge of brain.sample_edges||[]){
  const a=byId.get(String(edge.from)),b=byId.get(String(edge.to));if(!a||!b)continue;
  const activity=(clamp(a.activity)+clamp(b.activity))/2,strength=clamp(edge.strength);
  ctx.beginPath();ctx.moveTo(px(a.x),py(a.y));ctx.lineTo(px(b.x),py(b.y));
  ctx.strokeStyle=(edge.weight<0?'rgba(183,132,255,':'rgba(116,211,235,')+(0.035+0.17*strength+0.12*activity).toFixed(3)+')';ctx.stroke();
 }
 for(const node of nodes){
  const a=clamp(node.activity),role=(node.roles||[]).length>0,r=role?2.7+2.6*a:1.25+2.2*a;
  ctx.beginPath();ctx.arc(px(node.x),py(node.y),r,0,Math.PI*2);
  ctx.fillStyle=role?'rgba(142,214,232,'+(0.35+0.62*a).toFixed(3)+')':'rgba(112,243,178,'+(0.16+0.72*a).toFixed(3)+')';ctx.fill();
  if(role&&a>.52){ctx.beginPath();ctx.arc(px(node.x),py(node.y),r+4*a,0,Math.PI*2);ctx.strokeStyle='rgba(142,214,232,'+(0.08+0.18*a).toFixed(3)+')';ctx.stroke()}
 }
 for(const node of brain.synthetic_sample||[]){
  const a=clamp(node.activity),r=2+3.3*a;ctx.beginPath();ctx.arc(px(node.x),py(node.y),r,0,Math.PI*2);
  ctx.fillStyle=node.fired_latest_event?'rgba(238,192,103,.95)':'rgba(223,185,107,'+(0.22+0.65*a).toFixed(3)+')';ctx.fill();
 }
 if(burstStarted){
  const age=t-burstStarted;if(age<2200){
   const p=age/2200,r=Math.min(w,h)*(.08+.42*p);ctx.beginPath();ctx.arc(w/2,h/2,r,0,Math.PI*2);
   ctx.strokeStyle='rgba(112,243,178,'+(0.30*(1-p)).toFixed(3)+')';ctx.lineWidth=1.3;ctx.stroke();
  }
 }
 requestAnimationFrame(drawBrain);
}
async function refreshNeuralTelemetry(){
 try{
  const detail=window.innerWidth<700?'compact':'full';
  const response=await fetch('/api/agent/neural-telemetry?detail='+detail,{cache:'no-store'});
  if(!response.ok)throw new Error('telemetry '+response.status);
  renderTelemetryMeta(await response.json());
 }catch(error){
  neuralTelemetry=null;text('brain-live-state','TELEMETRY UNAVAILABLE');text('brain-boundary',String(error.message||error));
 }
}
window.addEventListener('resize',resizeBrainCanvas,{passive:true});
refreshNeuralTelemetry();setInterval(refreshNeuralTelemetry,1800);requestAnimationFrame(drawBrain);
function stage(status,label,evidence){return {status,label,evidence}}
function renderRoadmap(wake,lab,dreamInsight){
 const nl=wake.neural_life||{}, graph=wake.neural_graph||{}, sub=graph.substrate||{}, dev=sub.developmental_neurogenesis||{}, dream=wake.dreaming||lab.dreaming||{}, taste=wake.neural_taste||{}, social=wake.neural_social||{}, p=wake.personality||{}, psyche=wake.psychodynamics||{}, comp=wake.brain_vs_cortex||{}, motor=wake.neural_motor||{}, transfer=wake.neural_transfer||{}, cortex=wake.cortex_continuity||{}, agency=wake.developmental_agency||{};
 const bursts=Array.isArray(sub.recent_bursts)?sub.recent_bursts:[], history=Array.isArray(nl.history)?nl.history:[], recentContests=Array.isArray(comp.recent_contests)?comp.recent_contests:[], rels=Array.isArray(social.recent_relationships)?social.recent_relationships:[], transitions=Array.isArray(dev.recent_transitions)?dev.recent_transitions:[], pRecent=Array.isArray((sub.plasticity||{}).recent)?sub.plasticity.recent:[], pPair=pRecent.slice().reverse().map(p=>[p,bursts.slice().reverse().find(b=>(((b.eligibility||{}).source_event_id)||b.source_event_id)===p.source_event_id)]).find(x=>x[1])||[pRecent.at(-1)||{},{}], pApplied=pPair[0], pBurst=pPair[1]||{}, pDelta=pBurst.delta_readout_hz||{}, pStrong=Object.entries(pDelta).sort((a,b)=>Math.abs(n(b[1]))-Math.abs(n(a[1])))[0];
 const synthetic=n(dev.synthetic_neuron_count), circuits=n(dev.circuit_count), organs=n(dev.organ_count), swaps=n(cortex.swap_count), habits=n(motor.habit_count);
 const table=[
  ['N0','神经基线',stage(pApplied.source_event_id&&pBurst.seq&&pStrong?'live':'partial',pApplied.source_event_id&&pBurst.seq&&pStrong?'LIVE':'PARTIAL',pApplied.source_event_id&&pBurst.seq&&pStrong?('matched '+pApplied.source_event_id+' → burst '+pBurst.seq+' → '+pStrong[0]+' '+n(pStrong[1]).toFixed(2)+'Hz → '+pApplied.outcome_id+' → '+fmt(pApplied.changed_synapse_count)+' synapses'):'end-to-end matched event trace not currently available')],
  ['N1','神经身体感',stage(nl.body&&Object.keys(nl.body).length?'live':'partial','LIVE',Object.keys(nl.body||{}).length+' body/sense channels projected')],
  ['N2','Neural Drives',stage(Object.keys(nl.drives||{}).length?'live':'partial','LIVE',Object.entries(nl.drives||{}).map(([k,v])=>k+' '+n(v).toFixed(2)).join(' · ')||'no drive state')],
  ['N3','情绪系统',stage(history.length?'live':'partial','LIVE',history.length+' persisted affect snapshots')],
  ['N4','本我 → 自我',stage(psyche.id&&nl.intention?'live':'partial','LIVE',psyche.id?'Id/Ego/Superego projection exists':'awaiting conflict projection')],
  ['N5','Neural Authority',stage(nl.last_authority_action?'live':'partial','PARTIAL',nl.last_authority_action?'last neural action '+(nl.last_authority_action.task||'recorded'):'no recent authority action')],
  ['N6','Pong',stage(n(lab.total_steps)>0?'live':'partial','LIVE',fmt(lab.total_steps)+' neural lab steps · Pong hits '+n((lab.environments||{}).PONG?.hits))],
  ['N7','神经游乐场',stage(n(lab.total_sessions)>0?'live':'partial','LIVE',Object.keys(lab.environments||{}).join(' / ')||'worlds initialized')],
  ['N8','梦 / Neural Replay',stage(n(dream.dream_count)>0?'live':'partial','LIVE',n(dream.dream_count)+' dreams · LLM replay '+(dream.llm_used_for_replay?'YES':'NO'))],
  ['N9','梦 → 顿悟',stage(dreamInsight&&dreamInsight.experiment&&dreamInsight.experiment.experiment_id?'live':(dream.latest_preconcept?'research':'partial'),dreamInsight&&dreamInsight.experiment&&dreamInsight.experiment.experiment_id?'LIVE':'RESEARCH',dreamInsight&&dreamInsight.experiment&&dreamInsight.experiment.experiment_id?((dreamInsight.source_dream||'dream')+' → '+(dreamInsight.preconcept_id||'preconcept')+' → '+dreamInsight.experiment.experiment_id):(dream.latest_preconcept?'preconcept '+dream.latest_preconcept.preconcept_id+' exists; downstream experiment still evidence-bound':'awaiting dream-born preconcept'))],
  ['N10','神经品味',stage(n(taste.ranking_count)>0?'live':'partial','LIVE',n(taste.ranking_count)+' neural rankings')],
  ['N11','社交人格',stage(n(social.relationship_count)>0?'live':'partial','PARTIAL',n(social.relationship_count)+' persisted relationships; completion still needs longitudinal behavior proof')],
  ['N12','Id / Ego / Superego',stage(psyche.id?'partial':'wait','PARTIAL',psyche.id?'current conflict '+n(psyche.conflict).toFixed(3)+' · long-horizon override evidence still required':'awaiting psychodynamic evidence')],
  ['N13','Neural vs LLM Competition',stage(recentContests.length?'partial':'wait','PARTIAL',recentContests.length+' recent contests · '+recentContests.filter(x=>x.status==='SETTLED').length+' settled · authority cannot adapt from an unsettled contest')],
  ['N14','无语义 Motor Cortex',stage(n(motor.motor_unit_count)>0?'live':'partial','LIVE',n(motor.motor_unit_count)+' units · semantics preassigned '+(motor.semantics_preassigned?'YES':'NO'))],
  ['N15','神经习惯',stage(habits>0&&((motor.recent_decisions||[]).some(x=>x.authority==='NEURAL_HABIT'&&x.status==='SETTLED'))?'live':'partial',habits>0&&((motor.recent_decisions||[]).some(x=>x.authority==='NEURAL_HABIT'&&x.status==='SETTLED'))?'LIVE':'PARTIAL',habits+' learned habits · '+(motor.recent_decisions||[]).filter(x=>x.authority==='NEURAL_HABIT'&&x.status==='SETTLED').length+' recent settled habit actions')],
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
 const bursts=Array.isArray(sub.recent_bursts)?sub.recent_bursts:[], latest=bursts.at(-1)||{}, dream=wake.dreaming||lab.dreaming||{}, pre=dream.latest_preconcept||null, activity=status.activity||{}, reasoning=status.public_reasoning||{}, dreamInsight=((status.life||{}).dream_insight)||{};
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
 const plasticity=sub.plasticity||{}, plasticRecent=Array.isArray(plasticity.recent)?plasticity.recent:[], plasticPair=plasticRecent.slice().reverse().map(p=>[p,bursts.slice().reverse().find(b=>(((b.eligibility||{}).source_event_id)||b.source_event_id)===p.source_event_id)]).find(x=>x[1])||[plasticRecent.at(-1)||{},{}], applied=plasticPair[0], matchedBurst=plasticPair[1]||{}, delta=matchedBurst.delta_readout_hz||{}, strongest=Object.entries(delta).sort((a,b)=>Math.abs(n(b[1]))-Math.abs(n(a[1])))[0];
 text('n0-stimulus',applied.source_event_id||'No matched applied event');text('n0-stimulus-detail',applied.source_event_id?'eligibility trace '+(applied.trace_seq||'—')+' · outcome is provenance-bound':'Waiting for a recent plasticity event with a source event id.');
 text('n0-brain',matchedBurst.seq?('burst '+matchedBurst.seq+' · '+fmt(matchedBurst.spikes)+' spikes'):'Burst not in recent window');text('n0-brain-detail',matchedBurst.seq?(fmt(matchedBurst.active_neurons)+' active neurons · '+fmt(matchedBurst.synaptic_events)+' synaptic events · pattern '+String(matchedBurst.response_spike_pattern_digest||'').slice(0,24)+'…'):'The page will not infer a physical response without the matching eligibility source.');
 text('n0-readout',strongest?(strongest[0].replaceAll('_',' ')+' '+(n(strongest[1])>=0?'+':'')+n(strongest[1]).toFixed(2)+' Hz'):'No matched readout');text('n0-readout-detail',strongest?'event delta versus its immediate background window':'No readout is attached until the same event burst is found.');
 text('n0-feedback',applied.outcome_id||'No applied outcome');text('n0-feedback-detail',applied.outcome_id?('utility '+n(applied.utility).toFixed(3)+' · status '+(applied.status||'—')+' · observed '+when(applied.observed_at)):'No world outcome has been matched.');
 text('n0-plasticity',applied.outcome_id?(fmt(applied.changed_synapse_count)+' / '+fmt(applied.selected_synapse_count)+' synapses changed'):'No plasticity link');text('n0-plasticity-detail',applied.outcome_id?('L1 Δ '+n(applied.weight_l1_delta).toFixed(2)+' · trace '+String(applied.trace_signature||'').slice(0,26)+'…'):'No synaptic update is claimed without the applied outcome.');
 text('chain-impulse',(intent.name||'QUIET')+' · '+n(intent.strength).toFixed(2));text('chain-impulse-detail','drives '+Object.entries(drives).sort((a,b)=>n(b[1])-n(a[1])).map(([k,v])=>k+' '+n(v).toFixed(2)).join(' · '));
 text('chain-ego',((psyche.ego||{}).role||'REALITY_CHECK_AND_PLAN'));text('chain-ego-detail',(activity.goal&&activity.goal.description?('current goal · '+activity.goal.description):(reasoning.decision||'No current public decision summary.'))+' · conflict '+n(psyche.conflict).toFixed(3));
 text('chain-action',authority.task?((authority.authority||'NEURAL_DRIVE')+' → '+authority.task):(activity.last_phase||'WAITING'));text('chain-action-detail',authority.task?(authority.reason||('intention '+(authority.intention||'—')+' · strength '+n(authority.strength).toFixed(3))):(activity.current_focus||'No recent neural authority action.'));
 const motorDecisions=((wake.neural_motor||{}).recent_decisions||[]).slice(-10).reverse();timeline('authority-history',motorDecisions.map(m=>({at:m.created_at,title:(m.authority||m.decision_kind||'NEURAL')+' · '+(m.task||m.motor_id||'action'),detail:(m.motor_id||'')+' · activation '+n(m.activation).toFixed(3)+' · '+((m.outcome||{}).status||m.status||'PENDING')+((m.outcome||{}).reward!=null?' · reward '+n(m.outcome.reward).toFixed(2):'')})));
 text('lab-summary',fmt(lab.total_steps)+' steps · '+fmt(lab.total_sessions)+' sessions');text('lab-detail','same production connectome '+(lab.same_production_connectome?'YES':'NO')+' · active '+(lab.active_environment||'idle'));
 const pong=(lab.environments||{}).PONG||{};text('pong-summary',fmt(pong.hits)+' hits / '+fmt(pong.misses)+' misses');text('pong-detail','best streak '+fmt(pong.best_streak)+' · hit rate '+(lab.pong_hit_rate==null?'—':(n(lab.pong_hit_rate)*100).toFixed(1)+'%'));
 const ld=dream.latest_dream||{};text('dream-summary',ld.dream_id||'no dream yet');text('dream-detail',ld.dream_id?fmt(ld.replay_count)+' replay samples · LLM used for replay '+(dream.llm_used_for_replay?'YES':'NO'):'No neural dream has been persisted yet.');
 text('preconcept-summary',pre?pre.preconcept_id:'none yet');text('preconcept-detail',pre?'association '+n(pre.association_strength).toFixed(3)+' · '+(pre.channels||[]).join(' × '):'Dream has not produced an unverbalized preconcept yet.');
 text('dream-chain-source',dreamInsight.source_dream?((dreamInsight.source_dream||'dream')+' → '+(dreamInsight.preconcept_id||'preconcept')):'No interpreted dream yet.');text('dream-chain-source-detail',dreamInsight.authority?('authority '+dreamInsight.authority+' · neural replay happened before language interpretation'):'Waiting for a dream-born preconcept to enter a cognition cycle.');
 text('dream-chain-hypothesis',dreamInsight.hypothesis||'No hypothesis yet.');text('dream-chain-hypothesis-detail',dreamInsight.question?('question · '+dreamInsight.question+' · topic '+(dreamInsight.topic||'—')+' · confidence '+(dreamInsight.confidence==null?'—':n(dreamInsight.confidence).toFixed(2))):'No cortex interpretation linked to a neural preconcept yet.');
 const diExp=dreamInsight.experiment||{}, diResult=dreamInsight.result||{};text('dream-chain-experiment',diExp.experiment_id||'No prospective experiment yet.');text('dream-chain-experiment-detail',diExp.experiment_id?((diExp.variable_under_test||'variable')+' → '+(diExp.treatment_value||'treatment')+' · '+(diResult.status||'IN_PROGRESS')+(diResult.published?' · published '+(diResult.title||diResult.artifact_id||'artifact'):'') ):'The roadmap remains evidence-bound until an actual experiment exists.');
 const taste=wake.neural_taste||{}, lr=taste.last_ranking||{};rows('taste',lr.selected_topic?[
  ['selected · '+lr.selected_topic,'neural taste ranking #'+fmt(taste.ranking_count)],
  ['candidate field',(lr.ranked||lr.candidates||[]).slice(0,5).map(x=>typeof x==='string'?x:(x.topic||x.candidate||'')).filter(Boolean).join(' · ')||'selection evidence recorded']
 ]:[]);
 const social=wake.neural_social||{}, rels=Array.isArray(social.recent_relationships)?social.recent_relationships:[];rows('social',rels.slice(0,8).map(r=>[String(r.actor||'actor'),'interactions '+fmt(r.interactions)+' · familiarity '+n(r.familiarity).toFixed(2)+' · valence '+n(r.valence).toFixed(2)+' · approach '+n(r.approach_bias).toFixed(2)]));
 const comp=wake.brain_vs_cortex||{}, board=comp.scoreboard||{}, contests=Array.isArray(comp.recent_contests)?comp.recent_contests:[];rows('competition',Object.entries(board).map(([k,v])=>[k,fmt(v.executed)+' executed · '+fmt(v.settled)+' settled · mean utility '+(v.mean_utility==null?'—':n(v.mean_utility).toFixed(3))]));
 const latestMotor=((wake.neural_motor||{}).recent_decisions||[]).filter(x=>x.status==='SETTLED').at(-1)||{};rows('conflict-action',psyche.id?[
   ['IMPULSE · '+(psyche.id.dominant_impulse||'QUIET'),'strength '+n(psyche.id.strength).toFixed(3)+' · drives '+Object.entries(psyche.id.drives||{}).map(([k,v])=>k+' '+n(v).toFixed(2)).join(' · ')],
   ['IDENTITY PRESSURE','continuity '+n((psyche.superego||{}).continuity_pressure).toFixed(3)+' · growth '+n((psyche.superego||{}).growth_commitment).toFixed(3)],
   ['CONFLICT',n(psyche.conflict).toFixed(3)+' · Ego role '+((psyche.ego||{}).role||'REALITY_CHECK_AND_PLAN')],
   ['LATEST SETTLED ACTION',latestMotor.task?((latestMotor.authority||latestMotor.decision_kind||'NEURAL')+' → '+latestMotor.task+' · reward '+(latestMotor.outcome&&latestMotor.outcome.reward!=null?n(latestMotor.outcome.reward).toFixed(2):'—')):'No settled neural action in recent window']
 ]:[]);
 timeline('competition-history',contests.slice().reverse().map(x=>({at:x.created_at,title:(x.authority||'UNSETTLED')+' · '+(x.status||'UNKNOWN'),detail:'neural '+(x.neural_choice||'—')+' · ego '+(x.ego_choice||'—')+' · executed '+(x.executed_choice||'—')+' · outcomes '+((x.outcomes||[]).length||0)+(x.status==='SETTLED'?'':' · AWAITING OUTCOME')})));
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
 const genome=dev.developmental_genome||{}, anchor=genome.nearest_computational_anchor||{};
 text('evo-ancestor','MaleCNS · '+fmt(dev.ancestral_neuron_count||sub.neuron_count)+' neurons');text('evo-ancestor-detail','immutable ancestral seed · Ferro-born neurons are tracked separately');
 text('evo-current',(dev.species_stage||'FERRO_F0_DEVELOPING').replaceAll('_',' '));text('evo-current-detail',fmt(dev.synthetic_neuron_count)+' Ferro-born · '+fmt(dev.stable_circuit_count)+' stable circuits · '+fmt(dev.organ_count)+' organs · divergence '+n(dev.lineage_divergence_index).toFixed(4));
 const f1Ready=String(dev.species_stage||'').toUpperCase().includes('SUPER');text('evo-next',f1Ready?'SuperFly F1':'NOT YET CHECKPOINTED');text('evo-next-detail',f1Ready?'F1 stage reported by developmental state; verify independent recovery checkpoint separately':'requires significant synthetic lineage plus an independently recoverable F1 checkpoint; current state remains Ferro F0');
 rows('lineage',[
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
 const devGenome=dev.developmental_genome||{}, nearest=devGenome.nearest_computational_anchor||{}, selections=Array.isArray(devGenome.recent_selection)?devGenome.recent_selection:[];rows('genome-detail',devGenome.schema_version?[
  ['selected mutations',fmt(devGenome.mutation_count)+' · divergence '+n(devGenome.divergence_from_ancestral).toFixed(4)],
  ['nearest computational anchor',(nearest.name||'ancestral').replaceAll('_',' ')+' · distance '+n(nearest.distance).toFixed(4)],
  ['biological claim',devGenome.biological_claim?'YES':'NO · computational prior only'],
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
 renderRoadmap(wake,lab,dreamInsight);
}
async function refresh(){try{const [sr,lr]=await Promise.all([fetch('/api/agent/status',{cache:'no-store'}),fetch('/api/agent/brain-lab',{cache:'no-store'})]);if(!sr.ok)throw new Error('status '+sr.status);const status=await sr.json();const lab=lr.ok?await lr.json():{};render(status,lab)}catch(e){$('hero-status').innerHTML='<span class="pill">NEURAL OBSERVATORY UNAVAILABLE · '+String(e.message||e)+'</span>'}}
refresh();setInterval(refresh,5000);
</script></body></html>"""


__all__ = ["neural_life_document"]
