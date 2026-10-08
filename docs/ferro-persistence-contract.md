# Ferro persistence contract

The purpose of durable storage is to let Ferro **remember what was learned,
recover incomplete work, and prove real-world effects**. It is not to keep
copies of each prompt and every polling tick.

## Must persist (authoritative)

| Durable record | Why |
| --- | --- |
| Agent Runtime memory database, canonical evidence and source archive | Long-term facts, revisions, relationships, learning, citations |
| Minimal neural parameters and active goals/checkpoint | Resume behavior and learning after restart |
| Active tasks, next-run times, retries, outbox, idempotency keys | Resume work without duplicate external effects |
| Published articles SQLite and website design/assets | Ferro's independently editable public identity |
| X/OKX/website **POSTED** receipts, stable URL/IDs, verified effect observations | Prove actual publication and outcome |
| Experiments and settled prediction outcomes | Determine whether strategies worked |
| Secrets, account identity, authorization/session state | Continue authenticated execution |

Do not delete these based on age or size alone.

## Retain only during the operation / short diagnostics window

| Ephemeral record | Lifecycle |
| --- | --- |
| Full `inputs.json` (news + website inventory + prompt context) | Used for in-flight recovery; remove after a terminal result. Legacy completed cycles: prune after 7 days. |
| Unlinked `website-engagement-snapshot.v1` metric snapshots | Avoid writing if its metric window is already recorded; prune after 7 days with no committed ChannelEvidence reference. |
| Result of read-only neural projections | Respond once, then discard; not a memory. |
| Intermediate model contexts, candidate dumps, temporary search results | Keep only while a task may resume; do not confuse them with evidence sources. |
| Ordinary logs and observability buffers | Short retention; keep incident diagnostics when still needed. |

Keep immutable learning receipts and original provenance until their substantive
facts/sources have been imported into canonical memory and their downstream
consumers have been switched to database reads. Replacing all legacy cycle
files at once would break X outcome verification and historical display.

## Cleanup invariants

- Never prune an unfinished cycle, an unacknowledged external action, the
  memory database, source archive, or a published receipt.
- The state cleanup CLI defaults to dry-run, limits batch size, and only
  targets two independently audited cache classes; it leaves all other
  schemas alone.
- Readable ChannelEvidence references protect original metric snapshots.
  If evidence records are corrupted, cleanup aborts instead of guessing.
- Prior rollout: `scripts/prune_redundant_ferro_state.py --state-root ... --limit ... --apply`.
- Next refactor: move live/analytics/history consumers from cycle directories
  into one indexed store; only then retire the rest of the historical context.
