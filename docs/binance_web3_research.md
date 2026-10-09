# Binance public research feeds → Ferro evidence → Square

Current implementation (2026-10-09): the optional Binance Web3 collector
`scripts/run_binance_research_collector.py` reads only public endpoints.
It is independent from wallet signing, X, publishing, learning and the main loop.

## Live-verified sources

On the VM, all **11** endpoints completed with valid observations:

| Source id | Read-only resource | Practical use |
|---|---|---|
| `spot-btc-24h` | Spot BTC/USDT 24h ticker | Price, change and quote volume |
| `web3-smart-money` | BSC smart-money trade signals | Per-trade signals (not endorsements) |
| `web3-smart-inflow` | BSC token smart-money inflow rank | Fund-flow observations |
| `web3-token-rank` | BSC token trend rank | Trending tokens, metadata, liquidity |
| `web3-token-search` | BSC token search | Resolve token identities |
| `web3-trader-leaderboard` | BSC address PnL rank | Observed results, not generalizable performance |
| `web3-token-dynamic` | BSC USDT contract dynamic info | Token price and volume fields |
| `web3-token-audit` | BSC USDT contract security audit | Contract-risk observations, not safety proof |
| `web3-social-hype` | BSC token social attention rank | Sentiment labels and attention |
| `web3-topic-rush` | BSC AI trend/topic rank | Possible research questions, not evidence of accuracy |
| `web3-rwa-stocks` | Tokenized equities metadata list | Stocks and token/share multiplier |

These URLs and request parameter shapes come from Binance's official
[Skills Hub at commit 9960c675](https://github.com/binance/binance-skills-hub/tree/9960c675387bd27f8866645b83693c1fa87242f6/skills).
They are public `bapi` surfaces and may change; live success today is not
a guarantee of a documented stability promise. `query-token-info` K-line and
wallet-tracker `baw` WS/CLI are deliberately *not* claimed live-integrated;
neither are payment, trading or wallet signing APIs.

## Execution / storage

- One-shot: `python scripts/run_binance_research_collector.py`
- Snapshot: `$CONTENT_APPLICATION_ROOT/sources/binance_web3/latest.json`
- Reader: `read_binance_candidates(root, now)`, up to one-hour age, produces
  valid Ferro `EvidenceCandidate` public API entries.
- Wired into `GoalEvidenceExplorer` via the optional `binance_reader`
  injection when goal discovery is active.
- A provider failure does not prevent collecting and persisting other sources.
- `publication_authority=false` is explicit; **ingestion is never a posting trigger**.
- The optional timer runs every 20 minutes. It is a deployment *template* until
  its service is installed; code added to the source branch does not modify
  the already-running production release.
- Never match tokens by name or ticker only. Use (chainId, contractAddress),
  verify source/metric semantics, and preserve sampling time.

## First real research + publication

Observed a reproducible data-join hazard: two distinct BSC contracts are
both labelled 龙虾 in different Binance feeds. The first note was generated
from the saved snapshot (2026-10-09 02:09 UTC) and sent once through the
existing Square text posting client, with durable one-shot receipt
`$CONTENT_APPLICATION_ROOT/distribution/binance_research_note_20261009.json`.

[Square post 375278574767180](https://www.binance.com/square/post/375278574767180)

This proves data retrieval, evidence analysis and one verified Square API
submission. It **does not** imply the entire automated website editorial
pipeline has been deployed, nor that Square read/feedback API exists. The
Square web page itself may be inaccessible to public bot-based verification.

## Verify

`python -m pytest -q tests/test_binance_web3_public.py tests/test_binance_square_distribution.py tests/test_content_explorer.py`

Expected: 39 passed. Do not publish the one-off note again: it refuses
duplicate submissions based on a durable receipt.
