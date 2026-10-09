# Ferro Binance Square editorial quality reference

The user accepted the factual BTC/ETH example below on 2026-10-09
(Asia/Shanghai), then clarified that future posts must avoid template-like
content. The example calibrates quality only. It is not a house prose format.
Runtime preferences: `square_editorial/editorial_preferences.json`.

## Selection and composition

Start from an actual finding or reader-relevant question. Ask what changed,
why that change is worth understanding, and what the evidence supports. Choose
only the metrics needed to explain that finding. A post may focus on one coin;
neither a BTC/ETH comparison nor a full price/OI/volume/funding dashboard is
required in every body. Do not rotate predefined formats to simulate variety.

Let the discovery determine length, order and voice. A concise observation,
an event explanation, a relative-strength comparison, or an update correcting
an earlier judgment can each be appropriate. These are possibilities, not a
schedule or mandatory taxonomy. No fixed opening, title formula, paragraph
order, "my judgment" phrase or "watch these two things" ending is prescribed.
Internal reader-question/thesis/invalidation metadata records the editorial
assessment; it must not be printed as compulsory body sections. Descriptive
posts may record the limits of their observation in the invalidation field,
without inventing a forecast or padding the text with conditional predictions.

## Evidence and review

Verify sources, symbols, units, venue and sampling time. Align windows when
comparing price, base-asset OI and volume. Clearly distinguish observation
from inference. Do not infer net shorts, liquidations or directional new
positions from OI, funding signs or taker-buy share alone. Recheck every new
draft's numbers; the example's historical values must never be reused as live
data. The reviewed-copy binding and freshness checks still apply.

Before approving, compare the candidate with recent published drafts in
`square_editorial/drafts` (SUCCESS). Identify its new information, new relevant
question, or substantive revision of an earlier judgment. Updating numbers
under the same argument is insufficient. Cosmetic paraphrasing is insufficient.
If the evidence is flat or the argument repeats, skip publication rather than
fill a quota. Review the reasoning and reader value explicitly; numerical
validation and required fields alone do not establish semantic quality.

The accepted example has a confirmed publication receipt:
https://www.binance.com/square/post/375369749478470 . Acceptance of that post
does not automatically approve future drafts. The publishing worker consumes
reviewed drafts only. New production copy uses the existing Ferro inquiry,
memory and cognition pipeline through the BINANCE_SQUARE expression port;
there is no separate timed writer. The historical example remains a quality
reference, not proof that later outputs will have semantic novelty.

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
