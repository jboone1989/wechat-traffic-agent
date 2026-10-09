# Ferro: Binance Wallet Tracker CLI + WebSocket source (read-only)

Implementation date: 2026-10-09

## What is integrated

- `src/wechat_traffic_agent/sources/binance_wallet_cli.py`: command builder,
  bounded public Smart Money/KOL token and transaction queries, *optional*
  authenticated **read-only** group/list/follow queries, and 6 documented WS
  subscription modes (`smy`, `kol`, chain-level `wallet`, `address`,
  `address-list`, `following`). No account connection, trade, transfer, edit,
  follow/unfollow write, or API key operation.
- `src/wechat_traffic_agent/sources/binance_wallet_store.py`: durable SQLite
  event merge and deduplication. The same `signalId` is an **update**, not a
  second entry signal. Null updates preserve the original trigger price/time.
  Chain + transaction hash + log ID distinguish executed transaction events.
  Keep at most 3000 observations and remove entries older than 7 days.
- `scripts/run_binance_wallet_tracker.py`: independent optional worker:
  `poll` (bounded requests), `ws` (finite capture), or `stream` (official
  CLI's reconnecting continuous WebSocket, CLI >=1.9.1 required).
- `GoalEvidenceExplorer` receives wallet `EvidenceCandidate` entries
  through an optional `wallet_reader` injection. It reads up to four poll
  snapshots and four recent WS observations; only if newer than 1 hour.
  It cannot publish or change the owner's research goals by itself.
- No additional long-lived process has been enabled on the memory-constrained
  production VM. This branch has not been merged to main / rolled to the
  running release.

## Source provenance

[Official wallet tracker skill](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-wallet-tracker/SKILL.md)
and [CLI reference](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-wallet-tracker/references/cli.md).

Public `--tag-type smy` / `--tag-type kol` do not require login.
Private `--group-id`, `--following`, own groups and address-list management
require an authenticated session and must not be used by default.

`baw tracker ws` can emit an initial notification followed by dozens of
updates for the same signal. Missing `status` / `currentPrice` on the first
push are normal, not missing signals. The official 1.9.1+ CLI implements
connection-level keepalive and reconnection in `--duration 0` mode.
Our stream wrapper supervises only the CLI process, not the socket itself.

## How to test and run, after operator approval of the CLI

Installed npm package: `@binance/agentic-wallet@1.10.0` on the VM.
**Executing `baw` itself currently requires explicit host operator approval**,
so real CLI polling and socket subscriptions were *not* live verified during
this change; running `npm list` proves installation only.

Standalone commands in the branch:

```bash
python scripts/run_binance_wallet_tracker.py
FERRO_WALLET_TRACKER_MODE=ws FERRO_WALLET_WS_SCOPE=smy FERRO_WALLET_WS_SECONDS=15 python scripts/run_binance_wallet_tracker.py
FERRO_WALLET_TRACKER_MODE=stream FERRO_WALLET_WS_SCOPE=smy python scripts/run_binance_wallet_tracker.py
FERRO_WALLET_TRACKER_MODE=ws FERRO_WALLET_WS_SCOPE=kol FERRO_WALLET_WS_CHAIN=56 python scripts/run_binance_wallet_tracker.py
```

Do not route these through an unrelated preapproved command to circumvent
any pending host approval. Use the remote connector's normal authorization
workflow before a live `baw` execution.

`FERRO_WALLET_CHAINS=56,CT_501` allows multi-chain public polls. Default is
BSC only, one-off run. The worker never accesses wallet secrets.

## Runtime/deployment

- `deploy/systemd/ferro-binance-wallet-poll.service` + `.timer`:
  30-minute short-lived poll, disabled/not installed.
- `deploy/systemd/ferro-binance-wallet-stream.service`: optional persistent
  WS source, disabled/not installed; single process, native CLI reconnect,
  `MemoryMax=220M` and CPU quota. Enable only if RAM capacity and permission
  are validated; avoid reintroducing OOM.
- Observations: `/home/ubuntu/content-agent-state/sources/binance_wallet/latest.json`,
  `events.sqlite3`; no cross-link to Square posting.
- Query errors are isolated by source and reported in the worker JSON receipt.

## Test contract

`python -m pytest -q tests/test_binance_wallet_tracker.py tests/test_binance_web3_public.py tests/test_content_explorer.py`

Covers read-only CLI args, public/private scope separation, finite and
unlimited WS arguments, CLI version requirement, null-preserving signal state
merge, tx-level deduplication, per-source errors, timely evidence injection,
and mock WS JSON event processing.

## Remaining live acceptance (must not be called finished)

1. Approve the scoped `baw` host command using the normal connector workflow.
2. Run `baw --version`, `baw tracker token query -c 56 --tag-type smy --json`,
   `baw tracker tx query -c 56 --tag-type kol --json` and
   `baw tracker ws --smy --duration 15 --json`. Verify real response shape
   and event receipt; an empty 15-second sample is not a failed connection.
3. Verify optional longer running WS stream, memory usage and process recovery.
4. Merge, deploy and enable timer **after** resource acceptance; only then
   may the live Ferro goal-driven cycle consume this data automatically.

The earlier 11 Binance Web3 public HTTP feeds were already separately
live-verified and remain available; that is **not** proof of the new CLI/WS.
