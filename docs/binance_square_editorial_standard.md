# Ferro Binance Square accepted editorial standard

The user accepted the following Chinese BTC/ETH market and perpetual-structure
draft on 2026-10-09 (Asia/Shanghai). Use it as an editorial calibration example.
Runtime preference record: `square_editorial/editorial_preferences.json`.

Write a native Chinese post with a specific reader question, concrete evidence,
a clear interpretation and observations that would change the interpretation.
Compare price, base-asset OI and volume over aligned completed windows. Explain
the venue and sampling time. Clearly distinguish facts from interpretation.
Do not infer net shorts, liquidations or directional new positions from OI,
funding signs or taker-buy share alone. Avoid website-summary syndication,
engineering logs, click-through dependencies and posts written to fill a quota.

Style acceptance is calibration, not a reusable approval of market numbers.
Every new draft needs fresh evidence and an actual source-to-claim review.
The reference remains a draft; this preference update does not publish it or
approve any future draft automatically.

## Accepted example (historical snapshot, not current market data)

ETH 反弹有成交，持仓却没跟上：这波还缺一次确认

截至 10 月 9 日 16:06（北京时间），ETH 现货滚动 24 小时跌 2.44%，BTC 跌 0.39%。ETH 更弱，但“跌得多”并不自动等于“空头更拥挤”。

把镜头缩到 15:00—16:00，同看币安 USDT 永续：

• ETH：价格涨 0.19%，未平仓量（按 ETH 数量）降 0.19%；成交额比前一小时增加 21.1%，主动买入成交额占比 42.7%。
• BTC：价格涨 0.10%，未平仓量增 0.03%。

我的判断：ETH 这次反弹有成交放大，却没有持仓扩张配合，还不足以认定新增多头正在持续接力。回补可能参与其中，但这些汇总数据无法确认平仓方向，更不能直接写成“空头爆仓推动上涨”。

接口最新单期资金费率：BTC +0.00504%，ETH −0.00143%，均非年化。ETH 的负号也不能单独证明空头拥挤。

接下来我看两件事：ETH 能否连续抬高完整小时的收盘价，以及同一窗口的持仓量能否随之增加。两者出现后，才有理由上调对反弹持续性的评价；如果只有价格回升、持仓继续收缩，我仍把它视作待确认的修复。

范围仅限币安这两个永续合约和上述小时窗口，不代表全市场仓位。价格与持仓方向是线索，不是涨跌保证。

数据：币安现货 ticker/24hr、永续 openInterestHist、klines、premiumIndex；16:06 采样。本文为市场观察，不构成投资建议。

$BTC $ETH
