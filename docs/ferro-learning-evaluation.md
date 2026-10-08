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
