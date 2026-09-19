# Ferro Memory System roadmap

This roadmap is normative for memory-system development. Work advances in order unless a production
incident requires an isolated fix. New memory features must identify the milestone they belong to.

## Invariants

- Trajectory is immutable history; durable memory is a derived cognitive layer.
- Existing goals, missions, VirtualBrain worldview, predictions and runtime state keep their authority.
- Retrieval indexes are rebuildable and never become the sole source of truth.
- Every durable memory has provenance and temporal validity.
- Revision preserves earlier states.
- One Memory Router is the public recall entry point.
- One Memory Formation Pipeline is the public durable-write entry point.
- Vector search is an optimization milestone, not the architecture.

## M0 — Architecture contract

**Goal:** freeze semantics before adding infrastructure.

Deliverables:
- ADR-0011 accepted.
- Seven memory classes and authority boundaries documented.
- Canonical-versus-derived rule documented.
- Evaluation scenarios defined.

Exit criteria:
- future changes can be classified into a named memory class and milestone;
- no component needs vector search to satisfy M0.

## M1 — Canonical memory substrate

**Goal:** create a durable queryable substrate without changing recall semantics.

Deliverables:
- SQLite canonical memory store with schema versioning;
- indexed time/type/status/source/entity/topic metadata;
- append-only canonical memory event ledger;
- compatibility import/mirror for current owner `long_term_memory.jsonl`;
- current owner memory behavior preserved.

Exit criteria:
- restart durability and idempotent legacy import pass;
- duplicate IDs cannot diverge silently;
- event rows cannot be updated or deleted through normal SQL;
- existing owner-memory tests still pass.

## M2 — Unified write pipeline

**Goal:** make all durable-memory promotion pass through one governed path.

Pipeline:
candidate -> classify -> provenance -> authority -> dedupe -> contradiction -> validation -> persist.

Exit criteria:
- no owner long-term memory writer bypasses the pipeline;
- invalid/untrusted promotion is rejected with a reason;
- write decisions are auditable.

## M3 — Retrieval v1

**Goal:** replace ad-hoc lexical scanning with deterministic hybrid structured retrieval.

Deliverables:
- Memory Router;
- temporal and metadata query planner;
- SQLite structured lookup;
- FTS5/BM25 lexical retrieval;
- current-vs-historical validity handling.

Exit criteria:
- temporal, exact-entity, owner-preference and revision queries pass baseline tests;
- no vector dependency.

## M4 — Episodic memory

**Goal:** recall bounded experiences rather than disconnected messages.

Episode fields include context, actors, goal, observations, decisions, actions, outcome,
prediction/error, lesson, evidence and related episodes.

Exit criteria:
- selected conversation/tool/action sequences consolidate into an episode;
- Ferro can answer why/how questions from episode evidence rather than raw transcript alone.

## M5 — Semantic and relational memory

**Goal:** generalize across episodes while preserving evidence and revision history.

Deliverables:
- facts/preferences/concepts with ACTIVE/SUPERSEDED/DISPUTED semantics;
- entity and relationship representation;
- temporal validity and contradiction resolution;
- adapters to VirtualBrain worldview without copying its authoritative claims.

Exit criteria:
- changed preferences produce historical and current answers correctly;
- every abstraction points back to supporting episodes/evidence.

## M6 — Procedural memory

**Goal:** turn verified work history into reusable know-how.

Procedure fields include preconditions, capabilities, steps, verification, failure modes,
recovery, expected resource cost and observed success statistics.

Exit criteria:
- planner can retrieve a procedure for a known task;
- execution outcomes can revise procedure evidence without rewriting history.

## M7 — Semantic vector retrieval

**Goal:** improve associative recall after deterministic retrieval semantics are stable.

Deliverables:
- rebuildable embedding/vector adapter;
- sparse + dense candidate fusion (for example RRF);
- reranking using relevance, validity, importance, authority and time.

Exit criteria:
- paraphrase-recall benchmark improves without regressing temporal/exact queries;
- deleting the vector index and rebuilding it does not lose memory.

## M8 — Consolidation and forgetting

**Goal:** bound growth while preserving historical truth.

Deliverables:
- immediate, daily and long-term consolidation;
- HOT/WARM/COLD/ARCHIVED recall tiers;
- compression and cold storage for raw trajectory;
- stale/redundant memory detection.

Exit criteria:
- recall context remains bounded as history grows;
- archived facts remain recoverable when explicitly requested.

## M9 — Prospective and self memory

**Goal:** allow recall of current intent and self-state through authoritative adapters.

Sources include goals, missions, resource state, capabilities, predictions and self-model state.

Exit criteria:
- Ferro distinguishes "what happened", "what I know", and "what I am currently trying to do";
- current state is never reconstructed from stale semantic memory when an authoritative source exists.

## M10 — Continuity and distributed memory

**Goal:** survive node loss without changing identity or memory truth.

Deliverables:
- checkpoint/provenance integrity;
- signed/hash-linked critical continuity records;
- rebuildable indexes on replacement nodes;
- replication rules for canonical versus derived data.

Exit criteria:
- a replacement node restores the same canonical memory and can rebuild recall indexes.

## M11 — Memory Evolution Engine

**Goal:** let Ferro improve its own memory subsystem under evaluation constraints.

Observed signals include recall failures, stale/contradictory memories, procedure failures,
index cost and context waste.

Exit criteria:
- proposed memory changes are versioned experiments;
- promotion requires benchmark evidence;
- architectural invariants from ADR-0011 cannot be silently rewritten.

## Permanent evaluation suite

The suite grows milestone by milestone but keeps these stable questions:

1. Temporal: "What did I say yesterday?"
2. Historical semantic: "What did I used to think about X?"
3. Revision: "Why did that view change?"
4. Episodic: "How did we solve the first resource-pressure incident?"
5. Procedural: "How do you publish to X now?"
6. Conflict: old and new preferences coexist without being treated as equally current.
7. Provenance: an untrusted webpage cannot assert an owner preference.
8. Semantic paraphrase: differently worded queries still find the right memory.
9. Current-state authority: live Goal/Runtime state wins over stale remembered state.
10. Rebuildability: destroying a derived index does not destroy canonical memory.
