# Native BTC/ETH Square editorial publishing

Website summaries are no longer syndicated to Binance Square. The old event
consumer is inert, and the old summary adapter rejects publication. Website/X
events and historical Square receipts are preserved.

The scheduled worker reads `square_editorial/drafts`, not website events:

```text
Official public BTC/ETH endpoints -> immutable raw snapshot + aligned metrics
                                  -> Chinese native draft (DRAFT)
                                  -> editorial assessment (REVIEWED)
                                  -> copy/evidence/freshness check
                                  -> durable PUBLISHING claim -> Square receipt
```

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

After an actual editorial review, use `square_draft.py review DRAFT_ID --reviewer
EDITOR --evidence-check NOTES`. Review binds the exact body, symbols, evidence,
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

No engagement analytics transport or autonomous author is implemented here.
The collector, draft/review tools and gated publishing worker are deployed;
selection and semantic review are explicit editorial work.

## Editorial quality reference

See [the quality reference and anti-template guidance](binance_square_editorial_standard.md)
and `square_editorial/editorial_preferences.json` before drafting or reviewing.
Style acceptance does not bypass fresh evidence or the reviewed-copy gate.
