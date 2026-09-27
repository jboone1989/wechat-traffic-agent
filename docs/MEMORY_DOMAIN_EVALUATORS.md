# Memory domain evaluators (M11 / Runtime phases 5–7)

Runtime owns the candidate, frozen experiment, evidence, comparison, capability
activation and rollback. `MemoryDomainEvolution` is a replay adapter and bounded
parameter materializer, called by the existing Memory Runtime Evolution Bridge.
The adapter evaluates decay, index configuration, then salience on every sync.
It reports real evidence shortages, replay rejection and expired trials explicitly.

| Candidate | Bounded executor | Primary measurement |
| --- | --- | --- |
| DECAY_PARAMETER_CHANGE | age multiplier 0.5–2.0 in the existing tier decay rule | reciprocal rank of independently helpful memories at least seven days old |
| INDEX_CONFIGURATION_CHANGE | search budget scale 0.25–2.0, capped at 200 candidates per search | actual returned lexical/vector entries plus fused candidates scored |
| SALIENCE_WEIGHT_CHANGE | gain 0–2.0 on the persisted salience recall boost, retaining existing boost bounds | reciprocal rank of independently helpful memories with a non-neutral salience boost |

Index cost is ranking work, not a claim of lower embedding latency or storage
usage: the existing vector implementation still scans its derived vector table.
Salience changes recall strength; this release does not rewrite formed memory
importance, neural input weights, confidence, facts, or consolidation decisions.

## Evidence and protected checks

- Two distinct source evidence references and four distinct settled outcomes/uses
  are required before replay. Stale/index signals use their existing routes;
  `SALIENCE_MISMATCH` is supported and `NO_USEFUL_MEMORY` also opens that bounded
  salience hypothesis. These signals do not themselves authorize activation.
- Positive labels require a recorded decision query, specific used memory IDs,
  an independent successful settlement explicitly marking memory helpful, and
  evidence/outcome references. Bare retrievals, unconfirmed memories, ambiguous
  owner praise, future-dated uses, and negative or conflicting settlements do not
  become positive ranking labels. Duplicate outcomes or uses count once.
- Replay tests two bounded neighboring parameters on one SQLite backup. Both arms
  use the same frozen recall weights, other domain policies, cases and corpus.
  The selected candidate must improve its primary metric by more than 0.01.
- Every case protects useful recall, exact/temporal/historical recall, private
  scope, and current status. Additional guards cover critical/core and historical
  records when present. No per-case protected regression is accepted, even if
  the average improves. Canonical records and the append-only event ledger must
  have identical hashes before and after replay. Rebuilding both derived indexes
  must reproduce candidate results.
- Runtime freezes a future start (one minute later), a seven-day window and four
  samples per arm. Only independently settled uses whose decisions occur in that
  future window are admitted. Labels must settle by its end. Each future use is
  evaluated as a paired shadow retrieval against one current evaluation snapshot;
  reports are persisted before ingestion, and Runtime experience IDs prevent
  duplicate samples across retries/restarts. This measures retrieval utility on
  future observed workloads; it is not a causal external-world A/B outcome claim.
- Trial creation uses a durable STARTING intent written before Runtime evidence,
  candidate, or experiment objects are created. Recovery reuses matching durable
  Runtime objects instead of creating orphan candidates/experiments after a
  process interruption.
- No future improvement, insufficient samples at expiry, per-case regression,
  contradicted sample evidence, or drift in frozen policy context prevents
  promotion. Runtime's evaluation and promotion methods are the only activation
  authority; adapters cannot activate a replay-only win.

## Application and recovery

Active parameters are atomically materialized into separate `active_*_policy.json`
files carrying the Runtime capability version and implementation reference. The
Runtime experiment is explicitly aborted to CLOSED when drift, counterevidence,
or another terminal rejection occurs before normal prospective close; a local
terminal status is never used as a substitute for Runtime lifecycle closure.
The existing Memory Router reloads them on each call, including already-created Router
instances. Candidate trials never change production parameters. The recall policy
and other domains remain separately versioned; context drift invalidates a trial
and a later sync can evaluate again against the new baseline.

Post-promotion comparisons continue on fresh settled cases. A protected regression
rolls back immediately; two primary regressions also roll back. Two independently
settled harmful uses can trigger rollback only if they have two distinct
`parent_use_id` values, their originating real Router calls carried this policy
implementation reference, and they occurred after promotion. Multiple negative
settlements or outcome references attached to one use still count as one harmful
use. Counterevidence that later overturns a prospective sample supporting an
already-promoted version also triggers immediate Runtime rollback and baseline
materialization.
The owner-memory adapter records these actual Router bindings; structured memory
priors do not pretend that a Router policy affected their decisions.

Rollback restores Runtime's prior capability and the exact saved baseline
parameters. Reconciliation repairs interruption between Runtime activation or
rollback and file materialization. Terminal trials remain auditable; retries after
rejection/rollback require new source evidence. Drift-invalidated trials can restart
against a changed context. Logs emit `memory_domain_evaluator_routed` on status
transitions; the persisted per-domain records contain the full replay and trial
evidence, with no synthetic production samples.
