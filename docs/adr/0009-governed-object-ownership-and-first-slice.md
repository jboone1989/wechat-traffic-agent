# ADR-0009 — Governed-object ownership and first-slice execution semantics

Status: Accepted (GAI v3.1 execution freeze)  
Date: 2026-09-09  

Related pending work: P8 product-identity freeze.  
ADR-0009 is self-contained and does not depend on uncommitted ADR-0008.

Architecture status: **READY WITH REQUIRED PROTOCOL FIXES**

This commit is an A0/A1 limited authority audit plus protocol freeze. It does
**not** implement GAI, does not extract a generic package, does not move Trading
evolution code, does not modify Runtime learning APIs, and does not add tables
or services.

Allowed to start after this freeze: A0/A1 (this document), W0 correctness, W1
provenance, W2 Persistent Observer.

Forbidden until G0/G1 execution-protocol blockers close: automatic governed
apply, generic GAI package extraction, automatic policy adoption.

---

## 1. Frozen ownership

### Agent Runtime

Owns: identity, lifecycle, authorization, budget, scheduling, execution,
checkpoint/recovery, consumer actual-use recording, Runtime-side binding.

Runtime does **not** prove domain policy is valid. Runtime binding is **not**
the target state itself.

### Domain Agent

Owns: research, belief, hypothesis, experiment design, attribution,
context/regime semantics, candidate space, domain evaluation, bounded local
adaptation, persistent candidate generation.

This repository is the WeChat/financial-narrative Domain Agent.

### Domain Executor

Owns: final precondition, actual apply, authoritative read-back, stop,
compensation, safe-state recovery.

The executor proves what the target was actually observed to be.

### GAI v1

Owns **single-object governed change** only:

candidate identity, immutable artifact, baseline revision, authority,
authority epoch, grant, atomic attempt admission, conflict exclusion, receipt,
revocation, `UNKNOWN` / `RECOVERY_REQUIRED`, audit, idempotency.

Does **not** own: learning algorithm, causal attribution, universal confidence,
reward, domain PASS/FAIL, experiment design, business rollback.

### VirtualBrain

Owns: affect, motivation, appraisal, drives, self-model, VirtualBrain-own
evolution.

Must not: issue a business `AdoptionGrant`, mutate domain governance bounds,
lower the Evidence Floor, or bypass the Domain Executor.

Audit finding: current VirtualBrain code matches this boundary. There is no
business `AdoptionGrant` type. `SubjectAdoptionStream` is a research
capability-assessment stream, not a business grant ledger.
`GovernedSelfEvolution` treats constitution as immutable.

---

## 2. Three truth sources

| Source | Answers | Must not be inferred from |
| --- | --- | --- |
| Authorization truth | Who allowed which immutable change | receipts, bindings, usage |
| Target-state truth | What the target actually became | grant existence, Runtime binding |
| Actual-use truth | Which revision a consumer action really used | latest grant, latest receipt, latest ObjectBinding |

Forbidden inferences:

- receipt exists → Runtime has loaded the revision
- Runtime binding updated → the external target has changed

---

## 3. Frozen GAI v1 execution rules

These rules are frozen for G0/G1. Implementing them is later work. First-slice
code must not contradict them.

### 3.1 Atomic attempt admission

A single protected operation must:

1. verify grant exists
2. verify grant is not revoked
3. verify `execute_before` has not expired
4. verify `authority_id` matches
5. verify `authority_epoch` matches
6. verify `governance_policy_ref` still permits execution
7. verify `expected_revision == current revision`
8. verify no conflicting `STARTED` / `UNKNOWN` / `RECOVERY_REQUIRED`
9. verify the grant has not already been consumed by an attempt
10. create a unique `ApplyAttempt`
11. set status `STARTED`
12. acquire exclusive execution ownership of that governed object

These checks must not be split into `check → unlock → later create STARTED`.

### 3.2 One grant, one attempt

One `AdoptionGrant` admits at most one attempt. Retry reuses the same
`attempt_id`. A new execution requires a new grant.

`grant_id != attempt_id` is only an identity distinction. The invariant is:

`grant_id` → max one accepted `attempt_id`.

### 3.3 Revocation

- Before admission: do not create an attempt.
- After admission: do not assume the domain operation can be cancelled.
  Enter domain stop / recovery semantics.
- Revocation must not delete `STARTED`, `UNKNOWN`, or `RECOVERY_REQUIRED`.

### 3.4 Authority transfer

`authority_epoch N → N+1`:

- unused old grants become invalid immediately
- already `STARTED` / `UNKNOWN` attempts must not be deleted or pretended invalid
- reconcile / recover first
- the new authority must not overwrite unresolved old executor state

### 3.5 External write eligibility

A GAI DB lock is not enough for an out-of-process target. Automated apply
requires at least one of:

- **A.** target conditional write (`apply` only if current revision == expected)
- **B.** stale-writer rejection (target rejects old epoch / fencing token)
- **C.** single controlled write proxy (all writes serialize through one boundary)

If the target can be mutated by arbitrary scripts, cannot detect out-of-band
writes, cannot confirm actual state, or cannot reject a stale executor, it is
not eligible for strict automated governed apply. Mark
`ADVISORY / MANUAL_CONFIRMATION_REQUIRED`.

### 3.6 Receipt vs observation

- `APPLIED` requires the domain expected postcondition after authoritative
  read-back. Command return code 0, a changed revision, or API success is not
  enough.
- `NOT_APPLIED` requires evidence that the change did not happen. Timeout must
  not become `NOT_APPLIED`.
- `UNKNOWN` may leave `observed_revision` / `state_ref` empty. It must not fill
  last-known state as if it were a current confirmation.
- `RECOVERY_REQUIRED` means ordinary apply must not continue; domain
  reconciliation is required.
- A later confirmation must not overwrite history. `12:00 UNKNOWN` then
  `12:10` actually applied appends a reconciliation / later observation.

### 3.6.1 Attempt status and receipt outcome

The canonical wire/schema field is `ApplyAttempt.status`. Do not introduce a
second field named `current_status`. `status` **is** the current resolution.

`ApplyAttempt.status` and the latest authoritative `ApplyReceipt.outcome` are
not independent truths. Receipts are observations that set that resolution;
they do not form a second ledger.

Normative transitions:

```text
admission:
  no receipt
  attempt.status = STARTED

authoritative observation:
  receipt.outcome = X
  attempt.status = X

X ∈ APPLIED / NOT_APPLIED / UNKNOWN / RECOVERY_REQUIRED
```

Illegal combinations (against the **latest** authoritative receipt, not against
retained history) include:

- `attempt.status = STARTED` with any receipt
- `attempt.status = APPLIED` with latest `receipt.outcome = UNKNOWN`
- `attempt.status = NOT_APPLIED` with latest `receipt.outcome = APPLIED`
- any status / latest-outcome pair that is not the identity mapping above

Receipt append identity/order:

- each `ApplyReceipt` has immutable `receipt_id`
- each receipt for one `attempt_id` gets a monotonic `receipt_sequence`
  starting at 1, assigned atomically at append
- “latest authoritative receipt” is the receipt with the highest committed
  `receipt_sequence` for that `attempt_id`
- `observed_at` is when the executor observed the target; it is not the
  ordering key
- equal `observed_at`, delayed delivery, replay, or storage reordering must
  not change which receipt is latest
- appending the same `receipt_id` is idempotent and must not allocate a new
  sequence

Authoritative observation is **one atomic operation** for a given
`attempt_id`. In that single protected operation, all of the following
succeed or none do:

1. allocate the next `receipt_sequence` (or detect an existing `receipt_id`)
2. append the immutable receipt
3. set `ApplyAttempt.status` to exactly that receipt’s `outcome`

A crash after committing a receipt but before updating `status` is not a
legal persisted state. Restart/recovery must not choose between a later
receipt and a stale `status`; the atomic record is the source of truth.

Replay of an existing `receipt_id` returns the already-committed resolution
and must not allocate another sequence. Conflicting payload for the same
immutable `receipt_id` is rejected; history is not modified.

If `UNKNOWN` is later confirmed `APPLIED` (or any other terminal X):

- the original UNKNOWN receipt/event must be retained
- append a reconciliation / later observation with a new `receipt_id` and
  the next `receipt_sequence`
- `attempt.status` may update to the new current resolution
- historical facts must not be overwritten or deleted


### 3.7 Out-of-band modification

An out-of-band change invalidates the old baseline. An old grant must not apply
against a silently mutated object.

### 3.8 Governance policy binding

`AdoptionGrant.governance_policy_ref` is immutable. It records which rules
authorized the grant (for example whether `evaluation_refs=[]` is legal). A
material policy change refuses unused old grants. v1 does not infer policy
compatibility.

### 3.9 Local adaptation (W7 constraint, recorded now)

Bounded surfaces register governed baseline, min, max, max delta per update,
max cumulative delta, TTL, reset semantics, owner, and shared cumulative
budget id. Cumulative drift is relative to the latest formally approved
baseline. TTL expiry restores that baseline or enters revalidation. Restart
must not keep an adapted value while clearing the drift record. Multiple
adaptors on one surface share one cumulative budget, including VirtualBrain
attention/research allocation on the same surface.

---

## 4. GAI v1 contract (schema freeze, not implementation)

`schema_version` is fixed. `object_id` values are namespaced.

Digest algorithm and canonicalization are fixed in v1; no algorithm
negotiation.

Identity fields are references. Authentication is a Runtime/deployment trust
boundary, not a GAI field.

| Type | Required fields |
| --- | --- |
| `ArtifactRef` | `locator`, `digest`, `media_type` |
| `ObjectBinding` | `schema_version`, `object_id`, `authority_id`, `authority_epoch`, `executor_id`, `revision`, `confirmed_state_ref` |
| `ChangeCandidate` | `schema_version`, `candidate_id`, `object_id`, `expected_revision`, `change_artifact_ref`, `submitted_principal`, `submitted_at`, optional non-authoritative `source_metadata` |
| `EvaluationAttestation` | `schema_version`, `report_id`, `candidate_digest`, `evaluation_profile_ref`, `evaluator_principal`, `report_artifact_ref`, `completed_at` |
| `AdoptionGrant` | `schema_version`, `grant_id`, `object_id`, `authority_id`, `authority_epoch`, `candidate_digest`, `expected_revision`, `governance_policy_ref`, `evaluation_refs[]`, `issued_principal`, `issued_at`, `execute_before` |
| `ApplyAttempt` | `schema_version`, `attempt_id`, `grant_id`, `started_at`, `status` ∈ {`STARTED`, `APPLIED`, `NOT_APPLIED`, `UNKNOWN`, `RECOVERY_REQUIRED`}. `status` is the current resolution. After admission it remains `STARTED` until the first receipt; thereafter it must equal the latest authoritative `ApplyReceipt.outcome`. There is no separate `current_status` field. |
| `ApplyReceipt` | `schema_version`, `receipt_id`, `attempt_id`, `receipt_sequence`, `executor_id`, `outcome` ∈ {`APPLIED`, `NOT_APPLIED`, `UNKNOWN`, `RECOVERY_REQUIRED`}, optional `observed_revision` / `observed_state_ref`, `observed_at`, optional `details_ref`. `receipt_sequence` is monotonic per `attempt_id` and assigned atomically at append. Latest authoritative receipt = highest committed `receipt_sequence`. Later receipts append; they do not rewrite earlier ones. |
| `GrantRevocation` | `schema_version`, `grant_id`, `revoked_principal`, `revoked_at`, optional `reason_ref` |

Runtime actual-use events bind at least: `usage_event_id`, `object_id`,
observed governed revision, artifact digest, consumer identity, action/episode
ref, `used_at`.

GAI v1 is complete only when these ten questions have authoritative answers:

1. Who authorized?
2. Which immutable change was authorized?
3. What was the baseline?
4. Who was authority at that time?
5. Was the attempt uniquely admitted?
6. What did the executor actually observe?
7. Is there currently `UNKNOWN`?
8. Which revision did Runtime actually use?
9. Was there an out-of-band change?
10. Is the next change safe to allow?

---

## 5. A0 object A — Trading G0 target

Chosen object (one real persistent Trading governed object):

`trading:adoption-stream:trading-agent-arb-cai-001/trading.paper.shadow`

This is the production-default paper-experience **Subject Adoption Stream**:
durable CAS state that mature evaluations can change, with receipt verification
and a Trading consumer that freezes the adopted state it actually used.

Rejected for G0 (not a durable write target, or not the paper adoption path):
`PromotionEvidencePackage`, `SkillGovernanceRecord`, `ImprovementProposalRecord`,
`edge_knowledge` (stops at SHADOW), `paper_policy_portfolio`, static
`strategy_registry`, Runtime `LearningController` strategy commit.

| Field | Finding |
| --- | --- |
| `object_id` | `trading:adoption-stream:trading-agent-arb-cai-001/trading.paper.shadow` |
| logical object | Scoped adopted capability assessment for Trading paper experience (`adjustment_factor` over `trading.experience` / `capture_ratio.v1`) |
| physical target / storage | Durable JSON `subject_adoption.{sha256(stream_id)[:16]}.json` plus lock sibling under `--state-dir` (`virtualbrain.research.adoption.SubjectAdoptionStream`). Optional in-memory `SubjectCore._adoption_state`. Bridge side-files under `--root`. Evidence snapshots in btc2 SQLite (`agent-experience.v2`, scope `trading:paper`). |
| proposer | btc2 bridge `_apply_verified_experience` (and any direct `stream.apply` caller) |
| evaluator | `SubjectAdoptionStream._apply_unlocked` (`ACCEPT` / `REVERSE` / `REJECT` / `NO_UPDATE`) |
| adoption authority | **None as a separate grant role.** The write API is itself the authority. Parallel `EvaluationDelivery` (`ADOPTION_PENDING` → `ADOPTED`) is not the write gate. |
| authority epoch source | Closest fence: `state_version` / optional `expected_state_version`. Formal GAI `authority_epoch`: **absent**. |
| executor | `SubjectAdoptionStream._save` (tempfile + `fsync` + `os.replace`) under interprocess lock |
| write path | `stream.apply(evaluation, expected_state_version=?)`. Production bridge currently calls `apply` **without** `expected_state_version`. |
| authoritative read-back | `stream.read_receipt()` → `AdoptionStateReceipt.verify()`; also `snapshot()` / `assessment` |
| current revision source | monotonic `state["state_version"]` |
| Runtime actual-use source | btc2 `CandidateSubjectDecisionBundle` freezes `adoption_state_version`, digest, payload, consumer/code/config/experiment identities |
| out-of-band writers | any process with filesystem access to the state-dir JSON; `SubjectCore.restore`; tests calling `apply` directly; bridge cron; no hard single-writer proxy |
| recovery owner | code-level: VirtualBrain `SubjectAdoptionStream` (failed write rolls memory back; restart reload + history replay). Org/ops owner: **UNKNOWN**. |
| current bypass risks | production apply omits CAS; direct JSON overwrite; `apply` with no grant; Runtime `LearningController` does not protect this object; `--root` side-files mutate independently of adoption CAS |
| automated apply eligibility | **`MANUAL_CONFIRMATION_REQUIRED`** |

External-write check:

- **A** conditional write: yes, `expected_state_version` exists
- **B** stale-writer rejection: yes, `ConcurrentSubjectUpdate("stale adoption state version")` plus lock
- **C** single controlled write proxy: **partial** — `apply()` is the intended API, filesystem remains writable

G0 may **wrap** this object. It must not claim strict automated GAI apply until
atomic admission, one-grant-one-attempt, `ApplyReceipt`, and
`UNKNOWN` / `RECOVERY_REQUIRED` exist, and production apply uses CAS.

### Reuse classification (Trading G0 and adjacent)

| Component | reuse class | disposition |
| --- | --- | --- |
| `object_id` / stream identity | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` |
| assessment payload / `adjustment_factor` | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` |
| file CAS + `expected_state_version` + interprocess lock | `GENERIC_PROTOCOL` | `REUSE_DIRECTLY` |
| durable JSON filenames / `--state-dir` layout | `TEMPORARY_IMPLEMENTATION` | `WRAP_AND_REUSE` |
| `AdoptionStateReceipt` + payload digest | `GENERIC_PROTOCOL` | `REUSE_DIRECTLY` |
| `SubjectUpdateRecord` history | `GENERIC_PROTOCOL` | `REUSE_DIRECTLY` |
| production bridge `apply` without CAS | `TEMPORARY_IMPLEMENTATION` | `WRAP_AND_REUSE` (force CAS + grant) |
| proposer from verified paper experience | `DOMAIN_SEMANTIC` | `WRAP_AND_REUSE` |
| `CandidateSubjectDecisionBundle` actual-use freeze | `DOMAIN_SEMANTIC` | `REUSE_DIRECTLY` |
| `EvaluationDelivery` (unwired to stream write) | `TEMPORARY_IMPLEMENTATION` | `REPLACE_LATER` if it becomes the grant surface |
| filesystem / restore out-of-band writers | `LEGACY` | `REPLACE_LATER` (controlled proxy) |
| Runtime `LearningController` / `StrategyRegistry` | `GENERIC_PROTOCOL` (different object) | `WRAP_AND_REUSE` later; do not retarget G0 onto it |
| Runtime `ActionPolicy` / Constitution / Autonomy / Budget | `GENERIC_PROTOCOL` | `WRAP_AND_REUSE` for admission checks |
| Runtime `CheckpointCoordinator` | `GENERIC_PROTOCOL` | `REUSE_DIRECTLY` (local durability pattern) |
| Runtime `CapabilityUsageRecord` | `GENERIC_PROTOCOL` | `WRAP_AND_REUSE` shape; **not** consumer actual-use |
| Runtime `CapabilityBinding` / inventory | `GENERIC_PROTOCOL` (naming hazard) | `KEEP_DOMAIN` as local capability; **do not** reuse as `ObjectBinding` |
| Runtime `ConsentGrant` | `DOMAIN_SEMANTIC` (social) | `KEEP_DOMAIN` |
| Runtime `VerificationReceipt` (network, revision-bound) | `GENERIC_PROTOCOL` | `WRAP_AND_REUSE` (“receipt ≠ apply ≠ load”) |
| Runtime `RecoveryController` stub | `LEGACY` | `REPLACE_LATER` |
| Runtime `RuntimeLease` | `GENERIC_PROTOCOL` (process lock ≠ epoch) | `WRAP_AND_REUSE` exclusion idea only |
| VB `PromotionReceiptLedger` + `REQUIRE_REVIEW` recovery | `DOMAIN_SEMANTIC` (self-evolution) | `WRAP_AND_REUSE` sealed receipt / fail-closed recovery; not business grants |
| VB `GovernedSelfEvolution` immutability | governance hygiene | `REUSE_DIRECTLY` as proof VB must not weaken floors |
| VB `ContinuityLedger` | partial fencing | `WRAP_AND_REUSE` pattern; `REPLACE_LATER` for GAI epochs |

G0 protocol tests (later, not this commit) must cover: two grants on one
baseline; one grant with two `attempt_id`s; unused grant after epoch change;
crash after apply before receipt; crash after receipt before Runtime binding;
`UNKNOWN` during authority transfer; out-of-band change then old grant; timeout
while target actually changed; retry same attempt; consumer not yet reloaded.

---

## 6. A0 object B — WeChat G1 target

Chosen object:

`wechat:research-policy:high-risk-source-diversity`

This object **does not exist yet** as a versioned governed artifact. The G1
slice is specified here so later work does not smuggle a safety-floor change
in as if it were a policy A→B.

### Frozen G1 change (not OPTIONAL → REQUIRED)

**Baseline** (must already hold before the candidate is meaningful):

The Domain Agent already enforces the mandatory counter-evidence Evidence
Floor. That floor is **not** optional. W3 must implement it before G1.

**Candidate** (the actual A→B):

For specified high-risk financial claims, **in addition to** the Evidence
Floor, require at least one corroborating source that satisfies a
domain-defined independence rule.

This does not lower the safety floor. After adoption, verification is:

grant → apply → read-back → receipt → consumer reload → actual-use event →
later qualifying research episodes execute the extra source-diversity rule.

### Current (pre-G1) physical reality

| Field | Finding |
| --- | --- |
| `object_id` | `wechat:research-policy:high-risk-source-diversity` (logical; not persisted) |
| logical object | Research-time source diversity policy for high-risk financial claims |
| physical target / storage | **None.** Source constraints are hardcoded in `wechat_traffic_agent.planner` (experiment drafts must use empty `source_urls`) and `wechat_traffic_agent.service` (compose may only reuse topic `source_url`s). ViralPattern analysis requires evidence refs, not independent corroboration. No policy revision store. |
| proposer | Domain Agent / human (future `ChangeCandidate`). Today: whoever edits those Python modules. |
| evaluator | Domain evaluation (future). Today: none for this policy. |
| adoption authority | **Absent.** Manual packaging only (`publish_mode` remains human). No `AdoptionGrant`. |
| authority epoch source | **Absent.** |
| executor | **Absent.** No Domain Executor for research-policy apply. |
| write path | Edit and deploy this repository. That is not a governed apply. |
| authoritative read-back | **Absent.** No confirmed_state_ref. Running code SHA is not a policy revision. |
| current revision source | **UNKNOWN** (unversioned code behavior) |
| Runtime actual-use source | **Absent.** Agent Runtime does not consume this WeChat research policy. `CapabilityUsageRecord` is local tool use, not policy-revision consumption. |
| out-of-band writers | any commit that changes planner/service/objectives/prompts; any operator prompt override; no detection |
| recovery owner | Domain Agent maintainers (this repo). No automated recovery. |
| current bypass risks | dual source rules (planner vs service); no counter-evidence floor in code; no independence rule; no usage event; Runtime binding cannot exist because there is no object revision |
| automated apply eligibility | **`ADVISORY / MANUAL_CONFIRMATION_REQUIRED`** — fails A, B, and C |

W3 must exist (minimum Evidence Floor) and G0 protocol tests must pass before
G1. G1 also needs a real physical target that supports A, B, or C, plus
consumer actual-use recording. Until then the object stays advisory.

### Reuse classification (WeChat G1 and adjacent)

| Component | reuse class | disposition |
| --- | --- | --- |
| intended `high-risk-source-diversity` policy | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` |
| mandatory Evidence Floor (W3, not yet in code) | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` |
| hardcoded `source_urls` empty vs topic-subset rules | `TEMPORARY_IMPLEMENTATION` | `REPLACE_LATER` by versioned policy object |
| ViralPattern evidence-ref discipline | `DOMAIN_SEMANTIC` | `WRAP_AND_REUSE` as research evaluation input, not as GAI grant |
| PR1–PR7 experiment ledger / near-copy / single-variable planner | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` |
| `TrafficLearner` / `StrategyLearner` | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` (W0 correctness; not GAI) |
| WeChat `ObjectivePolicy` | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN`; Runtime must not absorb it as authorization |
| human manual packaging / no publish authority | `DOMAIN_SEMANTIC` | `KEEP_DOMAIN` through W2/G1 |
| future GAI grant/attempt/receipt types | `GENERIC_PROTOCOL` | `REPLACE_LATER` after G0+G1+contract checks, then G2 extract |

---

## 7. Remaining blockers

| Blocker | Blocks | Notes |
| --- | --- | --- |
| No `AdoptionGrant` / attempt admission in any of the four trees | automatic apply, G0 “protocol available”, G2 extract | Closest: VB adoption CAS, Runtime auth stack |
| Production Trading apply omits `expected_state_version` | strict G0 auto-apply | Wrap must force CAS |
| Trading state-dir JSON remains freely writable | eligibility C | Needs exclusive proxy or fencing that out-of-band writers cannot skip |
| No GAI `UNKNOWN` / `RECOVERY_REQUIRED` on the Trading stream | G0 cases 4, 6, 8 | VB promotion `REQUIRE_REVIEW` is a wrap pattern only |
| No WeChat Evidence Floor (W3) | G1 | Baseline would otherwise be a hidden OPTIONAL→REQUIRED change |
| No WeChat policy artifact / revision / read-back / actual-use | G1 | Object is logical-only today |
| Runtime `CapabilityBinding` name collision | ObjectBinding misuse | Inventory must not be treated as consumer-loaded revision |
| Personal/Coder contract checks not yet run | G2 / schema freeze confirmation | `evaluation_refs=[]` under `explicit-user-command`; APPLIED vs stale actual-use |

Personal/Coder are contract challenges only (no formal slices before G2). If
v1 cannot express them, the contract must not freeze.

---

## 8. Consequences

- First Trading governed-change slice wraps
  `trading:adoption-stream:trading-agent-arb-cai-001/trading.paper.shadow`
  under manual confirmation. It does not invent a new Trading learning
  controller.
- First WeChat governed-change slice is
  `wechat:research-policy:high-risk-source-diversity` as specified in §6, after
  W3 and G0 protocol tests.
- Execution order stays the v3.1 plan: W0 → W1 → W2 → W3/W5 → G0 → optional
  W4/W6 → W7 → W8 → G1 → contract checks → G2 → shared GAI → first real WeChat
  adoption.
- Each later commit remains one problem, one verifiable behavior change.

Verification of this ADR: read-only inspection of `D:/wechat-traffic-agent`,
`D:/btc2`, `D:/virtualBrain`, and `D:/agent-runtime`. No runtime code in this
commit.
