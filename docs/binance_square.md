# Binance Square distribution (opt-in)

Ferro's audited website publication emits one canonical `PublicationEvent`.
The existing outbox creates one task per selected channel. With Square enabled,
Ferro queues `binance_square` beside `x`; one research result is reused,
not re-researched or blindly copied from unrelated accounts.

The Square adapter composes a short message from the approved title and
summary, links to the full article and evidence, and adds `$BTC`, `$ETH` etc
only when the event has an exact known coin symbol. The adapter uses the
official Square **posting-only** OpenAPI key (not a trading key) and publishes
via `/bapi/composite/v1/public/pgc/openApi/content/add`.

## Configuration

Obtain the posting-only key from:
https://www.binance.com/square/creator-center/home

Add the following settings **outside the repository** to the service
environment (or the private `EnvironmentFile` shown in the sample unit):

```ini
CONTENT_BINANCE_SQUARE_ENABLED=1
CONTENT_BINANCE_SQUARE_WRITES_ENABLED=1
BINANCE_SQUARE_OPENAPI_KEY=your-square-posting-only-key
```

Do not enable without a key. Keep the config file readable only by its owner.
By default, Square is disabled and existing X behavior is unchanged.
If enabled but writes are disabled, future audited website events remain
queued without attempting publication; X distribution stays independent.

## Execution

The existing CHECK_X outbox drain can also process Square tasks. For faster
Square delivery independent of X cognition or browser health, run
`scripts/run_square_distribution_worker.py` using the optional oneshot
systemd timer. It consumes at most one queued Square task per run (two-minute
interval), with no extra persistent Python worker.

The supplied `ferro-square-distribution.service` and `.timer` are examples
and are **not enabled automatically**. Verify the Python environment and
paths before installing to systemd. Use the same `CONTENT_APPLICATION_ROOT`
as the main Ferro worker.

## Receipts and failure semantics

A successful API response must contain `code="000000"` and a numeric
`data.id`. Only then does Ferro save a `SUCCESS` outbox receipt with
`published_post_id` and the public URL:

`https://www.binance.com/square/post/{id}`

If a timeout/HTTP 5xx/invalid receipt could hide a successful submission,
the task becomes `FAILED_PERMANENT` with a reason for manual reconciliation
and is *not* resent automatically. API rejection (including invalid/expired
key, sensitive words and daily limit) is also surfaced without blind retries.
New independent events are unaffected. No publication is claimed without
a verified post ID.

The official Square posting API handles publication only. Engagement numbers,
follower growth, cashtag fee rebates, market monitoring, and cross-platform
X-post importing are separate capabilities; this integration does not claim
to fetch them or produce them automatically.
