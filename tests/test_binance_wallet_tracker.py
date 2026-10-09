"""Baw CLI and WebSocket read-only integration tests; no remote CLI needed."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.sources.binance_wallet_cli import (
    BawTrackerCli, TrackerCliFailure, TrackerCliUnavailable, capture_ws,
    ws_args, _scope_args,
)
from wechat_traffic_agent.sources.binance_wallet_store import (
    WalletTrackerStore, canonical_data, read_wallet_candidates,
    stable_id, summarized_poll,
)
from wechat_traffic_agent.content_explorer import EvidenceCandidate, GoalEvidenceExplorer

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_public_commands_and_private_read_scopes(monkeypatch):
    calls = []
    monkeypatch.setattr("shutil.which", lambda binary: "/opt/bin/baw")
    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b'[{"tokenName":"BNB","ca":"0xabc"}]')
    cli = BawTrackerCli(executor=run)
    assert cli.token_query("56", "smy") and cli.trade_query("CT_501", "kol")
    assert cli.token_query("56", None, group_id=12)
    assert cli.follow_list() and cli.group_list() and cli.address_list(group_id=1)
    assert calls[0] == ["baw","tracker","token","query","-c","56","--period","24h",
                        "--token-size","20","--tag-type","smy","--json"]
    assert "--group-id" in calls[2]
    assert all(x[1] == "tracker" for x in calls)
    assert not any("add" in x or "delete" in x or "follow" in x
                   for x in calls[:3])


def test_cli_disallows_mutating_and_invalid_scopes():
    with pytest.raises(ValueError):
        _scope_args(None,None)
    with pytest.raises(ValueError):
        _scope_args("smy",4)
    with pytest.raises(ValueError):
        ws_args(mode="foo")
    with pytest.raises(ValueError):
        ws_args(mode="smy",duration=0)
    with pytest.raises(ValueError):
        ws_args(mode="kol",chain="fake")
    assert ws_args(mode="smy") == ["ws","--smy","--duration","15"]
    assert ws_args(mode="kol",chain="56") == ["ws","--kol","-c","56","--duration","15"]
    assert ws_args(mode="wallet",chain="CT_501")[-4:] == ["--wallet","SOL","--duration","15"]
    assert ws_args(mode="address",address="0xabc",chain="56")[:4] == ["ws","--address","0xabc","-c"]


def test_ws_capture_parses_json_lines_without_trades(monkeypatch):
    monkeypatch.setattr("shutil.which",lambda _: "/opt/bin/baw")
    calls=[]
    def executor(argv,**kw):
        calls.append((argv, kw["timeout"]))
        output='{"signalId":"42","status":null,"alertPrice":"1"}\nnot-json\n{"signalId":"42","status":"exitRate","maxGain":"0.7"}\n'
        return SimpleNamespace(returncode=0,stdout=output.encode())
    result=capture_ws(BawTrackerCli(),executor=executor,duration=10)
    assert len(result)==2 and calls[0][1]==25
    assert "--duration" in calls[0][0] and "10" in calls[0][0]


def test_cli_missing_binary_reports_unavailable(monkeypatch):
    monkeypatch.setattr("shutil.which",lambda _: None)
    assert not BawTrackerCli().available()
    with pytest.raises(TrackerCliUnavailable):
        BawTrackerCli().trade_query()


def test_repeated_signal_merges_instead_of_double_count(tmp_path):
    store=WalletTrackerStore(tmp_path)
    initial={"signalId":100,"chainId":"56","contractAddress":"0xab",
             "signalTriggerTime":1791500000000,"alertPrice":"0.1",
             "currentPrice":None,"status":None}
    later={"signalId":100,"chainId":"56","contractAddress":"0xab",
           "currentPrice":"0.2","maxGain":"1.0","status":"exitRate"}
    assert store.ingest([initial],kind="smy",now=NOW)["new"] == 1
    assert store.ingest([later],kind="smy",now=NOW)["updated"] == 1
    assert store.ingest([later],kind="smy",now=NOW)["unchanged"] == 1
    items=store.latest_events()
    assert len(items)==1
    assert items[0]["data"]["alertPrice"]=="0.1"
    assert items[0]["data"]["maxGain"]=="1.0"


def test_transaction_identity_includes_log_and_chain():
    first={"chainId":"56","txHash":"0xab","logId":"1","address":"0x1"}
    other={**first,"logId":"2"}
    assert stable_id(first,"kol")!=stable_id(other,"kol")
    assert stable_id(first,"smy")!=stable_id(first,"kol")


def test_cli_token_aggregations_are_explicitly_computed():
    entry={"tokenName":"BNB","ca":"0xab","addressList":[{"inflow":"1.25"},{"inflow":"-0.4"}]}
    out=summarized_poll({"data":[entry]})
    assert out["row_count"]==1
    assert out["sample"][0]["consensusCount"]==2
    assert float(out["sample"][0]["netInflow"])==pytest.approx(0.85)


def test_evidence_reader_is_fresh_and_valid(tmp_path):
    store=WalletTrackerStore(tmp_path)
    store.save_poll({"56-smy-token":{"row_count":1,"sample":[{"tokenName":"BNB","ca":"0xabc"}]}},now=NOW)
    store.ingest([{"signalId":"x","chainId":"56","ticker":"BNB"}],kind="smy",now=NOW)
    rows=read_wallet_candidates(tmp_path,NOW)
    assert len(rows)==2
    assert all(EvidenceCandidate.model_validate(r) for r in rows)
    assert read_wallet_candidates(tmp_path,NOW+timedelta(hours=2))==[]


def test_collector_failure_is_isolated(monkeypatch,tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location("wallet_worker",Path("scripts/run_binance_wallet_tracker.py"))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Dummy:
        def token_query(self,chain,tag):
            if tag=="kol":raise TrackerCliFailure("broken")
            return {"data":[{"tokenName":"BNB","ca":"0xabc"}]}
        def trade_query(self,chain,tag):
            return [{"txHash":"0x1","address":"0xa","ca":"0xc"}]
    report=module.collect(cli=Dummy(),store=WalletTrackerStore(tmp_path),now=NOW)
    assert len(report["sources"])==3
    assert len(report["failures"])==1
    assert report["publication_authority"] is False


def test_explorer_wallet_reader_only_when_active(tmp_path):
    async def rss(feeds,now):
        return {"observed_at":now.isoformat(),"items":[],"failures":[]}
    store=WalletTrackerStore(tmp_path)
    store.save_poll({"56-smy-token":{"row_count":1,"sample":[{"tokenName":"BNB"}]}},now=NOW)
    explorer=GoalEvidenceExplorer(state_root=tmp_path,feeds=(),
                                  rss_fetcher=rss,
                                  wallet_reader=lambda now: read_wallet_candidates(tmp_path,now))
    assert not asyncio.run(explorer.collect(now=NOW,active=False))["items"]
    assert len(asyncio.run(explorer.collect(now=NOW,active=True))["items"])==1


def test_continuous_stream_requires_supported_baw_version(monkeypatch):
    from wechat_traffic_agent.sources.binance_wallet_cli import minimum_ws_version
    monkeypatch.setattr("shutil.which", lambda _: "/opt/bin/baw")
    cli = BawTrackerCli()
    def version(result):
        return lambda *args, **kw: SimpleNamespace(
            returncode=0, stdout=result.encode())
    assert minimum_ws_version(cli, executor=version("baw v1.10.0")) == (1,10,0)
    with pytest.raises(TrackerCliFailure):
        minimum_ws_version(cli, executor=version("baw 1.9.0"))
    assert ws_args(mode="smy",duration=0,continuous=True)[-2:] == ["--duration","0"]


def test_continuous_stream_uses_cli_native_reconnect_and_updates(monkeypatch,tmp_path):
    from wechat_traffic_agent.sources.binance_wallet_cli import stream_ws
    monkeypatch.setattr("shutil.which",lambda _: "/opt/bin/baw")
    cmd=[]
    class Proc:
        returncode=0
        stdout=[json.dumps({"signalId":"X","chainId":"56","alertPrice":"1"}).encode(),
                json.dumps({"signalId":"X","chainId":"56","currentPrice":"2"}).encode()]
        def poll(self):return 0
    def popen(argv,**kwargs):
        cmd.extend(argv)
        return Proc()
    result=stream_ws(BawTrackerCli(),WalletTrackerStore(tmp_path),
                     popen=popen,version_executor=lambda *a,**kw:SimpleNamespace(
                         returncode=0,stdout=b"1.10.0"),now_fn=lambda:NOW)
    assert result["new"]==1 and result["updated"]==1
    assert cmd==["baw","tracker","ws","--smy","--duration","0","--json"]
    assert len(WalletTrackerStore(tmp_path).latest_events())==1
