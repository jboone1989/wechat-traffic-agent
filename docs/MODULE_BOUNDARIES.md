# Ferro feature isolation: one deployment, separate capabilities

Ferro remains one deployed product supervised by `run_ferro_supervisor.py`.
"Independently deployable" is a **code boundary goal**, not an instruction to
create microservices, separate databases, extra containers, or network hops.
Agent Runtime remains the owner of generic identity, goals, memory primitives,
model backends and scheduling. Ferro owns its domain behavior.

## Feature ownership after the second pass

| Feature | Implementation | Dependencies allowed |
| --- | --- | --- |
| Editorial | `editorial/` | Pydantic contracts, SQLite, model-independent evidence; no website/HTTP/X |
| Website | `website/` + `channels/` | Editorial content and injected publication event sink |
| Publication | `publication/` | Immutable publication events, channel-neutral outbox; not X transport |
| X | `platforms/x/` | Browser session adapter, publication event contract |
| Browser | `platforms/browser/` | Browser/session resources only; no editorial decisions |
| Wakeful task handlers | `orchestration/{owner,content,social,neural,resources,observation}.py` | Injected host runtime services, not website templates |
| External sources | `sources/` | Read-only evidence providers |
| Runtime shell | `wakeful_tasks.py`, worker scripts and supervisor | Wires handlers, admission, lifecycle and result recording |

### Enforced contracts

1. `editorial.routing._route_for` and `editorial.fingerprint.digest` are
   canonical; the HTML renderer simply re-exports them. The article SQLite
   repository does **not** import the site renderer or website policy.
2. The SQLite repository owns article records; `website.content_publication_store`
   owns generated HTML, derived listings, feed, and content symlink. That
   materialization layer is **not** an article repository.
3. `website.AutomaticWebsitePublisher` accepts a `PublicationSink`. The default
   in-process sink writes immutable publication events and schedules downstream
   distribution through `publication.DurablePublicationDispatch`. Other
   sinks can be injected without changing website code.
4. The X-specific distribution adapter is in `platforms/x/distribution.py`,
   not publication. Publication owns only events, tasks and verified receipts.
5. Importing `editorial.article_repository` must not import web, X, wakeful
   orchestration or Agent Runtime. Root package public exports are lazy.
6. The wakeful dispatcher delegates 43 methods to six capability mixins; its
   entrypoint stays stable. Each group can be tested in isolation with fake
   host components, but **mixins still access shared host fields**, so this is
   an intermediate boundary, not independent deployability yet.

## Compatibility and state

Top-level module names stay available as compatibility aliases to the same
canonical module objects. The alias for
`editorial.content_publication_store` now points to the website component.
No code or state is duplicated by those aliases.

Keep the existing `content-agent.service` supervisor and its independent
workers. Do **not** start extra processes or deploy these working-branch
changes during the modularization. Article SQLite remains authoritative;
`articles.json`, HTML and feeds are rebuildable projections.

## Short-function refactor (stage 3)

- `orchestration/content.py`: the 571-line `_content` function now coordinates short
  methods for owner decisions, site checks, cycle recovery, resource admission,
  content execution, receipt persistence, social follow-up and owner completion.
  Recovered cycles must use their new cycle ID, not the base ID.
- `website/automatic_website.py`: the 128-line `publish` is now a short
  coordinator around admission, timestamp validation, incremental/legacy
  rendering, attribution and durable publication events.
- `tests/test_short_function_boundaries.py` enforces a maximum of 100 lines
  per method in these two refactored modules and exercises recovery identity
  and disabled content-pipeline observer behavior.
- `scripts/audit_function_lengths.py` scans all Ferro production code. At
  the stage-3 milestone 142 legacy functions remained above 100 lines; this is an
  incremental target, **not** global completion. Most extracted helpers are
  below 50 lines and no touched method exceeds 100.
- `orchestration/resources.py`: the former 313-line resource-review method is
  now a coordinator over model pressure, infrastructure observation, provider
  discovery, evidence verification, provider trials and outcome feedback.
  Evolution deliberation uses separate need selection and proposal methods.
  Short-function tests also cover this module. The latest repository audit is
  **142** functions exceeding 100 lines; that backlog is still real.

## Content cognition and public API extraction (stage 4)

- `ContinuousContentLoop._run` went from **2,284** to **1,602** lines without
  moving the remainder into another monolith. Named, short helpers own
  resumable cycle inputs, strategy-outcome synchronization, owner seed evidence,
  world observation and prediction settlement, and Website experiment binding.
- The goal-formation path now independently handles direct owner focus,
  stable multi-epoch dream attractors, ordinary preconcept research inquiries
  and neural taste competition. The external mission discovery path is split
  into query construction, evidence-aware validation, failure receipts and
  creation of a bounded goal. All new extracted methods are <=100 lines,
  with most substantially below 50.
- `LiveAgentService._app` went from **2,485** to **2,320** lines in this
  stage. `web_api/route_registry.py` separately declares 26 public and
  51 operator routes; `web_api/public_views.py` owns public HTML and neural
  observations; `web_api/catalog.py` reads published articles from SQLite
  without traversing static JSON inventories.
- `tests/test_goal_formation_boundaries.py` and
  `tests/test_web_api_boundaries.py` prove public-route isolation and test
  the goal formation and mission failure contracts without a full agent
  startup. These are **incremental** boundaries: operator HTTP handlers,
  streaming chat and most of the content writer still remain in long
  functions; no independent deployment or additional process is intended.

## Event and use-case isolation (stage 5)

- `research_evidence_service.py` owns candidate admission, traffic demand context,
  self-directed research focus, primary and fallback web searches, and evidence
  admissibility. Callers supply an `EvidenceRequest` and explicit `EvidencePolicies`;
  it returns typed `EvidenceResult` and `EvidenceAdmission`. The module does not
  import `content_goal_loop.py`, construct an owner controller, or load browser state.
  The researcher can run alone with fake explorer, traffic and storage ports.
- `content_lifecycle_events.py` is a **typed in-process event router**, not another
  durable JSON event queue. `DraftChecked` triggers `MindProcessFeedback`, while
  `ContentCycleSettled` triggers `MemoryLifeFeedback` after publication admission.
  Both observers are independently injected; writing/publication need not import
  their implementations. Subscribers retain existing receipt semantics.
- Real public publication continues through `PublicationEvent` and the durable,
  idempotent distribution outbox. The neural worker retains ownership of the
  existing cross-worker `FerroStateEventBus`. Do not copy those events into
  a new general-purpose file bus. Immediate ask/answer decisions should remain
  ordinary calls, not eventual event requests.
- `web_api/operator_resources.py`, `operator_browser.py` and
  `operator_login.py` now own resource/survival, account/browser and
  authentication/OAuth routes respectively. They use explicitly injected
  clients, controls and authorization callbacks; they no longer import
  `live_agent.py`. No additional server, process or browser is started.
- After this stage the long entrypoints are **still unfinished**:
  `ContinuousContentLoop._run` is about **1,354** lines (previously 2,284),
  and `LiveAgentService._app` is about **1,488** lines (previously 2,485).
  The remaining work is isolating evidence selection, cognition and editorial
  verification, plus public/owner chat streaming. Replacing a 1,000-line method
  with another 1,000-line method in a different file is not completion.
- `tests/test_event_driven_feature_boundaries.py` verifies independent
  research execution, fallback behavior, typed event delivery, publication
  ordering, withheld publication behavior and module import boundaries.

## Editorial choice, inquiry, worldview and public chat (stage 6)

- `content_goal_contracts.py` exports stable selection and worldview decision schemas,
  allowing feature modules to import contracts without importing the giant runner.
- `evidence_choice_service.py` owns goal/topic constrained evidence selection,
  deterministic recovery from invalid model selections, related-article binding
  and live-market catalyst verification. `EvidenceChoiceRequest/Result` and
  injected decision, title and source policy ports let it run without a full
  agent, website publisher, or X browser.
- `learning_inquiry_service.py` owns prospective interest commitment, research
  execution, reward settlement and an explicit `COGNITION_ONLY` result when
  no new supported understanding exists. Its commitments/receipts are still
  restart-stable; it does not originate or claim neural preferences.
- `worldview_assessment_service.py` owns evidence-grounded worldview decisions,
  durable belief recording, and optional dated predictions. An unchanged belief
  produces **no prediction**. Existing decision and proposal receipts are
  reused after a restart.
- `web_api/public_chat.py` owns both JSON and SSE public conversation endpoints,
  sharing one input admission, state snapshot, social feedback, prompt compiler
  and guarded model output path. PublicChatPorts is explicitly injected by
  the web composition root; the handler has no operator session/browser imports
  and it doesn't start a separate service. The public reasoning summary is
  deliberately distinct from private model reasoning.
- `tests/test_public_chat_inquiry_boundaries.py` and
  `tests/test_research_worldview_feature_ports.py` use in-memory fakes to
  exercise these features without booting the full Ferro agent, and verify
  method-length and import boundaries.
- **Still incomplete:** `ContinuousContentLoop._run` is **1,105** lines,
  down from 2,284; `LiveAgentService._app` is **1,128** lines, down
  from 2,485. Remaining hard responsibilities are editorial planning,
  writing and verification, and private-owner chat/session orchestration.
  These need explicit services; a moved monolith is not an acceptable finish.

## Operator dialogue and editorial strategy (stage 7)

- `operator_private_chat_handlers.py` owns the authenticated owner conversation
  use case: prompt assembly, behavioral inputs, structured envelope repair,
  bounded read-only tool loops, durable memory/proposal receipts and native SSE
  streaming. Constructor ports explicitly supply agent runtime, conversation,
  memory, account registry, authorization and snapshot access. Streamed replies
  have separate generation, tool, repair and persistence phases; all feature
  methods are <=100 lines. `owner_dialogue_policy.py` owns the one canonical
  owner system prompt and generation budgets (re-exported from the original
  module for compatibility). No browser/HTTP service starts here.
- `method_evidence_binding_service.py` binds METHOD_NOTE cognition to exact
  observed or verified method evidence, and returns a typed HOLD outcome when
  there is no support. `writing_practice_service.py` selects and persists
  technique practice after expression is warranted, without publishing.
  `editorial_strategy_service.py` performs forecasts before strategy binding,
  chooses existing process variants, and registers prospective outcomes.
- `web_api/public_status.py` and `web_api/operator_pages.py` own public
  telemetry/activity streams and operator dashboards/observability endpoints.
  `web_api/app_factory.py` composes one Starlette application from independently
  injected handlers. Browser/OAuth controls are imported at actual adapter
  creation, not at module import time, so a connected fake/alternative provider
  can be substituted and the server does not eagerly launch another browser.
- `LiveAgentService._app` is **19 lines** (originally 2,485), with no HTTP
  business handlers. `ContinuousContentLoop._run` is **945 lines** (originally
  2,284); its remaining long phases are article generation/brief assembly and
  deterministic quality verification/one bounded draft repair. The latter must
  be separate feature use cases, not hidden in a new monolith.
- `counter_evidence_service.py` owns bounded counter-evidence discovery. Missing
  contrary evidence results in a saved lower-claim writing requirement and a
  strict post-draft claim check; its plan and missing-evidence receipts are
  reused after restart. The content runner does not call the counter-search
  adapter directly.
- `tests/test_owner_editorial_feature_boundaries.py` runs owner streaming,
  method evidence grounding, writing practice, strategy forecast ordering,
  counter-evidence restart/reuse, and import/size contracts against injected fakes, alongside the existing
  owner chat and content regression suites.

## Grounded writer, verifier and publication receipts (stage 8)

- `draft_generation_service.py` accepts a typed request and injected source,
  strategy and context projections. It compiles the bounded editorial brief,
  constructs cited TopicSignal inputs, performs initial draft generation, and
  applies the existing one-time lower-claim rewrite when counter-evidence is
  missing. Its methods are 74 lines or fewer; it never decides publication.
- `draft_verification_service.py` owns exact-source-quote validation,
  section evidence coverage, learned-technique checks, minimum length and
  perspective checks. Its single retry uses the same frozen draft/evidence
  and writes a receipt; it does not rewrite content or relax any criterion.
- `draft_repair_service.py` performs at most one missing-technique repair
  and calls the same verifier in strict mode, including title uniqueness and
  lower-claim conditions. It cannot bypass the publication gate.
- `draft_outcome_service.py` persists the reviewed draft and the process
  receipt and returns a result; only `ContentCycleCompletion` owns the
  subsequent publication/outbox action and typed feedback notification.
- `expression_decision_service.py` decides whether cognition warrants
  public expression and returns `COGNITION_ONLY` when not. Prior decisions
  remain restart-stable and a previously forced no-output from partial
  learning may be reconsidered only with concrete public evidence.
- `ContinuousContentLoop._run` is **378 lines**, down from **2,284**.
  Remaining work is shortening its goal/research/cognition phase composition
  using typed phase outputs; do not merely move 378 lines into another runner.
  `tests/test_draft_feature_boundaries.py` checks invalid quotes, frozen
  retry admission, independent module imports and function-length contracts.

## Typed in-process phase orchestration (stage 9)

- `ContinuousContentLoop._run` is now **15 lines** (originally 2,284).
  It only calls the goal, research, cognition, editorial planning, draft
  generation and verification/completion phases, propagating an existing
  terminal result when one phase stops early.
- The six phase methods are **87, 79, 93, 73, 60, and 97 lines**.
  Each phase only composes the separately testable use-case services.
  Explicit, frozen `_CycleGoalStage`, `_CycleResearchStage`,
  `_CycleCognitionStage`, `_CycleEditorialPlan`, and `_CycleDraftStage`
  objects carry phase outputs; they are short-lived **in-memory values**, not
  large JSON snapshots, a new queue, or an additional process.
- `tests/test_draft_feature_boundaries.py` enforces the <=50-line entrypoint
  and <=100-line phase limits as well as evidence and retry admission contracts.
- The **full repository is not yet fully short-method compliant**: older
  long methods elsewhere still require a separate audit and refactor.
  Prior wakeful-suite baseline failures and parallel working-tree changes
  remain separate integration tasks; no main merge or deployment is implied.

## Decision evidence retrieval and cognition projections (stage 10)

- `decision_candidate_retrieval.py` owns the only canonical-memory read
  used by this decision-context builder. Its injected router port returns
  bounded candidates, recall ranks, and per-need error receipts. Older
  facts are shadowed only when a newer fact explicitly supersedes a
  *specific shared dependency*; a shared owner goal alone is insufficient.
- `decision_fact_selection.py` is an **in-memory, no-IO** selector. Separate
  methods preserve current owner anchors, select marginal information-need
  coverage, expand specific causal dependencies within the fact budget,
  and produce selected/rejected explanations. The decision policy remains
  validated centrally; a memory outage does not give permission to invent
  evidence. Every method is at most 100 lines.
- `build_decision_context()` is now **92 lines**, down from 471. It plans
  information needs, composes retrieval and scoring, and delegates bounded
  selection; it does not mix memory access with set-cover internals.
- `cognition_selected_projection.py` converts bounded selected facts
  into prompt sections using pure functions, with no memory retrieval,
  model invocation, file serialization or contract observation.
- `cognition_contract_observer.py` owns the separate side-effect boundary
  for capability contract monitoring, fault sensing and self-maintenance
  diagnosis. The 483-line cognition-context builder supplies one snapshot
  and explicit runtime adapters; duplicate violations do not rerun diagnosis.
  It previously spanned 661 lines. Further decomposition of source
  collection and metadata compilation is still needed.
- `tests/test_cognition_decision_module_boundaries.py` exercises these
  features without starting Ferro, including missing memory, supersession,
  fact-budget selection, duplicate diagnostics, and pure projection. The
  larger content and Web regressions also continue to pass.
- This stage creates no new process or JSON state files and leaves
  the existing event and publication paths unchanged.

## Scoped cognition and organism observation pipeline (stage 11)

- `cognition_scope_planner.py` chooses relevant owner mission rows, a
  mission-anchored retrieval query, current owner goals and the matching
  latest plan. This is a pure policy operation: it neither scans all memory
  nor makes LLM calls. Generic owner-controller prompt wording no longer
  becomes an accidental memory-search target.
- `build_ferro_cognition_context()` now **379 lines** (originally 661,
  previously 483). Further work remains to separate capability collection
  from prompt metadata compilation; do not introduce a parallel memory or
  state store just to shorten the method.
- `organism_observations.py` is the one *existing-file read adapter* for
  resource/service/model evidence and settled-world rewards. It returns
  typed `ResourceObservations` and `RewardObservations`. It does not
  compute drives, decide actions or persist state.
- `organism_signal_calculator.py` computes freshness-bound signals and
  organism summary from those observations and the current neural/body
  inputs. Missing or stale signals remain UNKNOWN. The calculator has
  injected signal/freshness ports and does no direct disk IO.
- `OrganismStateService.refresh()` is **27 lines** (previously 467).
  Its single responsibility is reading observed evidence, computing one
  state update, and saving through the existing state path. The old
  on-disk schema and bounded history remain unchanged; no new JSON
  queues, daemons, processes or microservices were introduced.
- `tests/test_organism_cognition_ports.py` verifies owner scope selection,
  injected observation IO, independent signal computation, UNKNOWN
  propagation and import/method-size contracts. Existing organism and
  cognition closure regression tests verify behavioral compatibility.

## Wakeful neural phases and behavioral modulation (stage 12)

- `behavioral_modulation_rules.py` is the pure policy engine for affect,
  personality, drive, conflict and social-relationship expression cues.
  The existing `build_behavioral_state()` now only normalizes inputs,
  applies the independent rule compiler, and exposes the same bounded
  behavioral contract. Rules cannot change factual authority or permissions.
- `neural_stimulus_dispatcher.py` emits drive/body and dream-derived events
  through an injected core. `neural_authority_coordinator.py` separately
  handles prospective action recommendations, the owner-goal authority gate,
  personality adaptation and bounded motor scheduling. X availability is\n  supplied as an explicit composition-root setting, not read implicitly\n  by the feature.
- `neural_development_sensors.py` observes active developmental needs and
  extension sensors without scheduling tasks. `neural_development_scheduler.py`
  independently schedules candidate-world experiments, architecture
  revalidation, transfer and evolution consolidation using explicit ports.
- `neural_continuity_coordinator.py` updates cortex/individuality/organism
  continuity and schedules only *newly due* delayed outcome checks.
  It returns the due signature; `ContentWakefulRuntime` owns that short-lived
  comparison state. The same owner-goal gate and idempotent wake IDs remain.
- `ContentWakefulRuntime.refresh_neural_life()` shrank from **413 to 92 lines**;
  `build_behavioral_state()` shrank from **337 to 76 lines**.
  The new methods are <=100 lines, have no import dependency on the giant
  wakeful entrypoint and create no additional process or state-file queue.
- `tests/test_neural_feature_boundaries.py` tests these feature ports with
  stand-alone fakes, alongside the existing dream, personality, development,
  owner-control, and wakeful scheduling regression tests.

## Interoception and cognition presentation ports (stage 13)

- `interoception_task_classifier.py` is a deterministic, stateless symptom
  classifier for task statuses, retries, fallbacks, capability contracts and
  quantitative prediction/world-utility evidence. Its public classifier is
  still imported from the old path for compatibility. No episode files,
  L1 provider or repair scheduler is accessed by this module.
- `interoception_health_assessment.py` enriches a deterministic classification
  through an injected L1 health assessor. Its only side effect is that injected
  call; exceptions are represented as symptoms, not swallowed as healthy.
- `interoception_episode_ledger.py` owns existing fault episode files,
  recurrence receipts and verified recovery streaks. Persistence paths and
  recovery thresholds are injected; there is no new storage location.
  `interoception_state_projection.py` purely computes the next visible
  distress/fault projection, and `interoception_diagnosis_receipt.py`
  builds a bounded handoff request without scheduling repair.
- `InteroceptiveFaultSystem.observe()` is now **100 lines** (previously
  335), composing these independent ports. Original state schemas, episode
  signatures, lifecycle boundaries and classification meanings are unchanged.
- `cognition_environment_reader.py` collects runtime capability inventory,
  evidence-bound beliefs, relationships, and bounded behavior/phenotype
  context from explicit runtime inputs. It does not query memory or write.
  `cognition_selection_receipts.py` exposes source-count and provenance
  summaries but never includes host information-need question text in
  the model-facing decision context. `cognition_snapshot_presentation.py`
  composes the model-facing snapshot and suppresses neural control signals
  for an active owner goal, without retrieving anything.
- `build_ferro_cognition_context()` is now **211 lines**, down from 661
  at the start of the refactor. The remaining source/decision selection and
  surface metadata compilation can be independently audited next.
- `tests/test_interoception_cognition_ports.py` checks the independent
  ports for L1 escalation, fault recurrence/recovery, snapshot accuracy,
  omitted internal retrieval questions and owner-goal control suppression.
- No daemon, alternate data store, or new file queue was added. A broader
  legacy `test_wakeful_interoception.py` baseline currently has five
  failures in repair-signal forwarding, observed before this stage's edits;
  they remain a separate integration blocker, not an accepted success.

## Provider trial feature ports (stage 14)

- `ResourceTrialPolicy` is deterministic selection only: keep genuinely
  never-tried providers ahead of already-known ones, allow one executor-generation
  retry of stale failures, observe retry cooling, and exclude incompatible or
  mathematically inadequate model targets without discarding the provider.
  It neither reads credentials nor opens network connections.
- `ProviderTrialExecutor` resolves the requested target and runs a bounded
  trial. It returns measured observations and canonical trial objects; it
  cannot decide their success, schedule a retry or persist credentials.
- `ResourceOutcomeRecorder` exclusively writes existing immutable trial
  receipts and deduplicated acquisition-outcome records under
  `application/resource_discovery`. Replays preserve the original outcome
  timestamp. It exposes the existing read path for downstream planning.
- `ResourceFeedbackConsumer` independently evaluates persisted model
  trials, generates provider diagnostics, selects the empirically viable
  model, and synchronizes the resource pool. It does not invoke the trial
  executor or alter the publishing pipeline. The resource-policy change is
  observable on the next discovery/selection pass via saved outcomes.
- `ResourceTrialCoordinator` composes these four feature ports and handles
  one bounded candidate at a time. `ContentResourceScout.run_ready_trials()`
  shrank from 402 to **25 lines**. All new methods are <=100 lines.
  Credential values are read at the composition root and supplied only to
  the executor; they are never written to a receipt.
- `tests/test_resource_trial_ports.py` exercises policy, executor,
  replay deduplication, independently callable feedback, and import boundaries.
  Existing provider-trial regression suites continue to exercise rate limits,
  rotation, stale executor generations and failed target resolution.
- This adds no process, queue, periodic poller, database, or retry loop. Known
  unrelated `resource_economy/resource_regulation` pytest failures and a
  `wakeful_resource_trials` collection error reproduce on the unchanged
  main branch and remain separate baseline issues.

## Remaining steps to independently deployable code

These are substantive, not folder moves:

1. Keep the 15-line `ContinuousContentLoop._run` as composition only and avoid
   reintroducing domain work into any of its six <=100-line phase methods
   with explicit request/result objects. No domain use case reads another
   module's private cycle files.
2. Keep the 19-line `LiveAgentService._app` as composition-only, and replace
   residual direct internal state scans with narrow read interfaces. Public
   and operator endpoints already have separately injected handler sets.
3. Replace the wakeful mixins' implicit shared-`self` dependencies with
   explicit feature service constructors and ports. Retain one in-process
   wiring layer and existing worker scheduling.
4. Move Ferro-specific memory/learning **policies** behind interfaces to
   Agent Runtime's memory/learning machinery. Do not clone generic engines.
5. Add per-feature import contract tests plus replayable integration contracts.
   Prove a feature can run against an injected fake upstream/downstream port
   with no other feature instantiated.
6. Only after boundary tests pass, migrate internal imports to canonical
   package paths and delete temporary compatibility aliases.

No independent service deployment is planned. A feature being separately
deployable means its input, output, storage responsibility and initialization
are explicit; communication can remain ordinary Python calls.

## Verification and known baseline failures

`tests/test_independent_feature_boundaries.py` checks imports and task
delegation. Article/site publication and distribution tests verify that
swapping sinks preserves publication receipts. Some older wakeful suite tests
already fail at baseline `e055a1a`: 41 failed and 82 passed on the original
single-file runner and the same 41 failed/82 passed after extraction. A
worker-neural ownership test also fails in the baseline. These are **not**
evidence of new regression, but still need fixing before integration/deploy.

Do not merge or deploy until the full suite is checked against a known
baseline and other concurrent working tree changes are reconciled.
