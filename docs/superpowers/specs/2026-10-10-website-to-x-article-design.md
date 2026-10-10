# 设计：website 文章 → X Article 分发

日期：2026-10-10
状态：已获用户批准（方案 A；三个决策：全部 website 发布、文章替代链接分享、LLM 起草图片计划）

## 背景与目标

两层内容模型的研究层：深入研究的文章发布到 website（AdSense/SEO 目标）后，应以 X Article（全文+配图）形式同步发布到 X，作为价值展示。现状是 website 发布只产生纯链接分享（`publish_x`：title+summary+URL，带自设 280 截断）。

X Article 生产路径（`_owner_longform` → `XBrowserPublisher` → jev 事务化发布）已完整接线且是 Premium 口径（25000 字符上限）；本设计把 website 文章喂进既有文章发布能力，不新建传输。

**范围外**：短贴层（OKX Orbit / Binance Square）不动；`x_social._owner_longform` 自主文章路径不动；旧 `x` 渠道历史 outbox 任务不清理（新 adapters 不消费它们，成为惰性记录）。

## 架构与数据流

```
website 发布 → PublicationEvent → DurablePublicationDispatch(channels=("x_article",))
    → DistributionTask(channel="x_article") 入 outbox
CHECK_X worker（run_check_x_worker，每 30 分钟）→ DistributionWorker claim
    → publish_x_article(event, task):
        1. 从 articles 表取 content_json → ContentArtifact
        2. artifact 级去重检查（见幂等节）
        3. sections 渲染为纯文本正文（标题行 + 段落）
        4. LLM 起草图片计划（缓存）→ ArticleImageSpec 列表
        5. prepare_article_media → 封面 + 内嵌图（caption/署名注入正文）
        6. transport.publish_article 事务化发布 → 公开验证
        7. verified POSTED receipt → outbox 记 SUCCESS
```

关键事实（已核实）：
- `content_json` 就是完整 ContentArtifact JSON（`article_repository.py` 按 `ContentArtifact.model_validate_json` 读回）。
- `prepare_article_media` 自带 per-image receipt 缓存（`media.json`：request 匹配 + 文件哈希校验），重试幂等；图片计划变更会被显式拒绝。
- `publish_article` 的 `x_article_transaction.json` 已有 digest 绑定 + POSTED 短路 + reconcile 恢复。
- CHECK_X worker 进程已构造 `cognition_runtime`（`load_independent_ferro_cognition_runtime`），可作为图片计划 LLM 的模型运行时。
- 空图片计划时 `prepare_article_media` 自动落到 `generate_article_cover` 原图封面（确定性渲染，零外部依赖兜底）。

## 组件改动

| 组件 | 改动 |
|---|---|
| **新文件** `src/wechat_traffic_agent/platforms/x/x_article_distribution.py` | `publish_x_article(event, task, *, state_root, model_runtime)`；正文渲染函数；图片计划 prompt 与结构化调用。结构镜像 `distribution.py`（`x_browser_writes_enabled()` 门、transport 返回校验、`asyncio.run` 包装同步适配器签名）。 |
| `src/wechat_traffic_agent/publication/dispatch.py` | `DurablePublicationDispatch` 默认 `channels=("x",)` → `("x_article",)`。 |
| `scripts/run_check_x_worker.py` | `DistributionWorker` adapters 从 `{"x": publish_x}` 换成 `{"x_article": lambda event, task: publish_x_article(event, task, state_root=state_root, model_runtime=cognition_runtime)}`。`DailyPostingGoal.notify_distribution_result` 调用保持不变（渠道无关）。 |
| 工作目录（新增约定） | `state/content/x_article_distribution/<artifact_id>/`：`x_article_transaction.json`、`image_plan.json`、`x_article_images/`（media receipts 由 `prepare_article_media` 自己写入管理）。 |

图片计划：一次结构化 LLM 调用（`ReceiptRuntime` 包装，与 `x_social` 现有模式一致），输入 title/summary/sections/tags，输出可校验为 `ArticleImageSpec` 列表的 JSON。**校验先于缓存**：LLM 返回后立即 `ArticleImageSpec.model_validate` 并检查 COVER ≤1，通过才写 `image_plan.json`——非法计划不落盘，重试时重新起草而不是永远复用坏计划。生成图走 `ChatGPTImageGenerator`，搜图走 Commons 授权筛选（含 `AgentRuntimeChatGPTProvider` 选图）——全部复用 `x_article_media.py` 现有管线，不新写媒体代码。

## 幂等与去重（三层）

1. **图片计划缓存**：首次成功调用后写 `image_plan.json`；重试原样复用，保证 media receipts 与正文 digest 跨重试稳定。
2. **发布事务**：`publish_article` 的 digest 绑定 + POSTED 短路——同内容重试直接返回已验证结果，不重复提交。
3. **artifact 级去重**：website 修订重发（同 artifact_id、新 content_hash → 新 event → 新 task）时，适配器先查该 artifact 目录的发布 receipt；已发布则返回原 receipt（SUCCESS + 原 URL），不在 X 上重发新帖。

## 错误处理

- **媒体失败**（生成失败 / 搜图无果 / 选图 HOLD）：任务按 outbox 既有语义重试至 `FAILED_PERMANENT`。遵循既定哲学 "errors never become a substitute cover"——不降级发布。
- **正文超 25000 字符**（`X_PREMIUM_LONG_POST_MAX_CHARS`）：抛 `NonRetryablePublication`，不静默截断。
- **浏览器/会话不可用**：任务重试（与原链接分享一致）。
- **writes 门**：`x_browser_writes_enabled()` 为假时 RuntimeError（同 `publish_x`）。
- **每日预算**：不在此集成，与被替代的链接分享行为一致。

## 测试计划

新适配器单元测试（fake transport + fake model runtime）：
1. 正常路径：发布成功，返回含 `https://` URL 与 post_id 的 verified POSTED receipt。
2. 重试幂等：图片计划 LLM 只调用一次（第二次 attempt 复用 `image_plan.json`）。
3. artifact 去重：同 artifact 不同 event 的第二个任务返回原 receipt，transport 未被再次调用。
4. 媒体失败进入重试路径（任务 error 记录，非 SUCCESS）。
5. 正文超 25000 → `NonRetryablePublication`。
6. writes 关闭 → 拒绝。

存量测试更新：
- `tests/test_binance_square_distribution.py`：两处 channel 断言 `"x"` → `"x_article"`（`test_producer_has_no_binance_dependency`、`test_retired_consumer_never_queues_new_or_old_website_events`）。
- `tests/test_publication_outbox.py`：字面 `"x"` 渠道用例不受影响（渠道名只是 adapters 字典键）。

## 明确不做

- 不改 `x_social.py` / `_owner_longform` / `XBrowserPublisher`。
- 不删旧 `x` 渠道历史任务。
- 不做每日发帖预算集成。
- 短贴层（Square 并入 / OKX）另行设计。
