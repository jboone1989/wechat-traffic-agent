import pytest

from wechat_traffic_agent.platforms.binance_square_editorial import validate_mining_tags


def test_relevant_native_cashtags_are_accepted():
    validate_mining_tags("比较 $ETH 与 $BTC 的同窗口表现。$ETH", ["BTC", "ETH"])


@pytest.mark.parametrize("body,symbols", [
    ("ETH BTC分析", ["BTC", "ETH"]),
    ("$ETH 分析", ["BTC", "ETH"]),
    ("$ETH $BTC $DOGE", ["BTC", "ETH"]),
    ("$BTCUSDT", ["BTC"]),
    ("$BTC $ETH $DOGE $BNB", ["BTC", "ETH", "DOGE", "BNB"]),
])
def test_metadata_and_unrelated_or_excess_tags_are_rejected(body, symbols):
    with pytest.raises(ValueError, match="content mining"):
        validate_mining_tags(body, symbols)
