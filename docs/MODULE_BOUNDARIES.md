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
  this milestone 144 legacy functions remain above 100 lines; this is an
  incremental target, **not** global completion. Most extracted helpers are
  below 50 lines and no touched method exceeds 100.
- Next genuine hard boundaries: the 2,284-line
  `ContinuousContentLoop._run` and the 2,485-line
  `LiveAgentService._app`. Their inputs, outputs and local state must be
  separated by use case and API route groups, not hidden in one new wrapper.

- `orchestration/resources.py`: the former 313-line resource-review method is
  now a coordinator over model pressure, infrastructure observation, provider
  discovery, evidence verification, provider trials and outcome feedback.
  Evolution deliberation uses separate need selection and proposal methods.
  Short-function tests also cover this module. The latest repository audit is
  **142** functions exceeding 100 lines; that backlog is still real.

## Remaining steps to independently deployable code

These are substantive, not folder moves:

1. Break the 2,284-line `ContinuousContentLoop._run` into research selection,
   verification, cognition, editorial admission and publication use cases
   with explicit request/result objects. No domain use case reads another
   module's private cycle files.
2. Separate HTTP/API routing from the 2,485-line `LiveAgentService._app` and
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
