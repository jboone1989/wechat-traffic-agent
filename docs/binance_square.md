# Binance Square PublicationEvent consumer (opt-in)

Ferro's existing audited website publisher emits immutable `PublicationEvent`
records. The Binance Square consumer **only reads** those records. It is not
registered in the producer, X worker, cognitive loop, learning or memory.

```text
Website -> PublicationEvent (durable log)
                  |                   |
            existing X outbox     Binance Square consumer (separate)
                                      |
                               channel outbox task
                                      |
                               Square OpenAPI
                                      |
                             SUCCESS/FAILED receipt
                                      |
                             optional operator mailbox
```

The Square adapter transforms only the approved title/summary + source URL;
it attaches `$BTC`, `$ETH`, etc only for exact known coin tags in the event.
No fresh research, LLM, market search, or browser action is launched by the
consumer. It uses a Square **posting-only** key, not a trading key.

## Configuration

Get the posting key from
https://www.binance.com/square/creator-center/home

In the independent consumer service's private environment:

```ini
CONTENT_BINANCE_SQUARE_ENABLED=1
CONTENT_BINANCE_SQUARE_WRITES_ENABLED=1
BINANCE_SQUARE_OPENAPI_KEY=your-posting-only-key
# Optional: share the existing operator mailbox for WeCom notifications
CONTENT_SQUARE_OPERATOR_STATE_DIR=/path/to/operator/state
```

The default is disabled. Enabling the consumer never changes the producer's
X channels or schedules and never starts another browser.

On its **first enabled run**, the consumer records its activation watermark in
the shared publication state. It handles events *published after that point*;
it deliberately does not backfill older website articles. When writes are
disabled or the key is absent, newly observed events remain queued for later,
without touching X's queue. Once the key is configured, the consumer processes
one Square task per invocation.

## Execution

Run `scripts/run_square_distribution_worker.py` with the same
`CONTENT_APPLICATION_ROOT` as the website publisher. The optional
`ferro-square-distribution.service` and `.timer` invoke the one-shot
consumer every two minutes. They are not installed or enabled automatically.

Only this worker imports the Square consumer. The generic event store and
distribution worker do not import Binance; the X worker is unchanged.

## Receipts

The Binance v1 posting OpenAPI receives `contentType=1` and
`bodyTextOnly`. It must return `code="000000"` with a numeric `data.id`
to record a confirmed `SUCCESS` and Square post URL. HTTP 5xx, timeouts,
unreadable API replies and successful API codes without an ID are left for
manual reconciliation, not blind automatic resubmission. The generic outbox
also records the verified post ID.

When `CONTENT_SQUARE_OPERATOR_STATE_DIR` is set, the consumer adds a
deduplicated result notification to the existing operator mailbox. It does
not call the X-specific `DailyPostingGoal` notifier. Square attempts do not
count towards X's original-post quota.

This feature does not fetch engagement metrics, scrape markets, mirror X
posts or guarantee creator reward eligibility.
