# Ferro learning evaluation — isolated paired benchmark

**Purpose:** Tell apart (1) stored observations, (2) usable knowledge on
unseen tasks, (3) retained competence after revisions, and (4) proven task
improvement. These are different claims and must not share one score.

## Mature components reused

- Agent Runtime canonical memory + Ferro's existing `MemoryRouter` and FTS5
  index. No second memory backend or vector model is introduced.
- Existing isolated AWE evaluator contestant and ChatGPT-only provider; the
  same Ferro identity, drive and self-model are used in both arms. Only
  `recalled_memories` is withheld from the control arm.
- Standard *task / frozen dataset / independently supplied scorer* pattern
  used by [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai),
  [OpenAI Evals](https://github.com/openai/evals), AgentBench and LoCoMo.
  No new framework installed on VM09 (memory and recurring API budget matter).
- Exact paired sign test plus immutable SQLite score log rather than
  self-rating, replaying a task description, or counting memory rows.
- Existing `LearningTransfer` remains the real-world procedural A/B trial;
  the new `LearningEvaluation` measures *cognitive knowledge use*.

## Separation of responsibilities

`CanonicalMemoryStore` holds what was learned.
`FerroExternalEvalContestant` chooses answers using a frozen snapshot.
`LearningEvaluation` owns tasks, oracle gold and comparison scores.
`LearningEvalLedger` owns run version, answers and immutable results.

The evaluator is a **standalone capability used by Ferro** without needing
a content-posting cycle. At present its adapter lives in
`wechat_traffic_agent`; it has not yet been moved to the Agent Runtime
library, which already has concurrent ongoing development.

## Three validations

1. **Acquisition:** The candidate memory has a unique source reference and a
   meaningful evidence-backed explanation. This is necessary, not sufficient.
2. **Transfer:** Run retention, near and far transfer tasks. Each asks about
   unseen situations rather than repeating the memory text, with frozen
   action options and a hidden independently authored gold decision.
3. **Contribution:** Same model/provider, same prompt, same task and agent
   context. Alternate the memory-off/on call order. Report wins/losses/ties,
   split-level scores and paired significance. Do not call a gain causal until
   independent gold review, sample adequacy and replication.
4. **Forgetting:** Re-run the identical versioned benchmark after changing
   memory. Compare the two saved run IDs; require exact benchmark SHA and
   model ID. Report retention regressions even if the total score rises.

## Pilot on VM09

The committed `content/learning/ferro_evaluation_pilot_v1.json` has six
cases and `PROVISIONAL` oracle status. It samples existing research about
memory causal audits and execution-trace evaluation. Gold has **not** yet
been independently reviewed; its scores **cannot** support a claim of
learning benefit. `content/learning/ferro_eval_memory_ids_v1.json` lists
three specific, verified-active research-understanding memory IDs.

Prepare a small non-production snapshot:

```bash
python scripts/prepare_learning_eval_snapshot.py \
  --source-state-root /home/ubuntu/content-agent-state \
  --snapshot-root /home/ubuntu/ferro-learning-eval-20261008-a \
  --memory-ids-file content/learning/ferro_eval_memory_ids_v1.json
```

For a no-model retrieval check (no API cost, no skill claim):

```bash
python scripts/run_learning_evaluation.py \
  --snapshot-root /home/ubuntu/ferro-learning-eval-20261008-a \
  --benchmark content/learning/ferro_evaluation_pilot_v1.json \
  --ledger /home/ubuntu/ferro-learning-eval-20261008-a/evals.sqlite3 \
  --inspect-only
```

Remove `--inspect-only` to run the **same ChatGPT-only model**
twice per case; no API fallback is permitted. This is an explicit command,
not a background scheduled workflow, because each evaluation costs model
calls, CPU and time. The default is the existing FTS5 lexical retrieval
mode to avoid starting local embedding downloads on VM09. Passing
`--retrieval-mode full` uses production-equivalent hybrid retrieval and
must be reported as a separate configuration.

After independent review, publish an approved benchmark version with a
new hash; never silently change the gold of an existing benchmark.
Preserve failures and timeouts as failures, not zero or correct answers.

Compare two completed score runs against **identical case set and model**:

```bash
python scripts/run_learning_evaluation.py \
  --snapshot-root /home/ubuntu/ferro-learning-eval-20261008-a \
  --benchmark content/learning/ferro_evaluation_pilot_v1.json \
  --ledger /home/ubuntu/ferro-learning-eval-20261008-a/evals.sqlite3 \
  --compare-before RUN_ID --compare-after RUN_ID
```

## Reference benchmark and validity rules (2026-10-08)

The first pilot is too small and its multiple-choice responses are biased
toward the obviously cautious choice. Correct guesses are *not* evidence
that knowledge was transferred. We will not promote this pilot as a
proof. As a mature direction, follow
[MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench):
Accurate Retrieval, Test-Time Learning, Long-Range Understanding, and
Conflict Resolution. Prefer independently curated held-out examples over
rewording Ferro's own research notes. We reuse its assessment dimensions,
not an unreviewed claim of official benchmark scores.

**Anti-pseudoreplication:** The scorecard clusters all items whose
`source_refs` overlap, including indirect (transitive) links, so 48
paraphrases of one paper count as one independent source group. A
statistically supported comparison requires at least 24 scored items,
12 independent source groups, coverage in every transfer split, and a
positive paired sign test at the *source-cluster* level. These thresholds
are an early reporting gate, not a sufficient causal proof.

**Review separation:** Setting `review_status: APPROVED` in a dataset
does not validate the gold key. A separately authored review receipt must
bind the exact dataset SHA-256, every case ID, reviewer attribution,
review date and checked source evidence. It is a record of attestation,
**not cryptographic verification of the reviewer's identity or correctness**.
To supply the receipt:

```bash
python scripts/run_learning_evaluation.py \
  --snapshot-root /home/ubuntu/ferro-learning-eval-20261008-a \
  --benchmark content/learning/ferro_evaluation_pilot_v1.json \
  --ledger /home/ubuntu/ferro-learning-eval-20261008-a/evals.sqlite3 \
  --review-receipt path/to/independent_review.json
```

Receipt fields: `benchmark_sha256`, `reviewed_case_ids`,
`reviewed_by`, `reviewed_at`, `source_evidence_checked: true`.
A changed question or gold option invalidates the receipt. Without
the receipt the evaluator still records exploratory measurements but
cannot label them evidence of memory benefit.

The exploratory one-case factual pilot in VM09 state uses an active
source-linked memory about the guidance/retrieval factorial counts.
It is intentionally **not** a formal test, because its gold is sourced
from Ferro's own research record and has not been independently audited.
Its frozen lexical retrieval check found the relevant record. The remote
host requested execution approval before running the real ChatGPT-only
pair; no measured model score was obtained.

## What can be claimed today

- New research was stored: supported by original source-linked memory rows.
- Memory affects choices in a *mock-provider integration test*: supported
  only for the harness, **not** for live Ferro intelligence.
- The frozen VM09 snapshot can retrieve relevant history for six pilot cases.
- Real-model accuracy, transfer gains and resistance to forgetting remain
  **not measured**. Procedural trials in the live learning ledger remain
  separate, and currently lack enough real production outcomes to claim
  capability improvement.

Neither richer prompts, a higher model self-rating, nor the presence of
more memory records constitutes evidence that Ferro has learned effectively.


## Browser transport isolation and first real-model pair (2026-10-08)

The evaluator uses the original authenticated **JEV shared Chrome profile** at
`127.0.0.1:9600`, but now passes the logical
`session_id="ferro-learning-evaluation"` to the existing Agent Runtime
ChatGPT provider. This gives evaluation an independent conversation/receipt
and, when the production shared browser carrier has an unresolved submission,
its own tab **in the same Chrome process**. It does not create another
browser instance, login session, API route or memory service.

Previously both evaluation and production used `chatgpt-cognition`,
whose pending request was acknowledged as clicked without appearing in the
server conversation. The baseline evaluation then spent time recovering
an unrelated production request instead of answering the benchmark.
The live page inspection confirmed no matching user text or assistant
reply on the bound conversation; the original pending receipt was not
deleted or overwritten.

After isolating the logical session, a real-model run produced:
- Run ID: `a6d14600a0d94a53a332165d7c1af968`.
- `1/1` scored exploratory fact-retention case, memory OFF correct,
  memory ON correct, pairwise wins/losses `0/0`.
- `PROVISIONAL` unreviewed answer key, only one question from one
  source group. **Neither a memory benefit nor long-term learning gains
  are established.**
- Browser transport confirmed both model turns completed and the same
  frozen evidence snapshot was preserved.

Do not rerun evaluation using the production `chatgpt-cognition` journal;
the separate receipt name is part of its runtime contract. Production
pending model requests must be reconciled by their owning session rather
than silently cleared for evaluation.
