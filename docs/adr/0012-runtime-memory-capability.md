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

ADR-0011 remains authoritative for the semantics of existing stored memories during migration, but its repository ownership assumption is superseded by this ADR.

## Consequences

Ferro callers should migrate toward Runtime's MemoryCapabilityClient instead of importing memory storage/retrieval implementations.

No new `memory_*.py` subsystem files may be added to Ferro. Existing files may be deleted as extraction proceeds.

The future standalone implementation may remain Python or be replaced by Rust/Go without changing Ferro, Runtime callers, or other capabilities, provided it passes the same capability contract and parity tests.
