# Architecture boundary

This repository is a domain agent hosted by Agent Runtime.

It owns only:

- audience and topic inputs;
- canonical content artifacts, provenance and editorial decisions;
- channel-specific formatting (Website and legacy WeChat 题图);
- channel-scoped traffic-feedback interpretation;
- local content packaging and static website candidates.

It must consume, rather than reimplement, Agent Runtime facilities for identity,
goals, durable memory, context projection, model backends, authorization,
evidence, recovery, and execution policy.

The persistent generation worker has no publishing actuator. Its output is an
immutable private draft. Local operator review can admit a separate projection
for an explicitly requested website build; it does not deploy the build.

ADR-0010 and `docs/CONTENT_AGENT_PLAN.md` define the current product boundary.
Trading evidence enters through Agent Network only. Editorial approval cannot
grant Trading execution permissions or mutate the source draft.

## Evolution ownership

Ferro self-improvement is governed by Agent Runtime's canonical Evolution Engine.
This repository contributes domain observations, outcome adapters, metrics, replay
adapters, and bounded mutation executors; it must not implement a second promotion
or activation state machine. See `docs/EVOLUTION_ENGINE_INTEGRATION.md`.
