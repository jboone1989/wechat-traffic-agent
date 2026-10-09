from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from wechat_traffic_agent.sources.binance_market_structure import analyze

NOW = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


def market_report():
    now_ms = int(NOW.timestamp() * 1000)
    end = now_ms - 300000
    report = {"observed_at": NOW.isoformat(), "observations": [], "status": "OK"}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        oi = [{"timestamp": t, "sumOpenInterest": str(1000 + i),
               "sumOpenInterestValue": str((1000 + i) * 100)}
              for i, t in enumerate(range(end - 7200000, end + 1, 300000))]
        candles = [[t, "100", "110", "90", "100", "1", t + 299999,
                    "100", 1, "0.6", "60", "0"]
                   for t in range(end - 8400000, end + 600001, 300000)]
        # Open candle with enormous volume must never affect closed-window data.
        candles[-1][7] = "999999999999"
        for name, data in (
            ("spot_24h", {"lastPrice": "100", "priceChangePercent": "-2", "closeTime": now_ms}),
            ("mark_funding", {"lastFundingRate": "0.0001", "nextFundingTime": now_ms + 3600000, "time": now_ms}),
            ("open_interest_history", oi), ("futures_klines", candles),
        ):
            report["observations"].append({"symbol": symbol, "name": name, "status": "OK", "data": data})
    report["metrics"] = analyze(report)
    return report


def test_aligned_price_oi_volume_and_funding_units():
    metric = market_report()["metrics"]["BTCUSDT"]
    assert metric["perpetual_change_1h_pct"] == "0"
    assert Decimal(metric["oi_quantity_change_1h_pct"]) > 0
    assert Decimal(metric["quote_volume_1h_usdt"]) == 1200
    assert Decimal(metric["quote_volume_ratio"]) == 1
    assert Decimal(metric["taker_buy_quote_share_pct"]) == 60
    assert Decimal(metric["latest_funding_pct"]) == Decimal("0.01")


@pytest.mark.parametrize("failure", ["missing", "stale", "gap", "stale_common_window"])
def test_incomplete_or_misaligned_market_data_rejected(failure):
    report = deepcopy(market_report())
    rows = report["observations"]
    if failure == "missing":
        rows[0]["status"] = "UNAVAILABLE"
    elif failure == "stale":
        rows[1]["data"]["time"] -= 180000
    elif failure == "gap":
        rows[2]["data"].pop(-5)
    else:
        rows[6]["data"] = rows[6]["data"][:-3]
    with pytest.raises((ValueError, KeyError)):
        analyze(report)


def test_endpoint_update_skew_uses_latest_common_boundary():
    report = market_report()
    report["observations"][6]["data"].pop()
    metrics = analyze(report)
    assert metrics["BTCUSDT"]["window_end"] == metrics["ETHUSDT"]["window_end"]
    assert metrics["BTCUSDT"]["window_end"] == "2026-10-09T07:50:00+00:00"
