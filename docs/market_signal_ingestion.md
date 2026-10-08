# Ferro market signal ingestion — first working slice

A source module modeled on the observation half of Bnsbot. The *output is research evidence*, not templated posts.

```
Official market APIs / crypto news RSS / ETF daily flows
  -> independent one-shot collector (no browser, no LLM)
  -> SQLite source status and immutable signal identities
  -> first-success baseline / 36h freshness / metric thresholds
  -> GoalEvidenceExplorer.market_signal_reader (source quotes retained)
  -> Ferro investigation, counter-evidence, learning and editorial checks
  -> existing audited website PublicationEvent
  -> independent X / Binance Square publication consumers
```

## Verified providers

- **Binance Spot**: public `/api/v3/ticker/24hr` for BTC, ETH, SOL, BNB, XRP.
  Emits a market-move event only when the absolute 24-hour change crosses a *higher* 3% band after a previous observed baseline; not on every tick. Volume floor: 10m USDT.
- **BlockBeats**: its published RSS v2 **newsflash** and **article** feeds.
- **CoinGlass BTC ETF flows**: the documented `/api/etf/bitcoin/flow-history` endpoint, enabled only when `COINGLASS_API_KEY` is present. This is **delayed daily data**, not live order flow.
- **Jin10 and PANews**: **not preconfigured**. They are supported through the opt-in RSS connector once verified URLs/rights are available. Do not claim these as working live integrations before confirmation.
- Existing OKX price evidence and Runtime Web/X search remain unchanged.

Each source has its own success/failure row. On the first *successful* fetch, old items establish a baseline and never trigger a posting storm. A failed first fetch does not establish a baseline. SQLite deduplicates by source/kind/external ID, expires old rows after 30 days, and caps the number of research candidates. Nothing new is written into Ferro's autobiographical memory until its existing learning system justifies it.

## Startup

Prepare a private environment file outside the repo (no key for default Binance/BlockBeats sources):

```ini
CONTENT_MARKET_INGESTION_ENABLED=1
# Optional only after verifying publishing/resource budget:
CONTENT_MARKET_WAKE_ENABLED=0
# Optional subscribed ETF data:
# COINGLASS_API_KEY=...
# Optional vetted RSS feeds (HTTPS only):
# CONTENT_MARKET_EXTRA_RSS_JSON=[{"name":"panews","url":"https://.../verified-rss"}]
```

Run `scripts/run_market_signals_worker.py` with `CONTENT_APPLICATION_ROOT` matching Ferro's normal state. A sample one-shot systemd service and timer are in `deploy/systemd`. They are **not installed or enabled** by this patch. The collector does not call an LLM or a publishing endpoint.

With `CONTENT_MARKET_WAKE_ENABLED=1`, genuinely new signals schedule an existing `CONTENT_COGNITION` wake, **not a post**. Ferro can reject, question, investigate or ignore the evidence. Without wake enabled, the next content research cycle will see it through the injected evidence-reader port.

## Not implemented by this first slice

There is not yet a claimed 40-source real-time network, direct Jin10/PANews feed, a Binance announcements parser, a chart/card renderer, scheduled posting pace controls, or Square engagement/affiliate reconciliation. These are separate components to add after measuring the quality and reliability of the evidence->learning->publication chain. API response shape and geographic availability must be monitored in deployment.
