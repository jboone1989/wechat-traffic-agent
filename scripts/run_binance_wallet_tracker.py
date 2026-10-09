#!/usr/bin/env python3
"""Bounded read-only Binance wallet tracker job; independently scheduled.

No trading, no wallet key, no auto publication. Uses the official baw tracker CLI.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.sources.binance_wallet_cli import (
    BawTrackerCli, TrackerCliFailure, TrackerCliUnavailable, capture_ws, stream_ws,
)
from wechat_traffic_agent.sources.binance_wallet_store import (
    WalletTrackerStore, summarized_poll,
)


def collect(*, cli, store, now, chains=("56",), tags=("smy", "kol")):
    results, failures = {}, []
    for chain in chains:
        for tag in tags:
            for mode in ("token", "tx"):
                name = f"{chain}-{tag}-{mode}"
                try:
                    data = (cli.token_query(chain, tag) if mode == "token"
                            else cli.trade_query(chain, tag))
                    summary = summarized_poll(data)
                    if not summary["row_count"]:
                        raise ValueError("empty tracker result")
                    results[name] = summary
                except (TrackerCliFailure, TrackerCliUnavailable, ValueError) as exc:
                    failures.append({"source": name, "error_type": type(exc).__name__})
    path = store.save_poll(results, now=now)
    return {"sources": {k: v["row_count"] for k, v in results.items()},
            "failures": failures, "snapshot": str(path),
            "publication_authority": False}


def observe_ws(*, cli, store, now, mode="smy", chain="56", duration=15,
               address=None, address_list=None):
    events = capture_ws(cli, mode=mode, chain=chain, duration=duration,
                        address=address, address_list=address_list)
    receipt = store.ingest(events, kind=mode, now=now)
    return {"status": "OK" if events else "NO_EVENTS",
            "received_pushes": len(events), "changes": receipt,
            "publication_authority": False}


def run():
    root = Path(os.getenv("CONTENT_APPLICATION_ROOT", "/home/ubuntu/content-agent-state"))
    store = WalletTrackerStore(root)
    cli = BawTrackerCli()
    if not cli.available():
        return {"status": "UNAVAILABLE", "reason": "baw binary is not installed",
                "publication_authority": False}
    now = datetime.now(timezone.utc)
    mode = os.getenv("FERRO_WALLET_TRACKER_MODE", "poll").lower()
    if mode == "poll":
        chains = tuple(x.strip() for x in os.getenv("FERRO_WALLET_CHAINS", "56").split(","))
        result = collect(cli=cli, store=store, now=now, chains=chains)
    elif mode in {"ws", "stream"}:
        scope = os.getenv("FERRO_WALLET_WS_SCOPE", "smy")
        chain = os.getenv("FERRO_WALLET_WS_CHAIN", "56")
        seconds = int(os.getenv("FERRO_WALLET_WS_SECONDS", "15"))
        address = os.getenv("FERRO_WALLET_WS_ADDRESS") or None
        address_list = os.getenv("FERRO_WALLET_WS_ADDRESS_LIST") or None
        if mode == "ws":
            result = observe_ws(cli=cli, store=store, now=now, mode=scope,
                                chain=chain, duration=seconds, address=address,
                                address_list=address_list)
        else:
            result = {"stream_counts": stream_ws(
                cli, store, mode=scope, chain=chain, address=address,
                address_list=address_list)}
    else:
        raise ValueError("FERRO_WALLET_TRACKER_MODE must be poll, ws, or stream")
    return {"status": "OK", "mode": mode, **result}


if __name__ == "__main__":
    try:
        print(json.dumps(run(), ensure_ascii=False))
    except (TrackerCliFailure, TrackerCliUnavailable, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "publication_authority": False}))
        raise SystemExit(1)
