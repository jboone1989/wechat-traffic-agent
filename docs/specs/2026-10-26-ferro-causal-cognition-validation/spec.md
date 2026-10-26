# Ferro 事件触发认知与三仓架构核查 — 待批准 Spec v0.1

状态：**研究草案，未批准；本 PR 仅记录验证方案，不修改程序或部署。**
日期：2026-10-26
项目：Ferro（wechat-traffic-agent） / agent-runtime / virtualbrain；EventGraph 是候选知识来源。
相关讨论：[Ferro Issue #292](https://github.com/jboone1989/wechat-traffic-agent/issues/292)
证据版本：Ferro main `6113965`、agent-runtime main `f5e9833`、virtualbrain main `42dd1b8`。VM13 当前进程正在运行，但**未获许可读取其生产状态目录和 Ferro journal，生产部署版本/事件回执仍待实证核对**。

## 1. 目标和边界

目标不是“神经元越多越先进”，而是：Ferro 能从可靠的新事件中主动选择值得研究的话题，制作有观点、有证据的原创内容，**官网先发布再同步渠道**，对真实阅读、互动及其他结果建立反馈，以较少的无效 LLM 调用持续改善选题和产出。生产环境是 VM13；不与 EventGraph 的 Web/DB 部署混用。

非目标：重新实现 EventGraph 新闻采集、重做 Agent Runtime 基础能力、自造消息中间件、因少量文件轮询而引入 Kafka/Redis/Kubernetes、为通过评估伪造神经行为、在未测出收益前大规模迁移模块。

评估分两道门：先确保 **事件因果链和任务可靠性**；再决定 **神经门控有没有独立收益**。任何一关不通过，不启动跨仓库大重构。

## 2. 已核实的源码结构（不是生产执行证明）

```text
业务工作者 / Agent Network
  → FerroStateEventBus.emit: application/state_events/pending/*
  → run_ferro_neural_worker: 每 0.2 秒发现事件
  → FerroStateEventApplier / FerroNeuralRuntime / Agent Runtime WakefulCore
  → cognition_triggers
  → IndependentTaskScheduler(FilePlanStore)
  → run_content_cognition_worker: 每 0.5 秒 claim_due
  → WakefulContentTaskRunner / ContentTaskMethods
  → 原创内容与网站发布 → X/币安广场等同步 → 后续结果
```

此外 `run_content_cognition_worker.py::_daily_schedule` 使用日发帖目标主动安排同一个 `CONTENT_COGNITION`。因此当前是**事件、截止时间与轮询混合**；这不自动构成缺陷，但无法只凭模块名称宣称事件主导。

- `scripts/run_ferro_supervisor.py`：VM13 正在运行该入口及六类 Worker（通过进程清单确认）。 
- `scripts/run_ferro_neural_worker.py:49-73`：先 `applier.apply`，然后 `bus.acknowledge`，才检查 `snapshot.cognition_triggers` 并安排任务。**ack 与 schedule 之间有崩溃窗口**。
- `src/wechat_traffic_agent/task_scheduler.py:65-74`：`claim_due` 在执行前取消计划，**并无执行确认/租约**；执行期间崩溃能丢唤醒。
- `src/wechat_traffic_agent/task_scheduler.py:31-47`：`content:CONTENT_COGNITION` 共享固定 wake ID。存在更早截止时间时，只返回 `EARLIER_ALREADY_SCHEDULED`，没有并合新事件 `relevant_refs`；**事件证据可能没有传递到本轮认知**。
- `src/agent_runtime/wakeful/plan_store.py:56-70`：相同 wake ID 的进程使用相同临时文件名和 get-write-cancel 分离操作；需要验证真实并发覆盖边界。
- `src/agent_runtime/wakeful/core.py:255-259`：只有 `mark_cognition` 清除旧 `cognition_triggers`；Ferro `src/wechat_traffic_agent/orchestration/content.py:368-414` 多个早退分支没有调用 `MARK_COGNITION`。`run_ferro_neural_worker.py:63-73` 对任意输入（包括只读快照）重新检查所有旧触发器，可能重复排任务。
- `src/wechat_traffic_agent/neural_pulse_source.py`：有“周期放电成为认知意图”的完整实现，但源码 `src/`、`scripts/` 只有类的定义，没有启动者（同时核对了本地 checkout 的 `git grep`）。**不能视为已投产**。
- `src/wechat_traffic_agent/wakeful_runtime.py::ContentWakefulRuntime` 与 `src/wechat_traffic_agent/ferro_neural_runtime.py::FerroNeuralRuntime` 并存；前者仍由 `scripts/run_content_agent.py` 的维护/一次性模式和评估代码引用，后者用于当前 Supervisor 神经 Worker。必须核对真实调用者，不能粗暴删除。
- 未找到 Ferro 当前代码中针对 **EventGraph** 的直接查询/事件来源绑定；通用 `NETWORK_EVIDENCE` 不能替代 EventGraph 实际关联来源。
- 目前也未核对生产中的事件→文章→结果回执；事件因果及收益属于**未验证**。

## 3. 先做的最小验证（应在隔离测试状态完成）

| 编号 | 输入和操作 | 必须观察到 | 失败即说明 |
| --- | --- | --- | --- |
| T1 | 注入唯一 `event_id` 的重大事件，且没有发帖截止时间到期 | 可追踪的 `event_id → trigger_id → wake_id → cognition start → cycle`，若拒绝则有具体原因 | 事件接线不完整 |
| T2 | 在 applier/ack/schedule 各边界模拟进程崩溃后重启 | 每条事件最终产生一次可关联的效果，或显式可重试失败；不静默丢失 | 持久性不足 |
| T3 | 认知领取计划后、产物落盘前终止进程后重启 | 可恢复或明确重试；同一产物不会被重复发布 | claim / 完成确认不足 |
| T4 | 同时提交重大事件及每日目标的 `CONTENT_COGNITION` 计划 | 两者的来源/目标均保留，选择和合并是确定性的 | 固定 wake ID 丢上下文 |
| T5 | 一次事件触发后让认知走资源拒绝/失败路径，再连续请求只读快照 | 只读操作本身不创造新任务；失败处理符合明确重试策略 | 旧 trigger 风暴 |
| T6 | 从 EventGraph MCP 读取一条可验证的新事件，形成内容并跟踪发布 | 源事件 ID/版本、原始来源、引用、选题、文章及结果能关联 | EventGraph 只是宣传性集成 |
| T7 | 检索 VM13 实际安装 SHA、Worker 心跳、调度状态及真实回执 | 能明确区分生产与 main 代码，且进程在线不等于任务有效 | 无法得出生产效果结论 |

T1-T5 优先通过现有 pytest、tmp_path、真实 FilePlanStore 和故障注入完成，不新增运行平台、不用 mock 假装结果成功。T6 先核对 EventGraph **已有** MCP 工具与业务需求，再添加最小适配，不能先复制 EventGraph 的采集/分类系统。T7 仅读统计和脱敏关联元数据，不传输 API Key/用户凭据。

## 4. 神经因果收益实验（通过 T1-T7 后才执行）

同一事件样本、LLM 模型、证据、预算、出版约束、反馈窗口下，在**隔离状态**中比较：

- A：事件重要度业务规则 + Deadline（不引入额外神经注意力决策）。
- B：同一事件流 + 当前 VirtualBrain LIF/WakefulCore 门控。

先检查 B 是否实际改变处理顺序、进入认知的事件集合或注意力预算；如果结果与 A 完全一致，无法声称神经门控增益。
主指标：合格原创产出/模型调用、带可靠来源的发布率、事件到合格发布的时延、重复/无意义认知次数；次指标：在可用观察窗口内的真实阅读/互动/后续表现。对照样本保持互相隔离，避免重复公开发帖；质量可进行盲审或离线影子决策。事前明确最小有用收益与成本上限；样本不足则记录「未证实」。

## 5. 归属假设（尚非迁移决定）

| 归属 | 应保留 | 要核实 / 暂不行动 |
| --- | --- | --- |
| Ferro | 官网内容研究、信源判断、出版和渠道、用户目标、结果归因、领域事件到行动策略 | 两套 WakefulRuntime 的实际入口；未接入的 NeuralPulseSource；多重目标/资源/记忆包装层 |
| Agent Runtime | 已有 WakefulCore、PlannedWake、FilePlanStore、MCP 能力、通用 LLM 资源池 | 文件计划的 claim/ack/retry 语义可能是通用缺口，但**必须先有第二个真实消费者**再考虑抽象/迁移 |
| VirtualBrain | LIF/神经图、connectome、可塑性与其科学评估 | 不应承担官网选题、分发、收益、发帖预算及 EventGraph 新闻规则 |
| EventGraph | 事件发现、聚合、关系与有来源的事实 | 只提供真实需要的查询或订阅接口，不复制采集与图谱到 Ferro |

在「必要性、消费者、可替代方案、能减少多少代码、能证伪的实验」没有论证完成前，不从 Ferro 向 Runtime 新增抽取能力。Agent Runtime 本身的通用子系统也不得因为 Ferro 不用就贸然删除，须先核查其他真实消费者及可选入口。

## 6. 交付与决策条件

- 第一份产物是 T1-T7 的运行记录、能关联的事件证据及确切失败点，而不是新的架构图。
- 修复最小的调度一致性问题后重跑完整回归；失败不得通过跳过集成测试、兜底或虚构假事件变绿。
- 将独立 Codex 反对意见与 **Claude Code 的独立审查**逐项并列（Claude 2026-10-26 首次调用因额度限制未执行，**仍待复审**），分歧保留证据。
- 只有用户确认 Spec、关键验证通过，才规划删减/迁移模块及镜像发布；生产改动与公开仓库可见性变更另行落实。
- 成功不是「更多神经元/模块/Issue」，而是更好的内容决策、更少无效调用、可追溯可靠交付。

## 停止条件

T1-T5 证明丢任务或旧触发重复时，先修复既有链路，暂停所有新神经能力；B 无法显示可靠增益时，不继续扩张 Ferro 神经模块；如果单一实例和简单文件计划仍适用，不升级消息基础设施。
