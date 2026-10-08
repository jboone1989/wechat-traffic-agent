# Ferro functional module boundaries

Ferro is a **domain agent**, not a second general-purpose agent runtime.
Agent Runtime owns lifecycle, identity, goals, generic cognition, generic
memory, recovery, authorization, and execution policy. The Ferro repository
owns editorial domain behavior and integrations.

## Actual code layout (migration stage 1)

| Package | Owns | Must not own |
| --- | --- | --- |
| `editorial/` | Canonical content contracts, admission, provenance, drafts, articles in SQLite, publication records | Site layout or X browser sessions |
| `website/` | Web publishing projection, presentation, website feedback, public experiment evaluation | Canonical article database writes except through editorial APIs |
| `channels/` | HTML renderers, public site pages and channel-specific presentation | Agent decisions, authoritative content state |
| `platforms/browser/` | Shared browser lifecycle, account and session isolation, takeover | Publication policy or content selection |
| `platforms/x/` | X-specific posting, articles, metrics and external outcomes | Agent-wide goal planning and durable memory |
| `publication/` | Durable publication events and distribution receipts | Site rebuilds or independent agent state machine |
| `sources/` | Read-only external evidence connectors | Publishing actuators |
| `scripts/` | Thin production entrypoints and explicit migrations | Core domain algorithms |

The unchanged flat modules still include orchestration, learning, organism,
memory, and operations code. **Stage 1 does not claim those are finished.**
Subsequent stages should create `orchestration/`, `cognition/`,
`learning/`, `memory/`, and `operations/` and break up large classes
by responsibilities. Do not promote one giant `core/` package.

## Dependency direction

1. Workers / API endpoints compose feature services; they do not contain
   transport implementations, HTML layouts, or SQL.
2. Editorial content and evidence are the stable business contracts.
3. Website and X are independent consumers of admitted content.
   Updating the site layout does not rewrite articles; publishing an article
   does not regenerate the entire site.
4. Browser sessions are transport infrastructure. The X adapter requests
   a session from the browser layer and returns externally verifiable receipts.
5. SQLite is authoritative for published articles. A generated HTML page or
   `articles.json` is a disposable projection, never another source of truth.
6. Generic cognition and memory mechanisms belong to Agent Runtime.
   Ferro-specific memory observations and learning evaluators plug into them.
7. Cross-feature communication should use explicit functions, records and
   event/receipt contracts rather than direct filesystem scraping.

## Compatibility during migration

Top-level import paths (`wechat_traffic_agent.x_social`, etc.) are tiny
`sys.modules` aliases to the same implementation object now in its feature
package. There is **one implementation**, not duplicated state or shadow
classes. They allow existing tests, workers, third-party callers and mocks to
keep using old imports without simultaneous whole-repository import rewrites.
New implementation modules should import feature packages directly whenever
possible. Remove compatibility aliases after all users migrate and tests pass.

## Out of scope in stage 1

- Moving, editing or resetting uncommitted changes in Agent Runtime.
- Live service reload, database migration, or domain deploy.
- Redesign of website layouts.
- Removing authoritative memory state without a measured retention test.
- Splitting `content_goal_loop.py`, `wakeful_tasks.py`, or `live_agent.py`:
  these need behavior-focused extraction plus regression coverage, not another
  single-file relocation.

## Acceptance

- Feature modules import through canonical and compatibility paths.
- Article CRUD + website rendering and X/browser regressions pass.
- All changed files reviewed and committed separately from unrelated work.
- Live deployment only after the remaining service suite and health checks pass.
