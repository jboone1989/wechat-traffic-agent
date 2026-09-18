# ADR-0011: World-settled value, experiment governance, and metabolic action limits

- Status: Accepted
- Date: 2026-09-18
- Scope: Content Agent / VirtualBrain integration

## Context

The Content Agent already receives real reader outcomes through Website evidence and VirtualBrain learning,
but several internal layers could still award value before the world had supplied evidence. Publication success
was treated as external value, process success could increase interest, Website experiments could measure forever
without controlling future behavior, bounded ContentMind state silently discarded old autobiographical evidence,
and resource pressure was mostly descriptive rather than action-controlling.

These behaviors create reward leakage: the agent can learn that its own successful execution is valuable even when
readers did not respond.

## Decision

### World-settled audience value

Reader value is deterministic and evidence-backed. The primary audience reward combines:

- reach relative to a trailing pageview baseline; and
- reader quality measured as engaged_readers_30s / tracked_pageviews.

The two dimensions are combined harmonically so raw traffic volume is not counted twice. Missing denominator,
missing engagement coverage, or missing baseline stays UNKNOWN and is not coerced to zero.

Pageviews remain a diagnostic metric. Strategy control and experiment promotion use the composite audience reward.

### Resource outcomes

Content cycle completion records real cost immediately but leaves external, learning, and reproduction value UNKNOWN.
Delayed value is appended through VirtualBrain OutcomeSettlement revisions; historical Outcome objects are never edited.

begin_cycle learns expected value only from settled dimensions. Unsettled outcomes are excluded from the mean.

Process checks describe execution quality and effort; they do not self-award audience value.

### Prospective predictions

The Content Agent predicts both pageviews and, when supported by prior engagement evidence, engaged_readers_30s for
the mature 24h window. Both targets settle through VirtualBrain's prospective predictive-learning ledger.

### Experiment governance

The legacy fixed-horizon pageview evaluator remains read-only diagnostic evidence. Promotion and revocation authority
uses a separate fixed-horizon audience-reward evaluation with the same minimum sample, alpha, and minimum-lift gates.

A favorable conclusion is immutable. The promoted opening-mode version uses deterministic 90/10 champion/holdout
assignment so contemporary counter-evidence remains available. A statistically significant concurrent holdout reversal
creates an immutable revocation and returns assignment to the pre-promotion experiment.

LLMs do not promote or revoke experiments.

### Memory durability

ContentMind overflow is archived rather than silently discarded. A compact persistent dedupe ledger remains authoritative
for already-observed event ids so replaying an old event cannot create a second learning event after checkpoint trimming.

### Metabolic action limits

Resource pressure affects behavior:

- NORMAL / WATCH may execute normally within resource availability.
- CONSERVE executes only when free/windfall resources are available; otherwise it DEFERs and slows the next content wake.
- CRITICAL enters HIBERNATE and suppresses expensive cognition/generation while preserving resource review and outcome checks.

A balance increase is first recorded as an unresolved BalanceIncreaseEvent with source UNKNOWN. It is not automatically
classified as sustenance. Recovery can happen from improved resource state or an audited operator override; positive
reader lift is useful evidence but is not the sole wake-up key, avoiding a permanent death lock.

Operator force-wake actions create receipts.

## Consequences

The agent can no longer prove value by publishing successfully. Real-world outcomes may arrive later than cost, so
decision state must tolerate UNKNOWN values. Experiment control becomes slower but causally cleaner. Hibernation can
reduce output under pressure, but it remains reversible through real resource recovery, verified free capacity, or an
audited operator action.

## Cross-repository contract

VirtualBrain owns the immutable Outcome / OutcomeSettlement causal ledger. Content Agent owns domain-specific audience
reward construction and settlement evidence. Neither repository may reinterpret missing evidence as zero.
