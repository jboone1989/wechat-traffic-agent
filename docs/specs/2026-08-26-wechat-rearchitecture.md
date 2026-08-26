# WeChat Traffic Agent 重构说明（2026-08-26）

> 本文档定义三层边界与 PR1–PR7 实施顺序，是 `wechat-traffic-agent` 与后续
> `opportunity-agent` 重构的执行依据。每个 PR 单独分支、单独验收，禁止合并成巨型 commit。

## 总目标

当前涉及三个层次，禁止混淆：

```text
Agent Runtime
    │
    │ 通用身份 / 记忆 / Goal / Backend / Policy / Recovery
    ▼

Internet Capability
    │
    │ 只负责"看见互联网"
    │ search / hot / read / comments / profile
    │
    ├───────────────────────┐
    ▼                       ▼
Opportunity Agent      WeChat Traffic Agent
    │                       │
寻找蓝海                   寻找爆款
验证需求                   拆解爆款
验证商业机会               内容实验
学习机会模型               学习流量模型
```

严格遵守：

```text
Internet Capability 决定：
"我能看到什么？"

Opportunity Agent 决定：
"这是不是一个尚未满足的机会？"

WeChat Traffic Agent 决定：
"这是不是一个值得学习的爆款？为什么爆？我该试什么？"
```

不要把第二、第三层重新合并。

## OSS 策略

不要自己开发微博、小红书、X、Reddit、B站等爬虫。当前优先评估：

- **Agent-Reach**（v1.5.0，MIT）：作为深度读取/search/comments/profile 能力层，本身是
  internet capability router，已支持 Web、X、小红书、微博、B站、Reddit 等多平台与多后端
  健康检查。https://github.com/Panniantong/Agent-Reach
- **DailyHotApi**：便宜地批量获取各平台热榜。不复制源码进仓库，按独立服务/API 调用。
  它有缓存且部分来源依赖页面抓取，adapter 必须把它当 best-effort source，而不是可靠数据库。
  https://github.com/imsyy/DailyHotApi
- **NewsNow**（MIT）：保留为第二热榜 provider，第一版不同时接两个。
  https://github.com/ourongxing/newsnow

第一版：

```text
DailyHotApi   → 广撒网
Agent-Reach   → Top-N 深抓
不要自己写平台 scraper
```

## 实施顺序

| 阶段 | 目标                       | 仓库                   |
| -- | ------------------------ | -------------------- |
| P0 | 修正当前实验基础                 | wechat-traffic-agent |
| P1 | 接入通用互联网"眼睛"              | wechat-traffic-agent |
| P2 | 实现 Viral Radar           | wechat-traffic-agent |
| P3 | 实现爆款结构拆解                 | wechat-traffic-agent |
| P4 | 接入内容实验                   | wechat-traffic-agent |
| P5 | 建立反馈学习                   | wechat-traffic-agent |
| P6 | 重构 Opportunity 定位        | opportunity-agent    |
| P7 | 两个真实消费者出现后判断是否抽公共 bridge | 视情况                  |

（对应 PR1–PR7；PR1 与 P0 同，PR2 与 P1 同，依此类推。）

## PR1：先修当前 WeChat 实验基础

Branch `feat/wechat-experiment-ledger`，基线 `jboone1989/wechat-traffic-agent` main `774ece4`。
这一阶段不接任何互联网。

### 1.1 Experiment ID

输出改为：

```text
output/
  2026-08-26/
    exp-01.../
      experiment.json
      tietu.json
      tietu.md
      preview.html
```

每一次生成都有不可变字段：`experiment_id`、`created_at`、`audience`、`objective`、
`candidate_id`、`pattern_id`、`strategy`、`draft`、`source_refs`、`runtime_run_id`。
禁止覆盖历史 experiment。

### 1.2 Objective 必须真正生效

`ObjectivePolicy` 根据目标（READS / FOLLOWS / DISCUSSION / CONVERSION）决定：
composition guidance、primary metric、secondary metrics、reward calculation。
某指标拿不到时表示为 `UNKNOWN`，绝不能 `UNKNOWN → 0`——否则 learner 会把"没数据"误认为"表现很差"。

### 1.3 版本统一

Agent 自身版本统一（当前 agent.yaml 0.3.0 / pyproject.toml 0.1.0）。
`runtime_api >=0.3,<1.0` 单独表达 Runtime compatibility，不与 Agent 版本混用。

## PR2：建立极薄 Internet Port

Branch `feat/wechat-internet-sources`。

**不要设计"大一统互联网框架"**，只建立当前真正需要的 contracts：
`HotFeedProvider`、`ContentReader`、`CommentReader`、`AuthorReader`。

统一 domain-neutral DTO：`HotItem`、`ContentSnapshot`、`EngagementSnapshot`、
`AuthorSnapshot`。所有平台没有的数据必须允许 `None / unavailable`，不强迫统一。

第一版 Provider 只实现 `DailyHotApiProvider`、`AgentReachProvider`、`FixtureProvider`：
DailyHotApi → HotItem；Agent-Reach → ContentSnapshot/Comment/AuthorSnapshot；
FixtureProvider 用于 CI/unit tests。外部工具故障必须隔离（微博失败 ≠ 整个 scan 失败），
返回 `SourceResult { items, source, status, error? }`。

## PR3：Viral Radar

Branch `feat/wechat-viral-radar`。回答"最近什么内容正在异常成功？"，不是"什么领域是蓝海？"。

Scan pipeline：DailyHotApi → 数百 HotItem → normalize → dedup → platform-local ranking →
Top 20 → Agent-Reach deep fetch → content + author + engagement → `ViralCandidate`。

第一版优先：微博、知乎、B站、抖音/头条中可稳定拿到的来源；小红书等通过 Agent-Reach 补充；
Reddit/X 可用于结构参考但不是 P1 强依赖。

`ViralCandidate`：candidate_id、platform、content_id、url、title、text_excerpt、rank、
published_at、observed_at、author、author_followers?、views?、likes?、comments?、shares?、
saves?、absolute_hot_score、breakout_score?、freshness_score、cross_platform_score、
final_score、evidence_refs。

**排名不让 LLM 做。** 数字比较全部 deterministic。不同平台 raw 指标不能直接横比，先做
platform-local percentile（rank / engagement / breakout / freshness），再合成 viral_score，
初始权重 rank 35% / engagement 25% / breakout 25% / freshness 15%；某项不可用则重新归一化
剩余权重，不把 unavailable 当 0。

Breakout 不是"谁最火"，而是"谁的表现远超账号自身分发能力"。absolute popularity 与
relative breakout 必须分开保存。

## PR4：Viral Pattern Analyzer

Branch `feat/wechat-viral-pattern-analysis`。只有 Top 5～10 ViralCandidate 才允许进入 LLM
深度分析（避免 300 条帖子 × LLM）。

`ViralPattern`：candidate_id、target_audience、scene、core_desire、pain_or_tension、
title_pattern、opening_hook、curiosity_gap、first_screen_promise、emotional_trigger、
information_delta、conflict_structure、pacing、payoff、interaction_trigger、
comment_demands、save_reason、share_reason、reusable_elements、platform_specific_elements、
observed_facts、hypotheses、confidence、source_refs。

强制要求：`observed_facts` 与 `hypotheses` 分开（"评论区大量出现'说的就是我'"是观察；
"该内容可能因为身份认同而促进分享"是假设）。不允许写成"它爆火就是因为身份认同"——没有因果证据。

拆解重点：谁会点、为什么点、标题承诺什么、第一句制造什么张力、为什么继续读、为什么收藏/
评论/分享、哪些结构可跨平台迁移、哪些只是平台机制。

必须增加 near-copy guard（标题高度相似 / 连续长片段复用 / 明显句式照搬）。学习 pattern ≠ 改几个字复制原文。

## PR5：Experiment Planner

Branch `feat/wechat-content-experiments`。新主链路：
ViralRadar → ViralCandidate → ViralPatternAnalyzer → ExperimentPlanner → TietuDraft → manual publish。

保留 `--topics topics.json` 作为 manual override / fixture mode。CLI：
`wechat-tietu-agent scan / analyze / create`，另有 `run` 串联三步。

`ExperimentStrategy`：pattern_id、variable_under_test、baseline_value、treatment_value。
首批实验变量：title_pattern / opening_hook / emotion / information_delta / interaction_hook。
**每篇只主动探索一个主要变量**——同时改五个变量会让系统不知道阅读翻倍是因为什么。
这比一开始上复杂 bandit 更重要。

## PR6：Feedback Learner 2.0

Branch `feat/wechat-traffic-learning`。`TrafficLearner → EmotionAngle` 太窄，改为
`StrategyLearner`，学习 audience / objective / title pattern / hook pattern / emotion /
information style / interaction style / viral pattern family。

`PerformanceObservation`：experiment_id、post_id、published_at、observation_window、
reads?/impressions?/shares?/comments?/follows?/conversions?、account_baseline、objective。
允许 24h/48h/7d 数据窗口，禁止比较"发布 2 小时 vs 发布 7 天"。

Reward 不只靠绝对数字：账号增长后 1000 阅读含义不同。至少加入 trailing baseline
（如最近 10 篇中位数），learner 主要学 relative performance 而不是 raw reads。

Context isolation：按 audience_key + objective 分 context，杜绝跨人群污染
（银发亲情的 nostalgia 成功不能自动推导程序员职场也该 nostalgia）。

学习策略先简单：minimum samples + smoothed historical lift + exploration。
积累几十上百个真实 experiment 后再讨论 contextual bandit / Thompson Sampling。

## PR7：最后才处理 Opportunity Agent

等 WeChat 跑通真实 scan → candidate → pattern → content → 手工发布 → feedback 闭环后再动
`opportunity-agent`。其新定位为 **Blue-Ocean Discovery & Validation Agent**（不再是
Content Intelligence Provider，当前 README 的后者定位应更新）。新链路：
Internet Capability → 用户问题/评论/搜索/社区 → BlueOceanSearchPolicy →
UnmetDemandCandidate → OpportunityHypothesis → ValidationExperiment → Evidence →
SUPPORTED / INCONCLUSIVE / REJECTED。

允许复用同一个 Agent-Reach，但 `ViralScore` 绝不能进入 Opportunity，`UnmetDemandScore`
绝不能进入 WeChat。

## 什么时候抽公共组件

**现在不创建 `internet-agent`、`content-intelligence-platform` 之类的新仓库。**
先让 wechat-traffic-agent 真实使用 Agent-Reach / DailyHotApi，之后 Opportunity 也开始真实
使用；若两个仓库各自重复维护 AgentReachClient、DailyHotClient、ContentSnapshot DTO、
source health check、retry 且重复明显，再抽 `reach-bridge` 或轻量 SDK。
判断标准是"已经有两个以上真实消费者正在重复维护相同代码"，不是"理论上大家都能用"。

## 最终目标架构

```text
                         INTERNET
                            │
                 ┌──────────┴──────────┐
                 │                     │
            DailyHotApi           Agent-Reach
                 │                     │
            cheap hot scan       search/read/comments
                 └──────────┬──────────┘
                            │
                     Raw Internet Data
                            │
            ┌───────────────┴───────────────┐
            │                               │
            ▼                               ▼
     Opportunity Agent              WeChat Traffic Agent
            │                               │
   BlueOceanSearchPolicy             ViralSearchPolicy
            │                               │
   UnmetDemandCandidate              ViralCandidate
            │                               │
 OpportunityHypothesis               ViralPattern
            │                               │
 ValidationExperiment              ContentExperiment
            │                               │
       Evidence                     TrafficFeedback
            │                               │
   Learn opportunities              Learn virality
```

Agent Runtime 继续在两者下面提供生命周期基础设施，不承担上述 domain intelligence。

## 工程约束（写入任务验收）

| 约束              | 要求                                                |
| --------------- | ------------------------------------------------- |
| 不重复造轮子          | 平台访问优先复用 OSS                                      |
| 不自动发布           | 第一阶段保持 human-in-the-loop                          |
| 不复制 Runtime     | identity/memory/backend/policy 继续使用 Agent Runtime |
| 不依赖 Opportunity | WeChat 关键运行链路不经过 Opportunity Agent                |
| 不做蓝海分析          | WeChat 只研究已验证爆款                                   |
| 不让 LLM 排数字      | 热度、breakout、freshness 用 deterministic code        |
| 不制造因果           | ViralAnalyzer 区分 observations / hypotheses        |
| UNKNOWN ≠ 0     | 缺失指标保持 unavailable                                |
| 不覆盖实验           | experiment immutable                              |
| 不跨 context 污染   | audience + objective 隔离学习                         |
| 不同时改大量变量        | ExperimentPlanner 尽量单变量探索                         |
| 不写平台 scraper    | OSS adapter 优先                                    |
| 不做大抽象           | 真实有第二消费者后才抽公共模块                                   |

## 每个 PR 的验收标准

1. **PR1 Experiment Ledger**：同一天连续运行 3 次产生 3 个 experiment；旧 9 项测试继续通过；
   不同 objective 得到不同 reward policy；版本一致。
2. **PR2 Internet Sources**：fixture 能模拟至少 3 平台；单个平台失败不会中断；所有结果保留
   provenance；无网络单元测试全通过。
3. **PR3 Viral Radar**：输入至少 100 个 fixture hot items 后稳定输出 Top-N；低粉爆款能超过
   纯大号普通内容；不同平台 raw engagement 不直接横比；UNKNOWN 指标不被当 0。
4. **PR4 Viral Analyzer**：Top candidate 输出完整 ViralPattern；所有判断有 evidence refs；
   observed/hypothesis 分离；禁止 unsupported source；增加 near-copy 测试。
5. **PR5 Experiment Planner**：ViralPattern → TietuDraft → immutable Experiment 完整跑通；
   一次明确记录 variable_under_test；继续保持没有 publish actuator。
6. **PR6 Feedback Learning**：experiment → observation → strategy estimate → next
   recommendation 闭环完成；不同 audience 不互相污染；不同 objective 不共享错误 reward。
7. **Opportunity 重构**：等 WeChat 有真实数据后另开任务，不与上述 PR 混在一起。

完成到 PR4 时，项目从"AI 公众号文案生成器"升级成"会主动寻找市场已验证爆款并理解其结构的
Agent"；完成到 PR6 时才真正可以叫 **WeChat Traffic Agent**（Observe → Analyze →
Experiment → Measure → Learn）。

## 执行顺序结论

先执行 PR1 → PR2 → PR3。不先碰 Opportunity、不做自动发布、不创建新的公共互联网仓库。
三步完成后检视真实采集结果，再决定后续。
