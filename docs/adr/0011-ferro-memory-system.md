# ADR-0011 — Ferro Memory System architecture contract

Status: Accepted
Date: 2026-09-19

## Context

Ferro already persists several forms of durable state: owner conversation trajectory,
owner long-term notes, VirtualBrain worldview and life experience, predictions, goals,
missions, resource state, and content strategy outcomes. These are useful but are not yet
one coherent memory system. Adding vector search directly would make retrieval better in
one path while leaving authority, temporal validity, revision, provenance, consolidation,
and procedural learning fragmented.

This ADR freezes the conceptual boundaries before implementation continues.

## Decision

Ferro Memory System has seven memory classes:

1. **CORE** — identity and durable governing principles.
2. **TRAJECTORY** — immutable raw observations, conversations, tool calls, actions and receipts.
3. **EPISODIC** — bounded situations containing context, decisions, actions, outcomes and evidence.
4. **SEMANTIC** — durable facts, preferences, concepts and generalized lessons.
5. **RELATIONAL** — entities and durable relationships among owner, agents, projects and resources.
6. **PROCEDURAL** — learned ways to perform work, including preconditions, verification and recovery.
7. **PROSPECTIVE** — future intentions that reference authoritative Goal/Mission/Task/Trigger state.

These classes do not replace authoritative domain state. Goals, missions, runtime resources,
VirtualBrain worldview, predictive learning and account state remain owned by their current
subsystems. Memory adapters may recall them, but must not create competing copies.

## Canonical versus derived data

Canonical truth includes raw trajectory, canonical durable memories, VirtualBrain checkpoints,
goal/runtime state and referenced evidence. Retrieval indexes are always derived and rebuildable.

Derived indexes may include B-tree/SQL indexes, FTS/BM25, vector embeddings, entity indexes,
graph projections, reranking caches and summaries. Loss of a derived index may reduce recall
quality but must never erase the underlying memory.

## Common durable-memory envelope

Every canonical durable memory uses a versioned envelope with:

- `memory_id`, `memory_type`, `subject_id`, `schema_version`;
- `created_at`, `observed_at`, `valid_from`, `valid_until`;
- lifecycle `status`: `ACTIVE`, `SUPERSEDED`, `DISPUTED`, `RETRACTED`, or `ARCHIVED`;
- `importance`, `confidence`;
- provenance via `source_type`, `source_refs`, `evidence_refs`;
- revision links `supersedes`, `superseded_by`;
- `entities`, `topics`;
- access scope and a deterministic `content_hash`;
- type-specific payload.

A new statement never rewrites historical trajectory. A changed belief or preference creates
a revision and preserves the earlier state.

## Recall contract

All future memory retrieval goes through one logical `MemoryRouter`. Callers request recall;
they do not choose a database or vector backend directly. The router may combine:

- structured and temporal SQL lookup;
- lexical FTS/BM25;
- semantic vector retrieval;
- entity/relationship retrieval;
- adapters to authoritative runtime/worldview/goal state.

Candidate results are fused, validity/conflict rules are applied, then reranked into an explicit
Working Memory packet. Similarity alone is never the authority rule.

## Formation and consolidation contract

Raw events are not automatically long-term memories. Durable memory formation must pass through
a single formation pipeline that classifies candidates, attaches provenance, deduplicates,
detects contradictions, validates authority and persists accepted records.

Consolidation operates at three time scales: immediate important-event formation, daily episodic
consolidation, and longer-term semantic/procedural abstraction. Derived memories retain
`derived_from` evidence so Ferro can explain why it remembers them.

## Forgetting contract

Forgetting changes recall priority, not historical truth. Canonical trajectory is retained or
cold-archived. Durable memories may move through HOT/WARM/COLD/ARCHIVED recall tiers. Physical
deletion is not the normal forgetting mechanism.

## Authority contract

- CORE changes require explicitly authorized identity/governance sources.
- OWNER memory may form from authenticated owner conversation.
- WORLDVIEW remains authoritative in VirtualBrain and changes only through its evidence rules.
- PUBLIC conversation can produce bounded audience-interest evidence, not identity or owner facts.
- PROCEDURAL memory changes from verified execution outcomes.
- RUNTIME state changes only from actual runtime events.

Untrusted content must never promote itself into a higher-authority memory class.

## Implementation order

Implementation must follow M0 through M11 in `docs/MEMORY_SYSTEM_ROADMAP.md`. Later milestones may
refine implementation technology, but they may not bypass the boundaries above.

## Non-goals

This ADR does not mandate a particular vector database, graph database, embedding model or
reranker. It intentionally prevents those implementation choices from defining Ferro's memory
semantics.

## Consequences

Positive: Ferro can evolve retrieval technology without changing what counts as truth; historical
revision remains inspectable; existing VirtualBrain and runtime state are reused rather than
duplicated; procedural and episodic learning have explicit destinations.

Cost: the system requires migration adapters and more explicit schemas before semantic-vector
retrieval is introduced.
