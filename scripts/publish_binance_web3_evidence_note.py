#!/usr/bin/env python3
"""One-time live-data -> researched Square note, with durable no-replay receipt.

Uses two independently fetched Binance Web3 public sources. No trades or X.
Does not create a fake website PublicationEvent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.platforms.binance_square import BinanceSquareClient
from run_square_distribution_worker import load_private_config

STATE = Path("/home/ubuntu/content-agent-state")
SNAPSHOT = STATE / "sources/binance_web3/latest.json"
RECEIPT = STATE / "distribution/binance_research_note_20261009.json"


def build_note(snapshot: dict) -> dict:
    now = datetime.now(timezone.utc)
    when = datetime.fromisoformat(snapshot["observed_at"])
    if (now - when).total_seconds() > 3600 or when > now:
        raise ValueError("Binance research data snapshot is stale")
    groups = {row["source"]: row for row in snapshot["observations"]}
    inflows = groups["web3-smart-inflow"]["sample"]
    trends = groups["web3-token-rank"]["sample"]
    overlapping = [(a,b) for a in inflows for b in trends
                   if a.get("tokenName") and a.get("tokenName") == b.get("symbol")
                   and a.get("ca") and b.get("contractAddress")
                   and a["ca"].lower() != b["contractAddress"].lower()]
    if not overlapping:
        raise ValueError("no verified same-name, different-contract mismatch")
    a,b=overlapping[0]
    symbol=a["tokenName"]
    chain=groups["web3-token-rank"]["parameters"]["chainId"]
    value=Decimal(b["percentChange24h"])
    inflow=Decimal(a["inflow"])
    title="同名代币不是同一资产：两个 Binance Web3 榜单里的数据陷阱"
    body=(
        f"我从 Binance Web3 的 BSC 两个公开榜单获取实时数据，发现一个容易误导研究的细节："
        f"同样叫「{symbol}」的代币，合约地址竟然不同。\n\n"
        f"① 代币热度榜：{symbol}，合约 {b['contractAddress']}；"
        f"榜单记录的24小时变化 {value:+.2f}%。\n"
        f"② Smart Money 资金流榜：{symbol}，合约 {a['ca']}；"
        f"对应榜单的 inflow 字段为 {inflow:+,.2f}（以接口原始字段为准，不猜单位）。\n\n"
        "推理：如果程序按 symbol/name 做 join，就会把②的资金流错误归给①的资产，"
        "随后可能生成一篇貌似有数据支持、其实主体都错了的分析。\n"
        "更可靠的关联键至少是 (chainId, contractAddress)，而不是名称；"
        "还要核对采样时间及指标定义。这个观察不证明资金流导致价格变化。\n\n"
        f"观测时间：{when.strftime('%Y-%m-%d %H:%M UTC')}。"
        "接口来源：Binance Web3 public token-rank (rankType=10) 与 smart-money-inflow (period=24h, chainId=56)。"
        "官方 Skill 与参数说明：https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/crypto-market-rank 。"
        "不构成投资建议。"
    )
    if len(title)+len(body)>1500:
        raise ValueError("note exceeds Square posting length")
    return {"title":title,"body":title+"\n\n"+body,
            "observed_at":when.isoformat(),
            "evidence_sources":["web3-token-rank","web3-smart-inflow"],
            "contracts":[a["ca"],b["contractAddress"]]}


def main() -> int:
    load_private_config(STATE)
    RECEIPT.parent.mkdir(parents=True,exist_ok=True)
    lock_path=RECEIPT.with_suffix(".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if RECEIPT.exists():
            prior=json.loads(RECEIPT.read_text(encoding="utf-8"))
            print(json.dumps({"status":"ALREADY_ATTEMPTED","previous_status":prior.get("status"),
                              "url":prior.get("url"),"post_id":prior.get("post_id")},ensure_ascii=False))
            return 0 if prior.get("status")=="SUCCESS" else 2
        doc=build_note(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
        note_hash=sha256(doc["body"].encode("utf-8")).hexdigest()
        RECEIPT.write_text(json.dumps({
            "status":"SUBMISSION_UNKNOWN","started_at":datetime.now(timezone.utc).isoformat(),
            "evidence_sources":doc["evidence_sources"],"source_observed_at":doc["observed_at"],
            "contracts":doc["contracts"],"note_sha256":note_hash,
        },ensure_ascii=False),encoding="utf-8")
        try:
            post_id=BinanceSquareClient().post_text(doc["body"])
        except Exception as exc:
            print(json.dumps({"status":"SUBMISSION_UNKNOWN_OR_REJECTED",
                              "error_type":type(exc).__name__},ensure_ascii=False))
            return 1
        url="https://www.binance.com/square/post/"+post_id
        RECEIPT.write_text(json.dumps({
            "status":"SUCCESS","post_id":post_id,"url":url,
            "source_observed_at":doc["observed_at"],"note_sha256":note_hash,
            "published_at":datetime.now(timezone.utc).isoformat(),
            "evidence_sources":doc["evidence_sources"],
            "contracts":doc["contracts"],
        },ensure_ascii=False),encoding="utf-8")
        print(json.dumps({"status":"SUCCESS","url":url,"post_id":post_id,
                          "source_observed_at":doc["observed_at"]},ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
