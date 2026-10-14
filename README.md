# 微信公众号题图内容 Agent

第一阶段只负责内容，不登录公众号、不上传图片、不保存草稿、不发表。

它由 Agent Runtime 托管，复用 Runtime 的身份、记忆、目标、上下文投影和
`AgentBackend`。业务包只保留四件事：

```text
具体人群/场景/需求
  → 有来源的市场样本（优先观察低粉高互动）
  → Runtime advisory backend 生成严格题图 JSON
  → 本地内容包交给运营者手动发布
```

内容结构参考[这篇题图分析](https://x.com/WolfMoneyNotes/status/2077624635105980561)：
一个痛点、一个观点、3～5 句有信息增量的短文、一个自然互动问题。Agent 学习
选题、标题和情绪结构，不照搬原文，也不靠恐慌、孤独或强迫互动收割流量。

公众号后台的真实入口是 `type=10&createType=8` 的题图编辑器，其中“文字海报”
可以把文案生成题图。第一阶段输出：

- 标题：不超过 20 字；
- 文字海报正文：3～5 句；
- 描述/互动句；
- 本轮受众、场景、痛点、观点和样本来源记录。

## 运行

本项目是 `agent-runtime` 的独立消费者，不复制 Runtime 的身份、记忆、目标、上下文、
模型适配器或执行策略。开发时可把两个仓库并列放置，然后分别安装：

```text
pip install -e ../agent-runtime
pip install -e . --no-deps
wechat-tietu-agent --audience examples/audience.json --topics examples/topics.json
```

正式安装时，本项目依赖 `agent-runtime>=0.3,<1.0`；Runtime 发布对应版本后可直接执行
`pip install -e .`。

命令使用 Agent Runtime 已有的模型配置，默认从环境读取 DeepSeek 兼容配置，并
通过 `LegacyProviderBackend` 进入 Runtime 的只读 advisory 边界。模型只返回候选
内容，不能执行发布动作。

输出目录（每次运行产生一个不可变实验槽位，同日多次运行互不覆盖）：

```text
output/YYYY-MM-DD/exp-NN/
  experiment.json  # 实验记录：experiment_id/created_at/audience/objective/
                   # candidate_id/pattern_id/strategy/draft/source_refs/runtime_run_id
  tietu.md         # 直接复制与手动发布说明
  tietu.json       # 题图文案结构化记录
  preview.html     # 本地文字预览
```

示例 `topics.json` 使用用户指定的方法帖作为首轮市场样本。后续应持续替换或补充
真实样本；没有来源的话题不能被 Agent 声称为热点。

## 反馈学习

手动把阅读、分享、关注、评论数据整理成 `PerformanceObservation` 后，在下一次
运行时通过 `--history history.json` 传入。学习器先探索不同情绪角度，再使用平滑
指标调整方向，避免把一次低样本偶然爆发当成稳定规律。

每次实验的目标（`audience.json` 里的 `objective`：reads/follows/discussion/conversion）
通过 `ObjectivePolicy` 决定组合指导、主次指标与奖励计算。回填 `PerformanceObservation`
时拿不到的指标留 `null`——系统把缺失视为"未知"而不是 0，避免学习器把
"没数据"误判成"表现差"。

## 互联网来源（PR2）

平台访问复用 OSS，不自建 scraper。所有来源通过 `SourceResult` 隔离故障：
单个平台失败不会中断扫描；平台没有的数据一律 `None`（unavailable），不强迫统一。

- **DailyHotApi**（广撒网热榜，best-effort）：自托管 `docker run -p 6688:6688 imsyy/dailyhotapi`，
  适配器走 `GET /{platform}`（weibo/zhihu/bilibili/douyin/toutiao 等），防御式解析。
- **Agent-Reach**（Top-N 深抓，CLI 能力层）：`agent-reach doctor --json` 报各 channel
  健康与 active_backend；正文读取用其管理的 `curl -s https://r.jina.ai/{url}`（或注入
  共享 httpx client 走同一读取端点）；bilibili 有文档化 CLI 模板
  （`bili video {id} --comments --json` / `bili user {id} --json`，按 `{ok, data, error}`
  envelope 防御解析）；其余平台输出形状未文档化，按需注入 `commands` 模板。
- **FixtureProvider**：离线三平台数据 + 故障注入 + 深抓场景，CI 与单元测试专用。

## 真实数据面（PR7）

`--source` 显式选择数据平面，live 模式**绝不静默回退 fixture**：

```bash
wechat-tietu-agent scan --source live --dailyhot-url http://localhost:6688
wechat-tietu-agent run  --source fixture            # 离线全链（默认行为不变）
```

live 栈 = DailyHotApi 热榜 + `ReaderRouter` 深抓路由：**bilibili 平台完整**
（`bili video {id} --json` 元数据补齐 author_id / published_at / engagement，
于是 breakout / engagement / freshness 三项子分第一次在真实数据上出现），
其余平台走通用 Jina 正文读取，缺能力按 `unavailable` 暴露并由评分剔除权重。
`--dailyhot-url` 与 `--source fixture` 同时给出会直接报冲突。

## 病毒雷达（PR3）

`ViralRadar.scan()` 回答"最近什么内容正在异常成功"：

```text
DailyHotApi/fixture 广撒网 → normalize + dedup → platform-local rank → Top-20
  → deep fetch（正文 + 作者 + 互动）→ ViralCandidate 排序
```

数字排序全部 deterministic code，LLM 不碰任何排名。子分只在同平台内做百分位：
rank 35% / engagement 25% / breakout 25% / freshness 15%；某项缺失则剔除权重并
重新归一化剩余项——unavailable 绝不当作 0。breakout = engagement / author_followers，
"低粉爆款能赢大号普通内容"；absolute_hot_score 单独保存原始热度百分位，
不与 breakout 混淆；同题出现在多个平台会获得 cross_platform_score。

## 爆款结构拆解（PR4）

`ViralPatternAnalyzer` 只把雷达 Top-K（默认 5，上限 10）候选送入 LLM 深度分析，
绝不"几百条帖子 × LLM"。分析器确定性收集证据（全文、指标、热门评论），后端输出
`ViralPattern`（受众/场景/核心欲望/标题模式/钩子/信息增量/互动触发/收藏与分享理由等）。

三条硬性纪律：

- **观察与假设分离**：`observed_facts` 只能写证据里可直接指认的现象；
  `hypotheses` 用「可能/倾向于」措辞。没有因果证据不得断言因果。
- **判断必须引用证据**：每条观察/假设都要带 `evidence_refs`，且只能引用
  分析器实际提供的编号——禁止编造来源；评论抓取失败就是不可用，不是没有评论。
- **近复刻守卫**：确定性检测标题高度相似、正文连续片段照搬、句式复制
  （`near_copy.py`），学习 pattern ≠ 改几个字复制原文。

## 内容实验（PR5）

新主链路（旧 `--topics` 手动模式继续可用，作为 override / fixture mode）：

```text
wechat-tietu-agent run     # scan → analyze → create 一趟跑完
wechat-tietu-agent scan    # 热榜广撒网 → output/scans/scan-*.json
wechat-tietu-agent analyze --scan <scan.json>      # Top-K → patterns-*.json
wechat-tietu-agent create --patterns <p.json> --audience a.json
```

`ExperimentStrategy` 明确记录每次实验唯一被试变量（pattern_id /
variable_under_test / baseline_value / treatment_value）。首批五个实验变量：
title_pattern、opening_hook、emotion、information_delta、interaction_hook；
按槽位轮转保证覆盖，**一篇只测一个变量**——同时改五个变量就永远不知道阅读
翻倍是因为什么。emotion 是被试变量时允许草稿采用不同情绪角度，其余情况
角度钉死为学习者推荐值；每篇草稿都过近复刻守卫与不可变 Experiment 存档。
依然没有任何自动发布执行器。

## 反馈学习 2.0（PR6）

`StrategyLearner` 取代只学情绪角度的 `TrafficLearner`（后者仍负责写作角度的
探索/利用规则），学习维度覆盖 title pattern / hook pattern / emotion /
information style / interaction style：

- **窗口纪律**：`PerformanceObservation.observation_window` 只允许 24h/48h/7d，
  不同窗口的数据永不放进同一个统计口径。
- **Context 隔离**：统计以 (audience_key, objective, window) 为单位——银发人群的
  nostalgia 胜利不会推导到程序员职场场景；discussion 实验不与 reads 实验共享奖励；
  未提供 audience_key 时禁止跨人群历史推断，直接回退轮转。
- **相对表现**：reward 对比 trailing baseline（同一 context 最近 N 次的中位数），
  学习器学 lift 而不是 raw reads；缺失主指标保持 UNKNOWN（不计分），绝不折算为 0。
- **策略保持简单**：最小样本量内先探索（VARIABLES 序），样本充足后按平滑后的
  historical lift 选择下一个被试变量；bandit / Thompson Sampling 等真实数据积累
  后再讨论。

闭环：experiment 存档 → 手工回填带 `experiment_id` / `variable_under_test` 的
observations → `--history history.json --audience-key silver` 传入 → 学习器影响
下一次变量选择，选择理由写进当期 `experiment.json` 可审计。

## Public news source: AIHot

The website collects public RSS feeds, deduplicates articles and publishes editorially
selected events. Ferro should consume that single normalized news source instead of
maintaining a second set of subscriptions:

```sh
CONTENT_NEWS_AGGREGATOR_URL=https://YOUR-AIHOT-ORIGIN
CONTENT_NEWS_FEEDS=
```

With `CONTENT_NEWS_AGGREGATOR_URL` set, the continuous content loop reads
`/api/v1/items?mode=selected&window=7d&by=published` with bounded pagination.
Source names and original publisher URLs are retained; the API's summaries are
discovery leads, not independent proof. If the site is temporarily unavailable, the
reader records an explicit failure and does **not** silently fall back to scraping RSS.
Ferro can still research a question by following original sources or using web
search; the website replaces *routine news subscriptions*, not primary-evidence
research. Do not enable this setting on production Ferro until the AIHot endpoint
returns recent, valid selected items.
