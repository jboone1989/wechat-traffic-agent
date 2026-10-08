# Ferro self-learning rebuild — evidence before accumulation

## Why

The 2026-10-08 production audit found:
- 1,788 lifetime episodes but just 74 different episode *texts*;
- 156 experiential knowledge revisions sharing **three** generic principles;
- 109 application records, of which 108 had status UNRESOLVED;
- 50 source-linked research explanations (five ANSWERED and 45 PARTIAL);
- two active learned procedures, one with two variants and 22 observed successes /
  27 observed failures across its recorded variants.

There are more distinct payloads than text, so this is **not** evidence that
all experience is lost. It **is** evidence that the agent's durable, retrievable
learning signal is dominated by templates, unresolved trials and unmeasured
generalizations. None of these numbers establishes causal ability gains.

## Research grounding and what Ferro borrows

| Research / implementation | Transfer into Ferro | Do not assume |
| --- | --- | --- |
| [AI Agents in Depth ch. 9](https://bojieli.github.io/ai-agent-book/book/chapter9/) | Separate *recording, outcome evaluation, experience comparison, adoption, retention*; update knowledge / instruction / executable skill independently | "saved logs" == "learning" |
| [Reflexion](https://github.com/noahshinn/reflexion) | An observed failure must change the next trial and be evaluated | A reflection is a true explanation by itself |
| [ExpeL](https://github.com/LeapLabTHU/ExpeL) | Retrieve concrete successes **and** failures to infer reusable strategies across tasks | Count similar text as multiple independent observations |
| [Voyager](https://github.com/MineDojo/Voyager) | Keep executable verified skills and environment-feedback-based self-correction separate from declarative knowledge | Automatically apply a Minecraft curriculum to an online writing agent |
| [ACE](https://github.com/ace-agent/ace) | Evolve short, evidence-specific playbook **deltas**; preserve older tested variants | Rewrite the entire prompt or compress away the conditions of use |
| [Self-Refine](https://github.com/madaan/self-refine) | Iterate with feedback only when it changes a verifiable output | Self-critique alone proves improvement |
| [Generative Agents](https://arxiv.org/abs/2304.03442) | Episodic context supports retrieval, salience and planning | Episodic accumulation alone means increased competence |

## Actual production data flow

```
world observation / source / external action
    -> raw source & recovery receipt (evidence, not new knowledge)
    -> evaluate change and link exact source
    -> source-grounded research finding OR verified task outcome
    -> canonical memory (only genuinely learned knowledge)
    -> choose next question/method/skill with current memory
    -> external execution / follow-up observation
    -> settle outcome; compare against previous procedure
```

The source archive and canonical memory remain Agent Runtime components.
Ferro remains the domain owner of questions, verified action receipts, and
content expression. No second independent memory framework is introduced.

## Changes in this revision

1. VirtualBrain autobiographical records are real, independently searchable
   observations; the fallback `preserve lived experience` label is removed.
2. A life-event's **source category** no longer manufactures a new lesson.
   Explicit concrete event lessons with an observation reference can be
   consolidated; raw events are otherwise episodes only.
3. REPEATED / UNRESOLVED research results never become new canonical facts.
4. UNRESOLVED prediction tests stay in recoverable short feedback and source
   receipts; they are not active permanent application-learning memories.
   Old mistaken projections can be version-archived (not deleted).
5. After two incomplete attempts to answer the same question (including topic
   aliases), the proposed new action must change the external search query
   **and** describe its method change, or the decision is rejected.
6. `scripts/audit_ferro_learning.py` reports distinct source-linked findings,
   accepted explanations, settled transfers and procedure outcomes. It always
   reports that causal capability gain is unproven without a paired evaluation.

## Next pieces: measured improvement, not yet claimed

- **Controlled comparison**: freeze a set of domain tasks and real-world
  source snapshots; run the *same tasks* with memory/playbook enabled and
  disabled. Score correctness, source grounding, novelty, runtime cost and
  ability to finish, while preserving input parity.
- **Counterfactual skill trial**: log the old variant, new variant and
  independently verified world outcome. Promote a changed instruction or
  procedure only after an out-of-sample comparison shows improvement.
- **Research completion**: track unresolved focus and artifact needs
  independently of publishing. A blocked inquiry should resume when a
  specified document, code path, result or measurement becomes available.
- **Shadow evaluation**: no automatic claim of improved X audience or financial
  results until attribution, observation windows and external changes are
  accounted for.

### Acceptance bar

A future release may claim self-learning only if it shows all of:
- distinct new source-grounded observations and explanations, not paraphrases;
- changed behavior in at least one subsequent real task, with previous/new
  method recorded;
- an independently verified outcome and comparison baseline;
- no measurable regression on previously mastered tasks.

More memories, model self-ratings, and richer prose are **not** success metrics.
