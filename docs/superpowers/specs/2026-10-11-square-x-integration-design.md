# Square/X 两线打通设计（个性分化二期任务 1）

- 日期：2026-10-11
- 状态：已与用户对齐范围（C 全量 + 跟随周期不设上限），待实现
- 上游设计：`2026-10-11-personality-differentiation-design.md`（一期已部署：立场账本 + 衰减视图 + 核验优先 + streak 闸）
- 用户原则：一份内容多平台 fan-out，不是跨平台复制；X Premium 无 280 限制

## 背景与现状（2026-10-11 代码调研）

一期的 mind/立场只接了 Square observation 与 owner 决策两条路。X 线现状：

- **X 原创帖是内容周期下游**：内容周期结束 → `orchestration/social.py:398-411` → `x_loop.after_content_cycle(result=continuous)` → `platforms/x/x_social.py:817`。表达 prompt 两类：`X_EXPRESSION_DRAFT` 短帖（x_social.py:891-914）、`X_OWNER_LONGFORM_DRAFT` 长文（:1139-1159）。
- **mind 触点为零**：X 表达上下文来自磁盘周期文件（`_cycle_context` x_social.py:795-816，含静态 `mind_plan.json`）+ persona + mission + recent_shares。没有 live mind projection、没有立场账本、没有衰减兴趣。
- **x-observe 只写不读**：`x_social.py:508` observe 周期、`_handle_mention` 写 `l1_social_attention.json`（:651），全库无消费方。
- **发布与对账**：浏览器发布 `x_social.py:1211` → `platforms/x/x_browser.py:104-110` → `x_local_browser.py:133`；持久草稿先落盘（x_social.py:979-1007）；对账 `social.py:40-99`；事务 checkpoint `content/x/post-transactions/<digest>.json`。`PUBLICATION_UNVERIFIED` 出现于 x_social.py:1023 / x_browser.py:295-296。
- **既有扇出通道**：website 文章 → `PublicationEvent`（website/automatic_website.py:237-243）→ `DurablePublicationDispatch` channel=`x_article`（publication/dispatch.py:21-32）→ `platforms/x/x_article_distribution.py:86-140`（幂等 receipt）。
- **节奏**：`daily_posting_goal.py` 唤醒内容管线，X 帖为管线产出之一；CHECK_X 每 1800s 做观察/对账/扇出（run_check_x_worker.py:89-94,127-131）；L1 `x_strategy` HOLD 可拦截表达（x_social.py:863-877）。
- **运维事实**：2026-10-11 一期部署后 X expression 调用为零，唯一 post-transaction（部署前 5 小时）停在 `PUBLICATION_UNVERIFIED`——X 发布管线疑似静默。

## 目标

一个 Ferro，两个表达面：X 与 Square 消费**同一份** mind projection 与**同一个**立场账本；X 观察回流为账本证据；X 观察可自发登记 X 原生立场（`source="x"`）；核验/结算走同一套状态机与奖励。账本不分裂，人格连续。

## 组件

### ① live mind 注入 X 表达（与 Square 同构）

- 把 square_observation 的 `_mind_context` 构建逻辑提升为共享函数 `public_mind_context(mind)`（落点：content_mind 模块），Square 与 X 共用；含 staleness 过滤（OPEN 超过 ABANDON_AFTER 不进 payload）、有界文本、positions_open ≤6、recent_resolutions ≤4。square_observation 改为委托调用。
- **注入点唯一（已定）**：`XAutonomyLoop.__init__`（x_social.py:364-386）增加 `mind=None` 参数；`after_content_cycle`（:817）在 `_cycle_context` 之后设 `context["mind"] = self._mind_context()`（try/except → {}）；`_owner_longform`（:1112）入口同样合并。`_expression_model_context`（:127-191）末尾当 `context["mind"]` 为非空 dict 时追加进返回 payload——两个 prompt（短帖 :909-913、长文 :1157 都 spread 该函数输出）自动带上 mind，且落入 `x_expression_context_projection.json` 可审计。
- mind 实例接线：**只有主进程需要 mind**。X 表达（after_content_cycle → 短帖/长文 prompt）只发生在主进程（orchestration/social.py:398-411），`run_content_agent.py:567` 直接传已有 `mind=content_mind` 即可。`run_check_x_worker.py` **不构造 ContentMind**（避免 checkpoint.json 双进程写与 VirtualBrain 会话重复初始化）——worker 只碰账本，见③④。
- X 专用 mind 指令文案（语域更对话化），但实质与 Square 同源：立场可见、核验优先（有可测 falsifier 的 OPEN 立场时，X 表达优先做核验/跟进框架）、同 topic 连续性。

### ② 跨面记账

- 账本 `post_ids` 条目支持 `{"id": <hash>, "surface": "square"|"x"}`；`_load()` 把裸字符串归一化为 surface="square"，存储格式随每次写回收敛为 dict（`public_projection` 不投影 post_ids，LLM payload 不受格式影响；回滚时旧代码读 dict 仅损失 dedup 精度，无害）。
- 新增 `attach_post(position_id, post_id, surface)`：幂等（同 id+surface 不重复）。
- 挂接点（已定）：X 发布核验通过路径（x_social.py:1034-1046 拿到 post_id 后），用与③同一套**零 LLM** topic 匹配器比对发帖文本与账本立场，命中即 `attach_post(position_id, publication.post_id, surface="x")`。`run_check_x_worker` 侧不挂接（对账扇出不产生新立场足迹）。

### ③ x-observe 观察回流（evidence_refs）

- observe 周期（x_social.py:507-619）结束段：对 fresh mentions 与每个 OPEN position 做**零 LLM** topic 匹配——共享助手 `_topic_match(topic, text)`（topic 按非字母数字切词取小写 token，任一 token 命中 text.casefold() 即命中）。
- 命中即经新账本方法 `append_evidence(position_id, entry, cap=10)` 追加 `evidence_refs` 条目 `{source: "x_observe", ref: <x-post:<id>>, at: <iso>, note: ≤120字}`；每 position 上限 10 条，FIFO 淘汰。
- **红线：观察证据永不自动触发状态转移**。evidence 是被动材料，只在核验时进 prompt 作佐证。

### ④ X 原生立场生命周期

- observe 周期内新增**一次**轻量结构化调用（`X_POSITION_SCAN`，仅当 fresh mentions 非空）：合同 `{position: null | {topic, proposition, falsifier}}`，非空即 `register(..., source="x")`；缺字段/畸形/None → 跳过并 logger.warning，不炸观察周期。观察期预期产出少而精（每周 0-3 条为正常）。
- `register` 增加 `source` 参数（"square"|"x"，默认 "square"），入账本便于归因。
- 核验优先指令覆盖 X 表达（见①）；转移与奖励走一期同一路径——X 登记的 OPEN 立场进入唯一账本后，由后续 Square 核验周期按 topic 解析结算（transition → research-reward 回写 preference），不建第二套结算，worker 侧不发生转移。

### ⑤ 账本并发写保护

- 打通后主进程（register/transition/attach_post）与 X worker（register/append_evidence）都会写 positions.json。锁两层：模块级 `threading.Lock`（跨平台，Windows 测试可真实验证串行化语义）+ 服务器上 `fcntl.flock` 文件锁（best-effort，import 失败降级 no-op）。锁粒度 = 单次账本写操作的读-改-写窗口。
- 读路径保持免锁纯读（一期不变量：reads never mutate）。

## 前置诊断（实现阶段第 0 步）

X 发布管线静默须先查明：L1 x_strategy HOLD / 登录态 / 调度缺失 / after_content_cycle 未触发。是 bug 先修（若涉及浏览器登录态等超出本任务范围的问题，升级给用户），确认 X 线能承载发帖后再部署打通改动。

## 容错红线（与一期同源）

- mind/账本任何故障不得阻塞 X 的表达、观察、对账、扇出；每个账本操作独立 try/except + logger.warning。
- 账本损坏条目跳过；写入原子替换；故障吞掉后仍能被持久化状态断言的回归测试抓住。

## 测试清单

- mind 注入 X 表达 payload：含 positions_open/recent_resolutions；staleness 过滤生效；投影故障返回空上下文。
- attach_post：surface 标注、幂等、旧格式（裸字符串）混读兼容。
- evidence 回流：topic 命中追加、上限 FIFO、不触发转移。
- X 原生立场：schema 合法登记 source="x"；畸形跳过且观察周期照常完成。
- prime-directive 回归 ×4（对应一期模式）：账本损坏、投影为空、attach 失败、register 失败——X 表达/观察/发布均照常。
- 并发锁：写入串行化行为可测（单进程内锁语义）。

## 指标与红线（上线后 1-2 周）

| 维度 | 指标 |
|---|---|
| X 线健康 | X 日帖恢复 >0（诊断+打通后） |
| 立场贯通 | 带 mind 上下文的 X 表达比例（llm_audit 抽样）；跨面 position（post_ids 含两个 surface）数量 |
| X 原生立场 | 每周 source="x" 新登记数（预期少而精，0 也不 alarm，观察两周再评） |
| evidence 质量 | 回流条数；核验 prompt 中被引用率；若核验结论频繁与 X evidence 冲突 → 收紧匹配 |

红线：evidence 永不自动转移（结构性保证，非监控项）；X 侧账本写失败静默 → logger.warning 计数，出现即查。

## 回滚与部署

- 部署 = 叠加（position_ledger.py、content_mind.py、square_observation.py、platforms/x/x_social.py、run_content_agent.py 及新测试）到 `current` release + `sudo -n systemctl restart content-agent.service`。不碰 agent-runtime。`run_check_x_worker.py` 无需改动（XAutonomyLoop 已在 worker 构造，账本由 x_social 内部自建，无新构造参数）。
- 回滚 = 移除 payload 注入与可选字段调用；账本新字段（surface/source/evidence_refs）向后兼容，旧代码可读，零状态迁移。
- md5 双侧核对、CR 归一化 diff、不建备份目录、不翻新 worktree（沿用部署拓扑记忆全部规则）。

## 已确认的取舍

- evidence 匹配用零 LLM topic 比对：省调用、行为可预测；代价是匹配粗糙——靠"不触发转移"红线兜底，二期后续可升级为 LLM 相关性打分。
- 并发锁只锁写路径：读多写少，读免锁保持一期纯读不变量。
- X 帖量跟随内容周期不设上限（用户选定）；X Premium 无长度压力。
- 范围含 X 原生立场（用户选定 C 档）；为防碎片化，账本唯一、结算唯一，X 只增加登记来源与证据来源。
