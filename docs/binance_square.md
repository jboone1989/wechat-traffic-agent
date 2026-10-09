# Native BTC/ETH Square editorial publishing

Website summaries are no longer syndicated to Binance Square. The old event
consumer is inert, and the old summary adapter rejects publication. Website/X
events and historical Square receipts are preserved.

Verified website research can now also produce a native Square adaptation when
`square_editorial/editorial_preferences.json` enables `syndicate_website`.
The daily website goal remains active. Successful website publication queues
that same research cycle; the cognition worker independently assesses relevance,
writes standalone Chinese copy, and reviews sources and novelty before handing
it to the existing publisher. Unsuitable content is skipped. Preparation errors
retain the task for a five-minute retry without republishing the website.

Square is now an expression port of Ferro's existing research agent. The
owner episode can choose `BINANCE_SQUARE` for a BTC/ETH inquiry, using the same
durable memory, unfinished questions, source acquisition, reasoning, worldview
assessment and `DECIDE_EXPRESSION` stages as other research. Square bypasses
Website writing-profile selection and requires no Website article.

```text
Ferro interests / unresolved question / prior understanding
  -> shared research + memory retrieval + source-verified facts
  -> cognition: useful understanding, continue investigation, or no expression
  -> native Square expression decision + comparison with recent public posts
  -> independent source/understanding/novelty review
  -> reviewed draft bound to canonical research/cognition/review receipts
  -> independent Square publisher -> confirmed receipt
```

The ten-minute market timer is a sensor. It does not write content or select a
post. Its raw observations also enter the shared evidence explorer, so Ferro
can investigate them with other public sources and its existing memories.

The scheduled publisher only executes reviewed copy in `square_editorial/drafts`.
It does not choose questions, simulate new learning or auto-approve numerical
validation. The producer may decline a Square expression even after a useful
research cycle; further investigation and unpublished understanding remain
durable in the existing inquiry system.

## Evidence and copy

`collect_square_market_structure.py` collects Binance BTCUSDT and ETHUSDT spot
24h tickers, USDT perpetual mark/funding, 5m open interest history and 5m klines.
It retains raw API URLs/responses and derived metrics. All one-hour price/OI
windows share endpoints; only completed candles contribute to volume. OI is
base-asset quantity, avoiding price-driven changes in notional OI. Rolling 24h
spot changes are context, not an explanation of the last hour. Latest funding
is expressed as a per-period percentage, never annualized or treated as a
guaranteed next settlement. Missing, stale or misaligned endpoints fail closed.

`ferro-square-market.timer` collects every ten minutes, without posting or
trading. This is Binance-only evidence, not a market-wide leverage measure.

Native copy explains a reader-relevant BTC/ETH finding using selected evidence.
It must be readable without clicking Ferro's website. Its length, order and
voice are free. There is no compulsory question-first opening, full indicator
listing, BTC-versus-ETH paragraph or conditional ending. Internal assessment
fields are not a required prose outline. Review compares the candidate with
recent successful posts and rejects number-only updates or cosmetic rewrites.
Uninteresting or repetitive evidence is a reason to skip. An editor assesses
the argument, novelty and source-to-claim correspondence; numerical checks and
required fields are not a semantic quality score.

## Preparation and review

`square_draft.py create --document copy.json --evidence snapshot.json` stores a
draft without posting. The document contains `body`, `symbols`, `reader_question`,
`thesis`, and `invalidation`. Evidence is copied into a content-addressed archive.

Legacy/manual calibration drafts can still be stored with this CLI. In
`COGNITION_LED` production mode, bare drafts cannot be approved for dispatch:
new production copy must come through `SquareCognitionExpressionService` and
its real source-bound semantic review. For installations without cognition-led
mode, `square_draft.py review DRAFT_ID --reviewer EDITOR --evidence-check NOTES`
still records an explicit editorial decision. Review binds the exact body, symbols, evidence,
thesis and assessment. Changing any of them requires another review. Market
evidence expires after twenty minutes; replace expired snapshots and rewrite
numeric statements before preparing a new draft. A reader-approved style is not
permanent approval for stale figures or future assertions.

## Publication

`run_square_distribution_worker.py` loads the existing private posting key and
flags, then dispatches at most one valid REVIEWED native draft. With no eligible
copy it reports `WAITING_FOR_EDITORIAL`. Historical website queues are never
claimed. Enabling a timer does not approve a draft.

The worker persists PUBLISHING before contacting Square. A numeric post ID with
API code 000000 creates SUCCESS. Rejection creates REJECTED. Timeouts, malformed
receipts and crashes after a claim require manual reconciliation; neither
PUBLISHING nor UNVERIFIED is automatically retried. Editing or reviewing an
attempted draft is prohibited. The transport uses only the Square posting key.

## Cognition provenance and deployment

Runtime preferences select `production_mode=COGNITION_LED` and the native
Square channel. Existing owner decisions can choose the first-class
`BINANCE_SQUARE` surface. `SquareCognitionExpressionService` executes inside the
shared research cycle, after actual inquiry and the decision to express, using
that cycle's runtime. There is no separate periodically invoked author.

Production admission binds the actual `learning_result.json`, `cognition.json`
and `square_expression_review.json`. The final review is bound to the exact
public body. Altered or missing receipts block dispatch even if market numbers
are valid. Immutable per-cycle preparation preserves idempotency across a crash
before the result receipt. Publishing remains independent of cognitive work.

Recent successful Square posts enter subsequent owner and expression decisions
as experience; they are not permanent prose templates or evidence that their
market interpretation remains true. Semantic novelty is model-assessed, not
guaranteed by JSON fields. Continuous real research must validate the effect.
Engagement metrics remain unavailable; receipt success is not audience approval.

## Editorial quality reference

See [the quality reference and anti-template guidance](binance_square_editorial_standard.md)
and `square_editorial/editorial_preferences.json` before drafting or reviewing.
Style acceptance does not bypass fresh evidence or the reviewed-copy gate.
