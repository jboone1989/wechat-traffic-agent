# ADR-0012 — Memory is a reusable Runtime capability

Status: Accepted  
Date: 2026-10-04

## Context

ADR-0011 correctly defined Ferro memory semantics, but placed the implementation boundary inside Ferro. The repository subsequently accumulated a large family of `memory_*.py` modules. That makes the capability difficult to reuse in another agent and encourages future agent projects to rebuild the same subsystem.

## Decision

Memory is no longer a Ferro-owned target subsystem.

Agent Runtime owns the provider-neutral `agent.memory` capability contract and routing. The memory implementation must be independently replaceable. Ferro's current MemoryService remains temporarily available only through `LegacyFerroMemoryCapabilityProvider` while the implementation is extracted.

The first stable surface is:

- `remember`
- `recall`
- `read`
- `changes`
- `health`

## Migration status

The canonical SQLite store has been physically extracted to
`agent_runtime.memory.durable`. Ferro's `memory_system.py` now only preserves
the legacy import path and retrieval-view transition hook while existing callers
move to the Runtime capability boundary.

Deterministic retrieval/indexing has also moved to
`agent_runtime.memory.retrieval`: canonical synchronization, FTS, validity,
graph navigation, changes and ranking fusion are now reusable Runtime code.
Ferro's MemoryService delegates these operations.

The existing local embedding/HNSW implementation now lives in the optional
`agent_runtime.memory.local_retrieval` provider. It retains lazy FastEmbed and
hnswlib loading plus the transient bounded inference worker.

Ferro's historical `memory_models.py` and `memory_ann.py` are compatibility
imports only. Architecture tests forbid FastEmbed/HNSW implementation code from
returning to those files.

Evidence/source ingestion now lives in `agent_runtime.memory.sources`.
Ferro delegates exact source archival, version selection, chunk creation,
source-view rebuild and evidence-revision invalidation to that module.

The source archive remains byte-for-byte compatible with the existing state
layout; no second memory database or source store is created.

Evidence contextualization now lives in
`agent_runtime.memory.contextualization`. Runtime owns pending-evidence
selection, bounded request construction, output validation, prefix persistence,
FTS refresh and vector invalidation.

Ferro retains only the adapter that invokes its current cognition provider and
returns `{chunk_ref: prefix}` to the Runtime contextualizer.

Relation enrichment now lives in `agent_runtime.memory.relations`. Runtime
owns edge validation, allowed predicates, provenance, canonical RELATIONAL
memory writes and idempotency.

Ferro's `enrich_relations` method only prepares the research fact packet and
adapts its current structured cognition call into the Runtime relation
generator.

Semantic consolidation now lives in
`agent_runtime.memory.consolidation`. Runtime owns the summary hierarchy,
semantic clustering, vector reuse, child-version cache and stale-summary
supersession.

Ferro's `consolidate` method now only adapts its current embedding provider and
structured cognition call into the generic Runtime consolidator.

Ferro's learning/research receipt schemas are intentionally **not** part of the
generic Memory capability. Their projection into durable memory now lives in
`learning_memory_bridge.py`. Production learning code calls that bridge
directly; MemoryService keeps compatibility wrappers only while older callers
migrate.

Architecture tests forbid Ferro-specific receipt file names and receipt kinds
from returning to MemoryService.

The common durable-memory formation pipeline now lives in
`agent_runtime.memory.formation`. Ferro's `memory_formation.py` is a
compatibility import only. Episodic, semantic and procedural memory continue to
use the same formation behavior while their own modules are migrated
independently.

ADR-0011 remains authoritative for the semantics of existing stored memories during migration, but its repository ownership assumption is superseded by this ADR.

## Consequences

Ferro callers should migrate toward Runtime's MemoryCapabilityClient instead of importing memory storage/retrieval implementations.

No new `memory_*.py` subsystem files may be added to Ferro. Existing files may be deleted as extraction proceeds.

The future standalone implementation may remain Python or be replaced by Rust/Go without changing Ferro, Runtime callers, or other capabilities, provided it passes the same capability contract and parity tests.
