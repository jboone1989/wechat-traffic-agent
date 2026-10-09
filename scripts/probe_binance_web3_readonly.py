#!/usr/bin/env python3
"""Probe only documented, public Binance Web3 read endpoints; never auth, trade or publish."""
import asyncio
import json
import sys

import httpx

ROOT = "https://web3.binance.com/bapi/defi"
REQS = [
    ("spot_ticker", "GET", "https://api.binance.com/api/v3/ticker/24hr", {"symbol":"BTCUSDT"}),
    ("smart_money", "POST", ROOT + "/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai", {"chainId":"56","page":1,"pageSize":5}),
    ("smart_inflow", "POST", ROOT + "/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query/ai", {"chainId":"56","period":"24h","tagType":2}),
    ("token_rank", "POST", ROOT + "/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai", {"rankType":10,"chainId":"56","page":1,"size":5}),
    ("address_pnl_rank", "GET", ROOT + "/v1/public/wallet-direct/market/leaderboard/query/ai", {"chainId":"56","period":"7d","tag":"ALL","pageNo":1,"pageSize":5}),
    ("token_search", "GET", ROOT + "/v5/public/wallet-direct/buw/wallet/market/token/search/ai", {"chainId":"56","keyword":"BNB"}),
    ("rwa_list", "GET", "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/rwa/stock/detail/list/ai", {}),
    ("token_dynamic", "GET", ROOT + "/v4/public/wallet-direct/buw/wallet/market/token/dynamic/info/ai", {"chainId":"56","contractAddress":"0x55d398326f99059ff775485246999027b3197955"}),
    ("token_audit", "POST", ROOT + "/v1/public/wallet-direct/security/token/audit", {"binanceChainId":"56","contractAddress":"0x55d398326f99059ff775485246999027b3197955","requestId":"1f8c9454-3f99-44c7-a0d8-4b6a8662dcc6"}),
    ("social_hype", "GET", ROOT + "/v1/public/wallet-direct/buw/wallet/market/token/pulse/social/hype/rank/leaderboard/ai", {"chainId":"56","targetLanguage":"en","timeRange":1}),
    ("topic_rush", "GET", ROOT + "/v2/public/wallet-direct/buw/wallet/market/token/social-rush/rank/list/ai", {"chainId":"56","rankType":10,"sort":10}),
]

async def probe(client, name, method, url, params):
    try:
        kw = {"params":params} if method == "GET" else {"json":params}
        r = await client.request(method, url, **kw)
        data = r.json()
        body = data.get("data") if isinstance(data, dict) else None
        summary = {"status_code":r.status_code, "code":data.get("code") if isinstance(data,dict) else None,"message":str(data.get("message",""))[:160] if isinstance(data,dict) else "not json", "data_type":type(body).__name__, "data_keys":list(body)[:25] if isinstance(body,dict) else [], "rows":len(body) if isinstance(body,list) else None}
        if isinstance(body,list) and body and isinstance(body[0],dict):
            summary["first_row"]={k:str(v)[:130] for k,v in list(body[0].items())[:14]}
        if isinstance(body,dict):
            for k,v in body.items():
                if isinstance(v,list):
                    summary["list_key"]=k; summary["list_count"]=len(v)
                    if v and isinstance(v[0],dict):
                        summary["first_row"]={a:str(b)[:120] for a,b in list(v[0].items())[:14]}
                    break
        return {name:summary}
    except Exception as exc:
        return {name:{"error":type(exc).__name__, "detail":str(exc)[:120]}}

async def main():
    async with httpx.AsyncClient(timeout=11.0,follow_redirects=False,headers={"Accept":"application/json","User-Agent":"binance-web3/2.0 (Skill)"}) as client:
        rows=await asyncio.gather(*(probe(client,*x) for x in REQS))
    print(json.dumps(rows,ensure_ascii=False))

if __name__=="__main__":
    asyncio.run(main())
