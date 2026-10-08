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

## Remaining steps to independently deployable code

These are substantive, not folder moves:

1. Continue breaking the 1,105-line `ContinuousContentLoop._run` into research selection,
   verification, cognition, editorial admission and publication use cases
   with explicit request/result objects. No domain use case reads another
   module's private cycle files.
2. Continue separating HTTP/API handlers from the 1,128-line `LiveAgentService._app` and
   replace direct internal state scans with narrow read interfaces. Site API
   and operator API should have separate application compositions.
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
