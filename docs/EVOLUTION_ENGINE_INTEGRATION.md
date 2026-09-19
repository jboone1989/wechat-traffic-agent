# Ferro / Evolution Engine integration contract

Status: **ordered implementation contract**
Canonical engine owner: `jboone1989/agent-runtime`
Primary domain adopter: Ferro / Content Agent
Last updated: 2026-09-19

## Rule

Ferro does not own a second self-evolution state machine.

Agent Runtime owns the canonical Evolution Engine lifecycle, records, evidence gates,
experiment semantics, promotion decisions, capability history, consolidation policy,
and meta-evolution governance. Ferro supplies domain observations, outcomes, metrics,
replay adapters, and bounded mutation executors.

The canonical roadmap is Runtime
`docs/architecture/evolution-engine-v1.md`.

## Existing Ferro components

Existing modules are retained, but each gets one explicit role:

| Ferro component | Evolution Engine role |
| --- | --- |
| `self_maintenance.py` | emits failure / repeated-inefficiency / capability-gap observations |
| `generated_repair.py` | bounded **program mutation executor** only |
| `self_repair_executor.py` | replay/test executor for a program candidate |
| `capability_evolution.py` | challenger discovery input for capability candidates |
| `human_assistance.py` | human-dependency evidence source |
| `x_growth_mission.py` | external mission + real-world outcome source |
| website/channel evidence collectors | domain outcome / quality evidence sources |
| wakeful reflection | may propose hypotheses; cannot activate them |
| resource economy | resource-pressure and cost evidence source |
| operator memory | conversation/owner memory; not an evolution evidence substitute |

None of these modules may directly declare that Ferro has permanently learned a behavior.

## Required data flow

```text
Ferro world/domain event
        |
        v
Runtime ExperienceTrace
        |
        v
Runtime EvolutionExperience
        |
        v
domain/world outcome evidence
        |
        v
Runtime EvolutionEvidence
        |
        v
Runtime Diagnosis
        |
        v
Runtime EvolutionNeed
        |
        v
Runtime EvolutionCandidate
        |
        +-------------------------------+
        |                               |
        v                               v
Ferro domain executor              other executor
(repair/strategy/tool/etc.)
        |
        v
Runtime experiment / comparison
        |
        v
Runtime promote / reject / continue
        |
        v
Runtime CapabilityVersion
```

## Anti-drift constraints

1. Generated Repair is never equivalent to self-evolution.
2. Search results may create challengers, never replacements.
3. An LLM reflection may create a hypothesis, never verified evidence.
4. A successful local test may advance replay validation, never prove real-world improvement.
5. A single X post, website article, resource migration, or repair does not create permanent learning.
6. Ferro-specific code must not add a second `PROMOTED` / `ACTIVE` decision path outside Runtime.
7. Domain metrics may be defined in Ferro, but comparison windows and baseline semantics are Runtime-owned.
8. Every domain mutation executor must expose rollback or restoration information.
9. Negative and rejected experiments must remain queryable.
10. Meta-evolution remains Runtime-governed even when the proposed change targets a Ferro policy.

## Ordered Ferro adoption

### F0 — Contract only

Freeze this boundary and map all current self-improvement features to the Runtime roadmap.

Exit:
- no competing lifecycle is introduced;
- every current evolution-like module has an explicit role.

### F1 — Experience projection adoption

Consume Runtime Phase 1 `EvolutionExperience` records and attach domain references where needed.

Examples:
- X mission / post id;
- website artifact id;
- resource action id;
- repair problem / trial id.

Ferro must not mutate sealed Runtime experience records.

### F2 — Domain evidence adapters

Translate real Ferro outcomes into Runtime evidence classes.

First adapters:
- X / social results;
- website traffic/content outcome;
- self-repair tests and regression results;
- resource cost/runway outcomes.

### F3 — Domain diagnosis inputs

Expose competing explanations and domain-specific discriminating experiments.
Do not collapse one successful outcome into a strategy rule.

### F4 — Need sources

Map existing Ferro signals to canonical Runtime need origins:
- human dependency;
- recurring repair failure;
- resource pressure;
- capability gap;
- mission stagnation;
- opportunity / windfall.

### F5 — Mutation executors

Register current mechanisms as executors for explicit mutation layers:
- strategy/prompt;
- workflow;
- tool/harness;
- program repair;
- capability provider replacement.

Each executor receives a Runtime candidate and returns bounded trial evidence.

### F6 — Real prospective acceptance case

Use one bounded non-authority-raising Ferro behavior as the first end-to-end proof.
Preferred first case: content/social strategy selection.

Required chain:

```text
real experience
-> delayed world outcome
-> diagnosis
-> need
-> candidate
-> replay
-> shadow
-> prospective baseline comparison
-> promotion
-> later automatic reuse
-> continued observation
-> rollback still possible
```

### F7 — Sleep / consolidation adoption

Ferro supplies domain clusters and conflict candidates.
Runtime controls consolidation, deprecation, and candidate creation.

### F8 — Meta-evolution adoption

Only after Runtime Phase 9 is proven. Ferro may propose changes to its own learning policy,
but those changes use the same Runtime candidate/trial/promotion path.

## Development rule

Before adding any future Ferro self-improvement feature, the change description must state:

1. Evolution Engine phase;
2. canonical input record;
3. canonical output record;
4. evidence that settles success;
5. rollback path.

If those five items are missing, the change is not ready to implement.
