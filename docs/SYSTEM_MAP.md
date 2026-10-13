# 系统地图（2026-10-13 盘点）

本文件记录"什么代码在哪里运行"，以及仓库与线上之间的偏差。数据来自 2026-10-13
对服务器 `VM-0-9-ubuntu` 的只读检查和对 GitHub 仓库的盘点。线上状态变化后请更新本文件。

## 1. 正在运行的组件

| 组件 | 仓库 | 线上路径 | 线上版本 | 与仓库的关系 |
|---|---|---|---|---|
| 内容智能体（Ferro） | `wechat-traffic-agent` | `/home/ubuntu/content-agent/current` → `releases/05c99a1-square-data-review` | `05c99a1`（2026-10-09） | 落后 main 45 个提交；**另有 57 个未提交改动**（38 改 + 19 新增），见第 3 节 |
| Agent Runtime | `agent-runtime` | `/home/ubuntu/agent-runtime/current` → `releases/d5566d9-browser-poll-recovery` | `d5566d9`（2026-10-09） | 有 4 个未提交改动（`llm/deepseek.py`、`memory/local_retrieval.py`、`memory/session.py`、`wakeful/plan_store.py`） |
| Agent Runtime 运维/企业微信 | `agent-runtime` | `releases/321ea8b`（`agent-runtime-operator.service`） | `321ea8b` | 与上面不是同一个版本 |
| VirtualBrain | `virtualbrain` | `/home/ubuntu/virtualbrain/current` → `releases/42dd1b8-inquiry-provenance` | `42dd1b8`（2026-10-08） | 与 main 一致，工作区干净 |
| Screeps 训练 | （`ferro-screeps-runtime`，非独立仓库） | `/home/ubuntu/ferro-screeps-current` | `26d41ab`（2026-09-28） | 工作区干净；由 crontab 每 10 分钟运行 |
| omnipost（多平台发布） | **无仓库** | `/opt/omnipost`（Docker 容器内，root） | 不是 git 目录 | **只存在于服务器**；发布账本 `/opt/omnipost/state/ledger.db` |
| 交易系统 | `trading` | 另一台主机（`207.246.99.253:8080`，HTTP API 部署） | 未核对 | 不在本服务器 |
| Agent Network Seed | `agent-network` | `network.livingruntime.com` | 未核对 | 不在本服务器 |

## 2. 内容智能体的发布路径（2026-10-11 之后）

```
内容智能体 ──Square 草稿──▶ ferro-square-distribution（每 2 分钟）──MCP──▶ omnipost ──▶ 币安 Square
                                                                              └──▶ X（长帖）
内容智能体自带的 X 浏览器发帖通道：2026-10-11 已停用（x-decommission.conf，代码保留）
```

- omnipost 账本截至 2026-10-13：币安 Square 成功 70 条；X 成功 46 条、失败 1 条（超长）。
- **断点**：omnipost 发到 X 的帖子不会写回 `content/x/history.jsonl` 或任何 cycle 回执。
  X 指标采集器（`collect_x_outcomes.py`）只读这两处，所以 2026-10-09 之后没有采集到任何 X 反馈，
  读者反应也无法进入兴趣学习。

## 3. 只存在于线上的代码（需要同步回仓库）

线上 release 是 `/home/ubuntu/src/content-agent` 的 git worktree，在 `05c99a1` 之上有未提交改动：

- 新增：`platforms/omnipost_client.py`、`platforms/x/x_article_distribution.py`、
  `position_ledger.py`、`neural_pulse_source.py`、`portable_flock.py`、`sensory_stream.py`
  及 9 个对应测试；3 份 `docs/superpowers` 计划和 `docs/superpowers/specs/`。
- 修改：Square 发布/编辑/观察、X 社交与分发、发布事件存储、内容心智、每日发帖目标等 25 个源文件，
  7 个脚本，1 个 systemd 单元，13 个测试。
- 不应同步：`.env.ferro-llm`（凭据）、`.pytest_cache/`。

注意：`position_ledger.py`、`platforms/x/x_article_distribution.py` 在 main 上已经存在（通过后续提交合入），
而 `omnipost_client.py`、`neural_pulse_source.py`、`portable_flock.py`、`sensory_stream.py`
在 GitHub 任何分支上都不存在。同步时需要逐文件比对，不能直接覆盖。

## 4. systemd 配置的偏差

- `content-agent.service` 叠了 14 个 drop-in，其中 **5 个各自覆盖了 `ExecStart` / `WorkingDirectory`**
  （`e19701dd`、`7cbf52bc`、`4be2642a`、`82350042`、`current`），只有按字母序最后的
  `zzzzzz-current-release.conf` 生效。其余是历史遗留，容易误导排查。
- `/etc/systemd/system/` 下有 6 个 `content-agent.service.bak-*` / `.pre-*` 备份文件。
- `content-agent-x-outcomes.service/.timer` 存在于服务器，但**不在仓库**；该 timer 当前不在
  `list-timers` 结果中（未启用或已停止），而日志显示过去 14 天它运行过约 1254 次。
- 仓库 `deploy/systemd/` 里有 Square、钱包、研究等单元，但没有 `content-agent.service` 本体、
  它的 drop-in、`content-agent-x-outcomes.*` 和 `agent-runtime-operator.service`。

## 5. 分支盘点（2026-10-13）

| 仓库 | 默认分支 | 非默认分支 | 已完全合并（ahead=0） |
|---|---|---|---|
| wechat-traffic-agent | main | 12 | 8 |
| agent-runtime | main | 19 | 7 |
| virtualbrain | main | 10 | 7 |
| agent-network | master | 15 | 5 |
| trading | master | 10 | 1 |

另有 13 个仓库本次未涉及：`agent-engineering-kit`、`livingruntime-remote`、`pi-remote`、
`agency-wisdom-eval`、`livingruntime-agent-network`、`coder`、`personal-agent`、
`opportunity-agent`、`stories`、`report-agent`、`pm-agent`、`report-agent-hub`、`test`。

## 6. 整理建议（每项都需要负责人确认后执行）

按风险从低到高：

1. **同步线上代码**（第 3 节）到 `sync/prod-05c99a1-20261013` 分支，再与 main 逐文件合并。
   这是其他所有改动的前提，否则下一次部署 main 会覆盖掉 omnipost 对接。
2. **omnipost 纳入版本管理**：在 GitHub 建 `omnipost` 仓库（私有），并把它的启动方式写进仓库
   （目前是 Docker 内常驻进程，没有 systemd 单元，主机重启后是否自动恢复未知）。
3. **补齐 `deploy/`**：把线上实际生效的 `content-agent.service` 及仍有用的 drop-in、
   `content-agent-x-outcomes.*`、`agent-runtime-operator.service` 收进仓库；
   删除服务器上被覆盖的 4 个旧 release drop-in 和 6 个备份单元。
4. **修复 X 反馈断点**：omnipost 发 X 成功后写回 `content/x/history.jsonl`（或让采集器读 omnipost 账本）。
5. **删除已完全合并的分支**（共 28 个），剩余分支逐个判断。
6. **归档不再维护的仓库**（第 5 节末尾列表中长期未更新的），归档可随时恢复。
7. **冻结非主线功能**：Screeps 训练、果蝇连接组、钱包追踪等与"博主 + 交易员"两条主线无关的服务，
   先停用定时器、保留代码。
