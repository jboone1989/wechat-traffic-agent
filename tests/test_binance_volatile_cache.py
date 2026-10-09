import json
from datetime import datetime, timezone
from wechat_traffic_agent.sources.live_cache import live_cache_root
from wechat_traffic_agent.sources.binance_market_structure import save_market_structure
from wechat_traffic_agent.sources.binance_web3_public import save_binance_snapshot
from wechat_traffic_agent.sources.binance_wallet_store import WalletTrackerStore


def test_live_snapshots_overwrite_without_durable_archive(tmp_path):
    for i in range(2):
        report = {"observed_at": datetime.now(timezone.utc).isoformat(), "value": i}
        path = save_market_structure(tmp_path, report)
        save_binance_snapshot(tmp_path, report)
    assert path.is_relative_to("/dev/shm")
    assert json.loads(path.read_text())["value"] == 1
    assert len(list(path.parent.glob("*.json"))) == 1
    assert not (tmp_path / "sources").exists()


def test_wallet_observations_are_volatile(tmp_path):
    store = WalletTrackerStore(tmp_path)
    store.ingest([{"signalId": "x", "ticker": "BTC"}], kind="smy",
                 now=datetime.now(timezone.utc))
    assert store.db.is_relative_to(live_cache_root(tmp_path))
    assert not (tmp_path / "sources").exists()
    assert len(store.latest_events()) == 1
