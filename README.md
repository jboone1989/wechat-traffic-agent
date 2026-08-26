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
  健康与 active_backend；正文读取用其管理的 `curl -s https://r.jina.ai/{url}`；
  评论/作者协议已定义，等 PR3 接好平台 CLI 后端再解析。
- **FixtureProvider**：离线三平台数据 + 故障注入，CI 与单元测试专用。
