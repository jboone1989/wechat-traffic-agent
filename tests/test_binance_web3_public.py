"""Read-only Binance Web3 source contracts and Ferro evidence bridge."""
from datetime import datetime, timedelta, timezone
import asyncio
import json

import httpx
import pytest

from wechat_traffic_agent.sources.binance_web3_public import (
    PUBLIC_READS, ReadSpec, _observation, _rows, collect_binance_public,
    read_binance_candidates, save_binance_snapshot,
)

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_public_registry_never_uses_asset_permissions_or_secret():
    assert len(PUBLIC_READS) == 11
    assert all(s.method in {"GET", "POST"} for s in PUBLIC_READS)
    assert all("/public/" in s.url or "/api/v3/ticker/24hr" in s.url for s in PUBLIC_READS)
    assert not any("order" in s.url or "transfer" in s.url for s in PUBLIC_READS)
    assert all(not ({"api_key", "secret", "signature", "apiKey"} & set(s.params)) for s in PUBLIC_READS)


def test_read_rows_of_spot_and_web3():
    assert len(_rows({"symbol": "BTCUSDT", "lastPrice": "5"}, PUBLIC_READS[0])) == 1
    assert len(_rows({"code":"000000","data":{"tokens":[{"symbol":"BNB"}]}},
                     PUBLIC_READS[3])) == 1
    with pytest.raises(ValueError):
        _rows({"code":"000002","message":"parameter invalid"}, PUBLIC_READS[2])


def test_stable_observation_provides_source_revision():
    data={"code":"000000","data":[{"symbol":"BTC","price":"1.25","netInflow":"40"}]}
    obs = _observation(PUBLIC_READS[2], data, NOW)
    again = _observation(PUBLIC_READS[2], data, NOW)
    assert obs["revision"] == again["revision"]
    assert obs["row_count"] == 1 and obs["sample"][0]["netInflow"] == "40"


def test_public_collector_keeps_successes_when_one_provider_errors():
    def fake(request):
        if "token/search/ai" in str(request.url):
            return httpx.Response(200,json={"code":"999999","data":[]})
        if "ticker/24hr" in str(request.url):
            return httpx.Response(200,json={"symbol":"BTCUSDT","lastPrice":"11",
                                            "priceChangePercent":"2.1","quoteVolume":"1"})
        if "token/dynamic/info/ai" in str(request.url):
            return httpx.Response(200,json={"code":"000000","data":{"price":"1","volume24h":"2"}})
        if "security/token/audit" in str(request.url):
            return httpx.Response(200,json={"code":"000000","data":{"riskLevel":1,"hasResult":True}})
        if "unified/rank" in str(request.url):
            return httpx.Response(200,json={"code":"000000","data":{"tokens":[{"symbol":"BTC","price":"11"}]}})
        if "hype/rank" in str(request.url):
            return httpx.Response(200,json={"code":"000000","data":{"leaderBoardList":[{"metaInfo":{"symbol":"BTC"},"socialHypeInfo":{"sentiment":"neutral"}}]}})
        return httpx.Response(200,json={"code":"000000","data":[{"symbol":"BNB","price":"6"}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
            return await collect_binance_public(client, now=NOW)
    report=asyncio.run(run())
    assert len(report["observations"]) == 10
    assert report["failures"] == [{"source":"web3-token-search","error":"ValueError"}]
    assert not report["publication_authority"]


def test_snapshot_loads_as_valid_ferro_candidates(tmp_path):
    from wechat_traffic_agent.content_explorer import EvidenceCandidate
    obs = _observation(PUBLIC_READS[0],
        {"symbol":"BTCUSDT","lastPrice":"100","priceChangePercent":"5","quoteVolume":"1000"}, NOW)
    report={"observed_at":NOW.isoformat(),"observations":[obs],"failures":[],"publication_authority":False}
    save_binance_snapshot(tmp_path,report)
    candidates=read_binance_candidates(tmp_path, NOW)
    assert len(candidates)==1
    assert EvidenceCandidate.model_validate(candidates[0]).source_kind=="public-api"
    assert read_binance_candidates(tmp_path,NOW+timedelta(hours=2))==[]


def test_empty_snapshot_and_repeated_collection_does_not_post(tmp_path):
    assert read_binance_candidates(tmp_path,NOW)==[]
    assert not (tmp_path/"distribution").exists()
