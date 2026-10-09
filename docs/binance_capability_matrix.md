# Binance Skills Hub capability inventory — Ferro

> Audited on 2026-10-09 against official `binance/binance-skills-hub` commit [`9960c675387b`](https://github.com/binance/binance-skills-hub/tree/9960c675387bd27f8866645b83693c1fa87242f6/skills). This is a **repository-source inventory, not live API verification**. All statuses below are derived from repository `SKILL.md` and related source/scripts. It is NOT an inventory of every repository in the Binance GitHub organization.

## Scope and decisions

- Official tree: **20 directories with SKILL.md**: 7 under `skills/binance`, 13 under `skills/binance-web3`. No missing skills in these two directories in this snapshot.
- P0 = existing Square publishing integration; P1 = read-only evidence sources worth *testing* later; P2 = opportunistic subject-specific read-only sources; P3 = no integration into Ferro's research/publication goal.
- Skill packaging is not proof of a stable publicly supported third-party API; `bapi` HTTP interfaces and CLI internals require separate live test, authentication checks and maintenance assessment.
- The repository's Square skill explicitly says it **only creates new posts**, not reading, searching, commenting, liking, editing, deleting, scheduling or managing existing posts. Do not design a Square feedback collector on presumed interfaces.
- Binance trading credentials are NOT needed to publish Square and should not be reused. Do not automatically execute financial actions.

## Complete skill inventory

| Group | Skill | Verified capability from Skill source | Ferro relevance | Priority | Access / caveat |
|---|---|---|---|---|---|
| binance | [`academy-skill`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/academy-skill/SKILL.md) | Academy glossary / articles / courses / Learn & Earn search | 检索知识背景、术语、风险教育资料 | P2 | 公开检索;按 Academy 接口限制；教育内容，不是原始研究证据 |
| binance | [`binance`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/binance/SKILL.md) | binance-cli: Spot / USDⓈ-M Futures / Convert; extensive exchange reference set | 行情、市场数据或合约机制核验（只读调用） | P1 | CLI;文档写 Requires auth，具体匿名市场能力需单独验证；不安装交易权限作为采集前提 |
| binance | [`fiat`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/fiat/SKILL.md) | 法币买卖、支付方式、国家支持、报价；订单历史 | 稳定币/法币渠道专题研究 | P2 | 公开报价/支付方式；订单历史需认证；区域和时间高度相关 |
| binance | [`onchain-pay`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/onchain-pay/SKILL.md) | 法币购买并链上发送、报价、预订单和订单查询 | 无当前需求 | P3 | RSA 签名及支付授权；涉及购买/转账，不接入 |
| binance | [`p2p`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/p2p/SKILL.md) | P2P 广告价格/商家筛选、订单和申诉/广告管理 | P2P 市场结构专题时才考虑公开报价 | P3 | 公开广告查询；私有订单/广告动作需认证；不接私有交易管理 |
| binance | [`payment`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/payment/SKILL.md) | Binance Pay 收付款链接、二维码和订单动作 | 无当前需求 | P3 | 付款授权；不接入 |
| binance | [`square-post`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/square-post/SKILL.md) | 文本、长文章（title）、1–4 图片、长文封面、视频与上传轮询 | Ferro 官网文章分发；发布回执 | P0 | Square 专用 posting key；Node.js；视频另需 ffmpeg/ffprobe；当前 Ferro **只实测文本**；官方脚本其他模式待集成与验证 |
| binance-web3 | [`binance-agentic-wallet`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-agentic-wallet/SKILL.md) | 钱包查看、交易、转账、DeFi、限价单、预测市场等 | 只需独立研究数据，不引入写资产能力 | P3 | baw CLI + 钱包认证；暂不接 |
| binance-web3 | [`binance-leaderboard`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-leaderboard/SKILL.md) | 链上钱包 PnL/胜率排行、地址评分、Gem Hunter | 研究行为样本与策略差异 | P1 | baw CLI；公开排行榜免认证；部分私有配置需认证；排行榜绩效可能有样本和幸存偏差 |
| binance-web3 | [`binance-onchain-copy-trader`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-onchain-copy-trader/SKILL.md) | 组合 tracker/信号/地址评分/审计/下单的跟单策略脚手架 | 不符合内容发布主目标 | P3 | baw CLI+多依赖；下单需钱包授权；官方说明它不提供有效策略保证 |
| binance-web3 | [`binance-sports-ai-analyzer`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-sports-ai-analyzer/SKILL.md) | 世界杯比赛 AI 概率、新闻分析、市场赔率比较 | 非 Ferro 主要选题 | P3 | Node CLI；预测市场交易需另外授权；暂不接 |
| binance-web3 | [`binance-tokenized-securities-info`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-tokenized-securities-info/SKILL.md) | Ondo 代币化美股列表、公司行动、链上价格、基本面和K线 | RWA/代币化股票的机制与定价研究 | P1 | 文档列公开 Web3 查询端点，使用前需 live smoke test；核对 token/share multiplier，不能直接当股票价格 |
| binance-web3 | [`binance-trading-signal`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-trading-signal/SKILL.md) | Smart Money 逐笔信号；策略管理、回测、Web3 CLI | 研究信号选择偏差和事后绩效 | P1 | 逐笔信号可直接公开 HTTP；管理/回测需 baw CLI / 会话；与 trading-signal 有重叠；避免两份采集 |
| binance-web3 | [`binance-wallet-tracker`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-wallet-tracker/SKILL.md) | Smart Money/KOL 买卖、代币聚合、钱包群组、WebSocket 事件 | 链上资金流研究问题与追踪 | P1 | baw CLI ≥1.9.1；公开 --tag-type 查询；私有 group 需 agentSessionId；文档提醒字段部分为CLI本地聚合，不能称 API 原始字段 |
| binance-web3 | [`crypto-market-rank`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/crypto-market-rank/SKILL.md) | social hype、token rank、smart-money inflow、meme rank、address PnL | 从排行中发现异常后再研究 | P1 | Node CLI，公开查询，需验证速率与响应；排名≠因果/预测 |
| binance-web3 | [`meme-rush`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/meme-rush/SKILL.md) | 新币生命周期 / launchpad 与 AI 热门叙事 feed | 仅事件发现，不直接产生成稿 | P2 | Node CLI，公开查询；高噪声、高投机风险 |
| binance-web3 | [`query-address-info`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/query-address-info/SKILL.md) | 指定地址的 token 持仓数量、价格、24h 变化 | 跟踪有依据的链上账户持仓 | P2 | 公开链上查询、地址+chainId；钱包余额快照不等于完整交易意图 |
| binance-web3 | [`query-token-audit`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/query-token-audit/SKILL.md) | 合约诈骗、蜜罐、税费和控制权风险扫描 | 代币调查中的风险交叉证据 | P1 | 公开 POST /wallet-direct/security/token/audit；工具审计不等于独立安全证明 |
| binance-web3 | [`query-token-info`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/query-token-info/SKILL.md) | 代币搜索、metadata、实时市值/流动性/持有人、OHLCV | 代币研究的具体数据证据 | P1 | 公开 Web3 查询，Node CLI；保留来源时间和链 ID |
| binance-web3 | [`trading-signal`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/trading-signal/SKILL.md) | 公共 Smart Money 逐笔买卖信号（BSC/Solana） | 与 binance-trading-signal 统一采集入口 | P1 | Node CLI，公开查询；与上项重叠，仅保留一个 provider |

## Source code inspection: Square (P0)

Checked `skills/binance/square-post/SKILL.md`, `scripts/lib.mjs`, `post-text.mjs`, `post-image.mjs`, `post-video.mjs` from the pinned commit:

| Mode | Official script | Input | API payload distinction | Ferro status |
|---|---|---|---|---|
| Text | `post-text.mjs` | `--text` | `contentType=1` | **Already verified live** (single post + ID receipt) |
| Article | `post-text.mjs` | `--text --title` | `contentType=2` | Official support confirmed; **not** Ferro live-verified |
| Short image | `post-image.mjs` | `--text --images`, 1–4 files | `contentType=1` + `imageList` | Not integrated |
| Article cover | `post-image.mjs` | `--text --title --cover` | `contentType=2` + uploaded `cover` | Not integrated |
| Video | `post-video.mjs` | `--video --duration` (text optional) | Presigned upload + processing, first-frame cover | Not integrated; `ffmpeg` needed |

Verified from [`lib.mjs`](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/square-post/scripts/lib.mjs):
- Base v1: `https://www.binance.com/bapi/composite/v1/public/pgc/openApi`. The script uses `/content/add`, `/image/presignedUrl` and `/image/imageStatus`, plus a v2 API base for some media operations. Upload uses a returned presigned URL; **do not mistake upload for a one-step public POST**.
- `content/add` returning HTTP 504 is treated by the *official CLI as “Success / ID unavailable”*. For Ferro's durable outbox, this must remain **UNVERIFIED / no automatic resubmit** because the server result cannot be independently confirmed from the ID. The current Ferro adapter follows this stricter receipt contract.
- The **existing Ferro consumer is independent**: `PublicationEvent -> SquareEventConsumer -> Square OpenAPI -> SUCCESS/FAILED durable receipt`. It currently formats reviewed website excerpts into a short text post. Don't claim article/image/video implemented merely because the upstream skill supports them.
- Keep Square independent of X, market collection, learning runtime and trading APIs. The publication consumer must not backfill earlier site publications without owner approval.

## Read-only research candidates: order of validation

1. **P1, low-dependency**: `query-token-info`, `query-token-audit`, `crypto-market-rank`, `trading-signal`, `binance-tokenized-securities-info`. Each provides concrete, source-bound observations; test the public documented endpoint or official Node CLI before integrating. All should be data sources, not research conclusions.
2. **P1, CLI dependent**: `binance-wallet-tracker` and `binance-leaderboard` via `baw`, and `binance-trading-signal` (note its direct Smart Money query overlaps with `trading-signal`). For tracker, public `--tag-type smy|kol` differs from private `--group-id`. Official `references/cli.md` says `consensusCount` and `netInflow` are locally aggregated from per-address results; record this provenance. WebSocket keepalive and reconnect require sufficiently recent CLI version; **do not add the CLI to production before testing compatibility and memory cost**.
3. **P2**: Academy, fiat quote sources, meme hot-topic feeds, wallet holdings for explicitly chosen addresses.
4. **Exclude** asset-writing wallet, copy trading, payment/onchain-pay and P2P private operations, sports/prediction market execution. Do not conflate knowledge from a skill with permission to trade.

## Integration outcome

**Now**: concentrate on Square creation coverage (article / media / video + durable receipts) while retaining the proven text publisher. No analytics, comments or sentiment feedback provider until official accessible API is verified.

**Later, only after source smoke tests**: add small read-only adapters feeding existing evidence/candidate mechanisms, one provider at a time. Do not introduce a common `BinanceEvent` union just for the name: publication receipts and research evidence have different semantics.

## Verification and refresh

- Directory inventory source: [tree snapshot](https://github.com/binance/binance-skills-hub/tree/9960c675387bd27f8866645b83693c1fa87242f6/skills)
- Number of Skill files observed: **20**. Entries in this matrix are **7+13**.
- Source snapshot commit: `9960c675387bd27f8866645b83693c1fa87242f6`.
- Future refresh: compare `skills/binance/*/SKILL.md` and `skills/binance-web3/*/SKILL.md` at a new upstream SHA, then review changed authentication, method, endpoint and data shape before updating classification.
- This is an **inventory-only change**, not a deployment or confirmation of API reachability.
