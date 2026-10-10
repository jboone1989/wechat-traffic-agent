# Ferro 个性分化加速设计（两期）

- 日期：2026-10-11
- 状态：已与用户对齐，待实现
- 范围：wechat-traffic-agent（一期全部 + 二期大部分）；VirtualBrain 核心 schema 改动仅作二期条件触发项

## 背景与问题诊断

用户目标：让 Ferro 更快长出分化的个性（四个维度全要：兴趣偏好、观点立场、表达风格、整体人格连续感）。用户已确认：可降发帖产量换个性；可动 VirtualBrain 核心。

当前机制导致分化慢的四个原因（2026-10-11 实测）：

1. **Salience 棘轮**：`virtualbrain/research/interests.py` 中 `salience = max(旧值, novelty)`，只涨不跌。看过一次的话题全部饱和到 1.0，无法区分"着迷"与"路过"。
2. **Preference 信号饥饿**：内容/受众路径设计上给中性 reward=0.5（"注意力不是成败"），preference 不动；只有研究进展（`observe_learning_interest`）能动 preference，信号源窄、触发少。
3. **中性回归**：更新公式 `preference += lr * ((reward+1-effort)/2 - preference)`，缺省信号下大量话题锚死在 0.5（实测挤在 0.50±0.02）。
4. **注意力摊薄**：286 个可用话题、扁平先验，注意力均匀铺开，无复利效应。

反向证据：2026-10-10 模板帖事件——快速发帖路径绕过 mind 注入时帖子立刻模板化，注入恢复后出现自我信念连接。mind 在与不在，产出形态判然有别。今天（10-10 生产日志）还观察到立场问责行为自发出现：15:39 帖留下可证伪条件，15:58 主动 WAIT 等数据，16:04 结算后专程核验，rationale 自述"对已发布判断的问责性兑现，非换数重复"。本设计把这类自发行为制度化。

## 总体架构

方案 C：两层并行，分两期。

- **第一期（本周）**：叙事账本层（立场账本 + 自传体块 + 选择策略）全部落地，加最小信号改动（salience 衰减视图 + 学习率提升）。全部在 wechat-traffic-agent，不动 agent-runtime、不动 VirtualBrain 核心。
- **第二期（一期观察一周后）**：信号源扩容、风格自蒸馏；核心 schema v2 仅在指标显示视图层不够时触发。

数据流：

```
观察周期 (X / Square / 研究)
        │
        ▼
content_mind adapter
  ① 兴趣视图：salience 衰减视图 + lr 0.2（视图层计算，核心原始状态不动）
  ② 立场账本 positions.json：OPEN → CONFIRMED/REFUTED/ABANDONED
  ③ 自传体块：由 ①②+近期自家帖机械拼装
        ▼
mind payload（interests/beliefs/positions/自传体）
        ▼
选择策略：有可核验 OPEN position → 核验优先；
否则按衰减 salience 加权选话题（ε 探索保底）
        ▼
发帖 → 可证伪主张登记为新 position
        ▼
证据周期 → position 状态转移 → research-reward 回写 preference
```

关键决定：**salience 衰减做在 adapter 视图层，不改核心持久状态**。理由：核心 `observe()` 写入的 checkpoint 中 47+ 话题已饱和；改核心需动 provenance 契约、replay 验收与存量迁移；而选择与 payload 均只消费视图，原始饱和值不再进入任何决策路径。视图方案可逆、无迁移、当天可部署。

## 第一期组件

### ① 兴趣视图（衰减 + 学习率）

- adapter 侧为每个 topic 记 `last_touch`（该话题被注意/研究的时刻，墙钟时间）。
- 衰减视图：`view_salience = raw_salience × 0.5^(Δhours/48)`，半衰期 48h，不依赖 tick 节奏。参数可调。
- 学习率 0.1 → 0.2（在 `observe_learning_interest` 反馈调用处传参）。
- 原始 checkpoint 不动；所有消费方（payload、话题选择）只读视图。
- 边界：视图值非负且 ≤ raw。

### ② 立场账本

位置：`content-agent-state/application/content_mind/positions.json`（content_mind 模块拥有）。

Schema：

```json
{
  "position_id": "pos-<sha8>",
  "topic": "btc-eth-market-structure",
  "proposition": "费率转正+OI双扩张 ⇒ 结构从减仓转向增仓",
  "falsifier": "16:00 UTC 结算后费率回落或 OI 收缩",
  "status": "OPEN",
  "created_at": "...", "updated_at": "...",
  "evidence_refs": [], "post_ids": [], "resolution": null
}
```

- 状态机：`OPEN | CONFIRMED | REFUTED | ABANDONED`；非法转移拒绝。
- **登记**：v1 零额外 LLM 调用——内容周期输出结构带 position 字段（proposition+falsifier）则登记，无则不登记。
- **转移**：周期选择核验某 OPEN position → 产出核验帖 → CONFIRMED/REFUTED，并把核验结果作为 research-progress reward 回写该 topic 的 preference。确认与证伪都给正奖励（证伪是学习进展）；v1 奖励幅度统一取 feedback utility=1.0 档，二期再按证据强度分级。
- **ABANDONED**：OPEN 超 5 天无人问津自动放弃，自传体记放弃理由。
- 容错：损坏/缺字段条目跳过，不 crash；写入用原子替换。

### ③ 自传体块

每周期由状态机械拼装（确定性模板，零 LLM 成本），注入 `_mind_context()` 读到的 mind projection，新增字段：

```
我最近的立场：
- [OPEN·挂3天] <topic>: <命题>（可证伪条件：…）
- [昨天 REFUTED] <topic>: … → 我错了，错在 <resolution>
我反复回到的问题：<衰减 salience top-3 且有复访>
我的兴趣地形：<top-5 带分数>（其余话题只是路过）
```

失败兜底：拼装异常返回空块，不拖垮周期（沿用现有 `_mind_context` 兜底模式）。

### 选择策略（content_goal_loop）

决策 payload 增加两块：OPEN positions 列表 + 衰减兴趣视图。prompt 优先级：

1. 有到核验时点的 OPEN position → 核验优先（制度化现有自发行为）。
2. 否则按衰减 salience 加权选话题，ε≈20% 均匀探索保底。
3. 连续 K=3 帖同话题强制切换（防过拟合，配合现有开头结构查重 0.5 阈值）。

### 第一期测试

- 衰减数学（半衰期边界、last_touch 更新、非负性）。
- 账本状态机（非法转移拒绝、损坏容忍、原子写入）。
- 自传体块拼装（空账本、空兴趣、异常兜底）。
- 选择策略（核验优先、ε 探索、K 切换）。
- 现有测试套件全绿（本地 pytest 需 `PYTHONPATH="src;D:/ferro/agent-runtime/src"`）。

## 第二期（一期跑一周、有真实数据后）

1. **信号源扩容**（落地推翻"注意力≠成败"原则）：复访信号（同一 open_question/topic 自发重复出现 → preference 上调）；受众表现加权（待 analytics transport 有数据源，当前状态 UNAVAILABLE 不可依赖）；REFUTED 奖励按证据强度分级。
2. **风格自蒸馏**：从自家 SUCCESS 且过 evidence floor 的帖子提取确定性风格统计（可证伪条件句、"我错了"句式、hedging 模式、句长分布），注入 prompt 作风格基线。放二期原因：需样本量，且需先确认一期无模板化回归。与开头结构查重不同层（词法/论证习惯 vs 开头 60 字符形态），上线后盯查重拒绝率。
3. **核心 schema v2（条件触发）**：仅当指标显示视图层衰减未传导（如 VirtualBrain 内部目标选择仍扁平）才实施：state 加 `last_touch`、snapshot 惰性衰减、restore/replay 迁移兼容（接受旧 3 键 shape）。

## 验证标准（一期上线后 1-2 周）

| 维度 | 指标 | 红线 |
|---|---|---|
| 兴趣分化 | preference 标准差 0.02 → >0.08；衰减 top-5 话题日间重叠 ≥3/5；日均独立话题数下降、回访上升（复访 = 同一 topic 在 ≥2 个不同自然日发生 last_touch 更新） | — |
| 立场连续 | 每周 ≥5 新 position、≥3 次 OPEN→终态转移；≥50% 帖子推进已有 position | — |
| 人格连续 | 自传体块出现率 100%（兜底除外）；帖子自我引用句式上升 | — |
| 表达 | 人工读感：连读 5 篇认得出"同一个人" | 二期风格锚上线后结构查重拒绝率 >15% → 回退风格锚 |
| 产量 | 聚焦后自然下降（daily minimum 降至 20） | 连续 2 天 0 发帖 → 回退话题加权；evidence floor 拒绝率不得下降 |

## 部署与回滚

- 一期改动全在 wechat-traffic-agent：`content_mind.py`、`content_goal_loop.py`、`square_observation.py`、prompts、测试。部署 = 发 content-agent `current` release + 重启 `content-agent.service`。**不碰 agent-runtime**，不触发 venv editable 双写陷阱（见服务器拓扑记忆）。
- 服务器 daily minimum 100 → 20，与代码同批调整。
- 回滚：移除视图乘数与 payload 字段即可，零状态迁移；positions.json 保留无害。
- 本地工作区存在此前未 commit 的在途改动（mind-injection 等），本期改动保持最小独立 diff，不整体提交、不混线。

## 风险与取舍

- 话题加权降低覆盖广度：用户已明确接受（产量换个性）。
- "注意力≠成败"原则被推翻：已向用户明示并获认可；二期才落地，一期留观察期。
- 立场账本可能锁死选择（position 卡住主循环）：选择策略第 2/3 条 + ABANDONED 机制 + 产量红线兜底。
- 自传体块与结构查重的潜在冲突：一期自传体只进 payload 不进帖子表面结构；二期风格锚设置查重拒绝率红线。

## 实施偏差记录（2026-10-11，一期交付时）

- 学习率：代码基线已是 0.15（非设计时的 0.1），本次调至 0.2（`content_mind.observe_learning_interest`）。
- ε≈20% 均匀探索保底：**未实现，列入二期**。话题选择实际经 LLM 决策（owner 决策 prompt + square payload 衰减排序 + 指令），没有程序化选择器可注入 ε；一期以衰减排序、核验优先、K=3 同话题强制切换、产量红线（连续 2 天 0 发帖回退加权）作为替代护栏。
- ABANDONED 放弃理由已接入 `recent_resolutions`（5 天放弃条目一并浮出），自传体块可见，问责闭环完整。
- 选择策略落点与设计有架构性偏移：由 content_goal_loop 程序化加权改为"payload 衰减排序 + 决策层指令"，因话题选择本就由 LLM 完成；验证标准不变。
