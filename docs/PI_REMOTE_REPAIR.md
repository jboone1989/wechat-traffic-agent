# Pi Remote repair and Harness-RSI capability

`REQUEST_CODE_REPAIR` and `QUERY_CODE_REPAIR_STATUS` are registered by the
Content Agent composition root when Pi Remote is available beside the configured
self-repair workspace (or `PI_REMOTE_REPAIR_ROOT` points at a trusted checkout).

The production composition root binds the current Ferro Python interpreter and
`CONTENT_SELF_REPAIR_WORKSPACE` to the request-only Pi worker before runtime
capabilities are composed. Ferro does not receive Git, merge, deploy, or model
API credentials. ChatGPT cognition is requested through LivingRuntime's durable
cognition provider.

## Repair-RSI loop

A LOW-risk `RepairRequest` now drives this durable sequence:

1. Reproduce the authoritative evaluator baseline.
2. Give the ChatGPT proposer the bounded source/evaluator evidence, prior rejected
   hypotheses, and an annealed edit budget.
3. Produce multiple independent hypotheses.
4. Run an independent ChatGPT critic before code execution.
5. Create one child RepairRequest and isolated Git worktree per admitted candidate.
6. When the existing evaluator already passes, require an evaluator/regression
   edit that fails on the old source before the source repair is applied.
7. Apply the source repair, require the focused evaluator to pass, commit the
   candidate, and rerun it in the detached Reviewer worktree.
8. Select only candidates with measured gain above the configured noise/cost
   floor and required guards.
9. Persist selected and rejected hypotheses in `repair-rsi/history.jsonl`.
10. If a round is rejected, retry with the failed hypothesis history. After the
    round budget is exhausted, return that counterevidence to Ferro's
    `DIAGNOSING` state instead of silently stopping.

Each request has a single-instance worker lock. Status queries may recover a
stale or failed worker, but cannot run two RRSI workers for the same request.

An `APPROVED_CHECKPOINT` is still not a production deployment. It becomes the
input to the existing Guardian canary and A/B release path; promotion requires
that separate release authority. This keeps candidate generation/evaluation
separate from deployment while allowing the entire research loop before
promotion to run unattended.
