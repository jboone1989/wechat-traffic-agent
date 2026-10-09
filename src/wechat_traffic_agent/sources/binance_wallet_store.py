"""Bounded wallet feed evidence store, latest snapshots and stateful WS dedupe."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from .live_cache import live_cache_root
import sqlite3

OFFICIAL_DOC = ("https://github.com/binance/binance-skills-hub/blob/main/"
                "skills/binance-web3/binance-wallet-tracker/SKILL.md")
FIELDS = ("signalId", "chainId", "contractAddress", "ca", "address", "txHash",
          "logId", "ticker", "tokenName", "ts", "timestamp", "signalTriggerTime",
          "tradeSideCategory", "tradeSide", "txUsdValue", "buyCount",
          "sellCount", "consensusCount", "netInflow", "inflow", "tokenRiskLevel",
          "alertPrice", "currentPrice", "maxGain", "exitRate", "status",
          "tokenPrice", "price", "lastTrade", "latestTxTime")


def canonical_data(row: dict) -> dict:
    # The initial signal push often has nulls. Preserve them for later merges.
    data = row.get("data")
    if isinstance(data, dict):
        row = {**row, **data}
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        row = {**row, **data[0]}
    return {key: row[key] for key in FIELDS
            if key in row and isinstance(row[key], (str, int, float, bool, type(None)))}


def stable_id(row: dict, kind: str) -> str:
    chain = str(row.get("chainId") or "unknown")
    signal = row.get("signalId")
    tx = row.get("txHash")
    log = row.get("logId")
    if signal is not None and str(signal):
        core = f"{kind}:{chain}:signal:{signal}"
    elif tx:
        core = f"{kind}:{chain}:tx:{tx}:{log or ''}"
    else:
        core = json.dumps([kind, chain, row], sort_keys=True, ensure_ascii=False)
    return sha256(core.encode("utf-8")).hexdigest()


def merge_signal(old: dict, new: dict) -> dict:
    # Newer non-null fields supersede; omitted/null update doesn't erase trigger data.
    return {**old, **{k: v for k, v in new.items() if v is not None}}


class WalletTrackerStore:
    def __init__(self, state_root: Path):
        self.folder = live_cache_root(state_root) / "binance_wallet"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = self.folder / "events.sqlite3"

    def ingest(self, records: list[dict], *, kind: str, now: datetime) -> dict:
        counts = {"new": 0, "updated": 0, "unchanged": 0, "invalid": 0}
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS events ("
                               "id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                               "observed TEXT NOT NULL, body TEXT NOT NULL)")
            for raw in records[:200]:
                row = canonical_data(raw) if isinstance(raw, dict) else {}
                if not row:
                    counts["invalid"] += 1
                    continue
                identity = stable_id(row, kind)
                old = connection.execute("SELECT body FROM events WHERE id=?", (identity,)).fetchone()
                merged = merge_signal(json.loads(old[0]), row) if old else row
                if old and merged == json.loads(old[0]):
                    counts["unchanged"] += 1
                    continue
                connection.execute("INSERT INTO events(id,kind,observed,body) VALUES(?,?,?,?) "
                                   "ON CONFLICT(id) DO UPDATE SET observed=excluded.observed,"
                                   "body=excluded.body",
                                   (identity, kind, now.isoformat(),
                                    json.dumps(merged, ensure_ascii=False, sort_keys=True)))
                counts["updated" if old else "new"] += 1
            cutoff = (now - timedelta(hours=1)).isoformat()
            connection.execute("DELETE FROM events WHERE observed < ?", (cutoff,))
            connection.execute("DELETE FROM events WHERE id IN ("
                               "SELECT id FROM events ORDER BY observed DESC LIMIT -1 OFFSET 3000)")
        return counts

    def latest_events(self, *, limit=12) -> list[dict]:
        if not self.db.exists():
            return []
        with sqlite3.connect(self.db) as connection:
            items = connection.execute("SELECT kind,observed,body FROM events "
                                       "ORDER BY observed DESC LIMIT ?", (limit,)).fetchall()
        return [{"kind": kind, "observed_at": observed, "data": json.loads(body)}
                for kind, observed, body in items]

    def save_poll(self, results: dict, *, now: datetime) -> Path:
        path = self.folder / "latest.json"
        payload = {"observed_at": now.isoformat(), "sources": results,
                   "publication_authority": False}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        tmp.replace(path)
        return path


def _rows(response) -> list[dict]:
    if isinstance(response, list):
        return [x for x in response if isinstance(x, dict)]
    if isinstance(response, dict):
        for key in ("data", "rows", "list", "tokens", "transactions", "items"):
            value = response.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                return _rows(value)
    return []


def summarized_poll(response, *, max_rows=5) -> dict:
    rows = _rows(response)
    results = []
    for row in rows[:max_rows]:
        item = canonical_data(row)
        address_list = row.get("addressList")
        if isinstance(address_list, list):
            # CLI aggregates counts from addressList, but API doesn't supply top-level stats.
            item["consensusCount"] = len(address_list)
            item["netInflow"] = str(sum(float(a.get("inflow") or 0) for a in address_list
                                        if isinstance(a, dict)))
        if item:
            results.append(item)
    return {"row_count": len(rows), "sample": results}


def read_wallet_candidates(state_root: Path, now: datetime, *, max_age_seconds=3600) -> list[dict]:
    folder = live_cache_root(state_root) / "binance_wallet"
    candidates = []
    path = folder / "latest.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            observed = datetime.fromisoformat(data["observed_at"])
            if timedelta(0) <= now - observed <= timedelta(seconds=max_age_seconds):
                for name, result in list(data["sources"].items())[:4]:
                    candidates.append(_candidate(name, result, observed))
        except (ValueError, KeyError, TypeError, OSError):
            pass
    if (folder / "events.sqlite3").exists():
        for item in WalletTrackerStore(state_root).latest_events(limit=4):
            try:
                observed = datetime.fromisoformat(item["observed_at"])
                if timedelta(0) <= now - observed <= timedelta(seconds=max_age_seconds):
                    candidates.append(_candidate("ws-" + item["kind"],
                                                 {"sample": [item["data"]], "row_count": 1}, observed))
            except (ValueError, KeyError, TypeError):
                continue
    return candidates


def _candidate(name: str, summary: dict, observed: datetime) -> dict:
    evidence = json.dumps(summary["sample"], sort_keys=True, ensure_ascii=False)
    fingerprint = sha256((name + evidence).encode()).hexdigest()
    return {
        "evidence_id": "wallet:" + fingerprint[:32],
        "source_kind": "public-api", "source": "binance-wallet-" + name,
        "title": "Binance wallet observation: " + name,
        "summary": f"Binance tracker {name}; {summary.get('row_count',0)} observations; "
                   f"captured sample: {evidence[:1300]}",
        "evidence_text": evidence[:10000], "url": OFFICIAL_DOC,
        "observed_at": observed.isoformat(), "source_revision": "sha256:" + fingerprint,
        "evidence_refs": ["binance-wallet:sha256:" + fingerprint],
    }
