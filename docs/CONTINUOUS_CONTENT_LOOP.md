# Continuous Content Agent minimum loop

The operator explicitly authorized automatic Website publication on 2026-09-14.
This policy supersedes the earlier manual-only requirement **for this opt-in
Website loop**. It does not authorize WeChat posting, Trading writes, or treating
self-checks as audience outcomes. Existing manual admission records stay intact.

The same Content Agent / Runtime / VirtualBrain service now supports:

1. Read an operator-authored writing curriculum and up to eight prior experiences.
2. Use the real reasoning backend to practice 1–3 techniques with before/after
   examples. Persist a content-addressed lesson and prior-experience digest.
3. Observe recent items from 1–4 explicitly configured HTTPS RSS/Atom feeds.
   Sources are bounded to 512 KB each, 30 items each, 60 combined, seven days old
   at most, with no redirects or article-following. Coverage recency is a signal,
   not measured popularity. Unavailable feeds are recorded; no evidence means no
   article in that window.
4. Select a current event, reader question and gap using the full current Website
   inventory, Topic coverage, article links, learned techniques and prior titles.
5. Generate a sourced private article applying the lesson. News descriptions do
   not license unstated details. Existing article source references are retained.
6. Run a separate model self-check. Malformed source IDs, section indices and
   non-contiguous quotation references share its single schema/semantic repair
   budget; this never changes a negative substantive verdict to a positive one. Require >=350 words, every learned technique
   linked to an exact article quote, every section linked to an exact supplied
   source quote, and positive grounding/usefulness/privacy/no-investment-advice
   checks. Save the fallible assessment and a next-practice exercise. Failure
   holds the draft; it does not mark content publicly safe.
7. If separately enabled, a Website-only machine admission policy binds the exact
   draft and self-check, builds the whole candidate, audits metadata/indexing and
   internal links, then atomically switches the served symlink. Original drafts
   remain public_safe=false. Public copies have an explicit automated disclosure.
8. In the next six-hour window, read the previous lesson and critique, then practice
   and apply an adapted technique set. This proves experience influences later
   model input and records claimed applications; it does not prove causal skill
   or audience improvement. Real channel performance integration is deferred.

## Runtime configuration

All new flags default off. Existing Trading ingestion is unchanged.

```
CONTENT_NETWORK_SEED_URL=http://127.0.0.1:8001/a2a
CONTENT_CONTINUOUS_ENABLED=1
CONTENT_CONTINUOUS_INTERVAL_HOURS=6
CONTENT_NEWS_FEEDS='["https://feeds.bbci.co.uk/news/business/rss.xml","https://www.federalreserve.gov/feeds/press_all.xml"]'
CONTENT_WEB_DRAFTS_ENABLED=0
CONTENT_WEB_SITE_ROOT=/var/www/content-agent-live/current
CONTENT_WEB_BASE_URL=http://content-preview.43-133-181-149.sslip.io
CONTENT_AUTO_PUBLISH_ENABLED=1
CONTENT_AUTO_PUBLIC_ROOT=/var/www/content-agent-live
CONTENT_AUTO_SITE_CONFIG=/home/ubuntu/content-agent-state/automatic-site.json
```

An operator initializes `current` as a symlink to the existing audited site and
grants the service user write access only to the live release directory. Nginx
serves `current`. No sudo or arbitrary shell execution is exposed to the model.
Private state remains outside the public directory. The publisher saves canonical
inventories privately, keyed by the active release, so future selection sees
newly published articles. Inventory mismatch fails closed. Existing preview
noindex and AdSense configuration are preserved.

## Budgets and restart semantics

Current production starts with one-hour windows for rapid observation.
One attempt per fixed UTC window and deployed loop version; one draft per news content fingerprint.
Loop v2 preserves v1 terminal receipts under their original paths and uses v2-prefixed
cycle paths. Only an operator-deployed code version can start a new versioned
attempt; successful source fingerprints remain deduplicated across versions. Feed
publication timestamp changes do not change content identity. News already
written is excluded before any model call. Use the explicit local seed `/a2a` transport so a public Agent Network card does
not redirect a same-host client to an unreachable public ingress. This does not
change signed enrollment or research permissions. Audited public releases and
their release parent are explicitly world-readable/traversable for Nginx, even
under a restrictive service umask. Private receipts are never placed there.

Each cycle freezes its input snapshot,
lesson, raw model response receipts and terminal status. Every schema phase has
at most one repair. No schema downgrade or unrestricted retry exists.

A cross-process OS lock covers the full cycle. Each model call writes a started
receipt before dispatch; a crash without a response receipt fails closed, avoiding
an unbounded repeat bill. Terminal errors are not retried in that window. A crash
after draft creation may leave a private, unpublished draft; no automatic promotion
of unrelated inbox drafts occurs. Failed audits leave the prior served tree active.
Release and receipt history is currently retained; operators must monitor disk
usage. Inventory is bounded at 200 articles, after which selection pauses for
an explicit capacity change rather than silently dropping article coverage.

`content/continuous/cycles/<UTC-window>/` contains frozen inputs, model receipts,
lesson, experience, optional machine publication receipt and terminal result.
`content/automatic_website/inventories/` contains the admitted canonical inventory.
Neither is served by Nginx. Audience feedback storage stays separate and empty
until actual measurements are supplied.
