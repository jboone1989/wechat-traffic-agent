# Website editorial selection and channel evidence

## Website decisions before generation

The persistent runner optionally enables `WebEditorialWorker` with
`CONTENT_WEB_DRAFTS_ENABLED=1`. Supply `CONTENT_WEB_SITE_ROOT` (the rendered
website), `CONTENT_WEB_BASE_URL`, and optionally `CONTENT_WEB_ARTIFACTS` (a JSON
file/directory containing the complete admitted article inventory). The default
inventory is the release's starter corpus. If additional articles are published,
update that inventory too: mismatched rendered/canonical routes fail closed.

A snapshot includes original titles, summaries, sections, source references,
tags, indexed Topic pages, and links among article pages inside their main
content. Global navigation is excluded; related-article cards are included and
are not claimed to be authored contextual links. File/content hashes bind the
snapshot. Inventories above 200 articles or the context budget fail explicitly.

The reasoning backend proposes up to five distinct articles, with target Topic,
content gap, related article IDs and required evidence. Schema/reference failures
receive one repair attempt. Ranking uses inverse Topic coverage and unlinked
related articles. This is a transparent editorial heuristic, not a measured SEO
or audience-demand model; no growth claim follows from the priority score.

Plans are immutable under `content/editorial_plans/<snapshot>.json`. The highest
ranked proposal supported by existing article evidence is selected. Proposals
requiring new external evidence remain `EXTERNAL_REQUIRED`; if none is ready the
worker returns `NEEDS_EVIDENCE` without drafting. The selected proposal and full
related articles enter the existing composer, which retains schema repair,
source validation and private-draft admission boundaries.

One selected draft is created per unchanged snapshot/plan. On restart, existing
plans and drafts are loaded without model calls. New website content/link state
can produce a new snapshot. This slice does not autonomously retrieve missing
external evidence, modify internal links, admit content, or publish.

## Channel observations

`content_evidence_cli` imports one trusted operator-supplied JSON observation at
a time. No analytics credentials are bundled and no fake production observations
are seeded. Use actual exported platform measurements and source references.

```bash
python -m wechat_traffic_agent.content_evidence_cli --root "$EVIDENCE_ROOT" import --input observation.json
python -m wechat_traffic_agent.content_evidence_cli --root "$EVIDENCE_ROOT" report \
  --channel website --account "$ACCOUNT" --as-of "$CUTOFF" --dimension topic
```

`ChannelEvidence` binds channel/account, publication and content digest, optional
agent-candidate digest, Topic/title/structure/strategy versions, objective,
distribution cohort, metric definition/unit, observation window and source.
`null` means unknown; measured zero is different. Numbers must be finite,
counts integral and ratios within [0,1]. Observation time cannot precede the end
of the measured window, which cannot precede publication.

Corrections increment revision under the same channel/account/publication/window/
metric/source identity. Identical reimports are idempotent; conflicting writes
cannot overwrite. Reports select the highest revision visible at the requested
source observation cutoff, never both old and new versions as separate samples.
This is source-observation-time reporting, not a claim about when a later import
was first available to the local process.

Reports keep channels/accounts separate and stratify metric definitions, units,
objectives, distribution cohorts, window durations/ages, sources, and attribution.
Published-vs-candidate digest differences are editor-modified; missing candidate
digests are unknown. These groups are never silently treated as agent-only wins.
Reports show variant sample counts, means, missing counts and evidence references.
They are descriptive observational evidence; they do not isolate confounders,
perform significance testing, establish adequate sample size, or prove causality.
There is no automatic strategy mutation, editorial admission or publication path.
Real outcome collection still requires platform access and actually published
content; the current private drafts have no fabricated performance history.
