# PR1 实验台账（Experiment Ledger）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前"每次运行覆盖上一次输出"的脚本，改造成不可变实验台账：每次生成一个带完整 provenance 的 `Experiment` 记录，同日多次运行互不覆盖，且四个 `ContentObjective` 真正通过 `ObjectivePolicy` 决定组合指导与奖励计算。

**Architecture:** 新增两个纯逻辑模块：`objectives.py`（每目标的 guidance / 主次指标 / 缺失指标重归一化的 reward）与 `experiments.py`（frozen `Experiment` 记录 + 按日分配 `exp-NN` 槽位的 `ExperimentLedger`）。`PerformanceObservation` 的指标字段改为可选（`None` = UNKNOWN，绝不当 0），reward 计算从模型移入 `ObjectivePolicy`。`ContentPackager` 输出改为 `output/YYYY-MM-DD/exp-NN/` 布局并写入 `experiment.json`。

**Tech Stack:** Python 3.11、pydantic v2、agent-runtime 0.3.0（托管 Runtime，不修改）、pytest。

**基线（开始前必须确认）：** 仓库 `main` @ `774ece4`，工作树干净，`python -m pytest -q` → `9 passed`。分支：`feat/wechat-experiment-ledger`。

**PR1 之后的路线（不在本计划内）：** PR2 极薄互联网 Port（`HotItem`/`ContentSnapshot` DTO + DailyHotApi/Agent-Reach/Fixture provider）；PR3 Viral Radar（deterministic 排名）。每个 PR 单独分支、单独计划、单独验收，绝不合并成巨型 commit。

---

### Task 0: 建分支、落盘架构说明、确认基线

**Files:**
- Create: `docs/specs/2026-08-26-wechat-rearchitecture.md`
- 分支：`feat/wechat-experiment-ledger`（从 `main` @ `774ece4`）

- [ ] **Step 1: 确认工作树干净并建分支**

Run:
```bash
git status --short
git checkout -b feat/wechat-experiment-ledger
```
Expected: 第一行无输出（干净）；第二行 `Switched to a new branch 'feat/wechat-experiment-ledger'`。

- [ ] **Step 2: 落盘架构说明**

Write `docs/specs/2026-08-26-wechat-rearchitecture.md`，内容为本轮确定的架构文档（用户消息原文，去掉最后一段面向执行 LLM 的口语化收尾），必须包含：

- 三层边界：Agent Runtime / Internet Capability / Opportunity Agent 与 WeChat Traffic Agent（附 ASCII 图与"谁决定什么"三问三答）；
- OSS 策略：DailyHotApi（广撒网）、Agent-Reach（Top-N 深抓）、NewsNow（备用，第一版不接）；不自己写平台 scraper；
- PR1–PR7 实施顺序表与每个 PR 的验收标准；
- 13 条工程约束表（不重复造轮子 / 不自动发布 / 不复制 Runtime / 不依赖 Opportunity / 不做蓝海分析 / 不让 LLM 排数字 / 不制造因果 / UNKNOWN≠0 / 不覆盖实验 / 不跨 context 污染 / 不同时改大量变量 / 不写平台 scraper / 不做大抽象）；
- 最终目标架构图。

- [ ] **Step 3: 确认基线测试通过**

Run: `python -m pytest -q`
Expected: `9 passed in ...`

- [ ] **Step 4: Commit**

```bash
git add docs/specs/2026-08-26-wechat-rearchitecture.md
git commit -m "docs: capture rearchitecture brief (PR1-PR7 roadmap)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 1: 版本统一到 0.3.0

**Files:**
- Modify: `pyproject.toml:7`（version 0.1.0 → 0.3.0）
- Modify: `agent.yaml:22-24`（删除 `metadata.primary_metric: reads`——单一主指标与 ObjectivePolicy 矛盾）
- Test: `tests/test_wechat_traffic_agent.py`（新增版本一致性测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_wechat_traffic_agent.py` 顶部 import 区加 `import tomllib`（stdlib，3.11+），文件末尾加：

```python
def test_agent_version_matches_package_version() -> None:
    root = Path(__file__).parents[1]
    manifest = load_agent_manifest(root / "agent.yaml")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest.version == pyproject["project"]["version"] == "0.3.0"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_wechat_traffic_agent.py::test_agent_version_matches_package_version -v`
Expected: FAIL（manifest 是 0.3.0，pyproject 是 0.1.0）

- [ ] **Step 3: 实现**

`pyproject.toml` 第 7 行改为：

```toml
version = "0.3.0"
```

`agent.yaml` 的 `metadata` 块改为：

```yaml
metadata:
  publish_mode: manual
  strategy: specific_audience_scene_pain
  owner: agent-runtime
```

（`version: 0.3.0` 保持不动；`runtime_api: ">=0.3,<1.0"` 保持不动——它表达 Runtime 兼容性，与 Agent 自身版本无关。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest -q`
Expected: `10 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml agent.yaml tests/test_wechat_traffic_agent.py
git commit -m "chore: unify agent version to 0.3.0

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 目标感知的奖励管线（models + objectives + learning）

**Files:**
- Modify: `src/wechat_traffic_agent/models.py`（PerformanceObservation 指标可选 + conversions；删除 reward/estimated_open_rate；ContentPackage 加 experiment_file）
- Create: `src/wechat_traffic_agent/objectives.py`
- Modify: `src/wechat_traffic_agent/learning.py`（目标感知、UNKNOWN≠0）
- Test: `tests/test_objective_policy.py`（新建）

这是 PR1 1.2 的核心。三个文件是一组不可分割的变更（reward 属性从模型移除的同时必须有新去处），作为一个 TDD 任务提交。

- [ ] **Step 1: 写失败测试**

Create `tests/test_objective_policy.py`：

```python
from __future__ import annotations

import pytest

from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import (
    ContentObjective,
    EmotionAngle,
    PerformanceObservation,
)
from wechat_traffic_agent.objectives import ObjectiveMetric, policy_for


def _obs(**changes) -> PerformanceObservation:
    values = {
        "post_id": "p1",
        "angle": EmotionAngle.RESONANCE,
        "impressions": 1000,
        "reads": 500,
        "shares": 50,
        "follows": 20,
        "comments": 30,
        "conversions": 5,
    }
    values.update(changes)
    return PerformanceObservation(**values)


def test_objectives_yield_different_rewards_for_the_same_observation() -> None:
    obs = _obs()
    rewards = {
        objective: policy_for(objective).reward(obs) for objective in ContentObjective
    }
    assert all(reward is not None for reward in rewards.values())
    assert len(set(rewards.values())) == 4
    assert policy_for(ContentObjective.READS).primary_metric is ObjectiveMetric.READS
    assert policy_for(ContentObjective.FOLLOWS).primary_metric is ObjectiveMetric.FOLLOWS
    assert policy_for(ContentObjective.DISCUSSION).primary_metric is ObjectiveMetric.DISCUSSIONS
    assert policy_for(ContentObjective.CONVERSION).primary_metric is ObjectiveMetric.CONVERSIONS


def test_missing_secondary_metric_renormalizes_remaining_weights() -> None:
    obs = _obs(shares=None)
    reward = policy_for(ContentObjective.FOLLOWS).reward(obs)
    follows_rate = 20 / 1000
    reads_rate = (500 + 2.0) / (1000 + 20.0)
    expected = (0.60 * follows_rate + 0.25 * reads_rate) / 0.85
    assert reward == pytest.approx(expected)


def test_all_metrics_missing_is_unknown_not_zero() -> None:
    obs = PerformanceObservation(post_id="p1", angle=EmotionAngle.RELIEF)
    assert policy_for(ContentObjective.READS).reward(obs) is None
    assert policy_for(ContentObjective.CONVERSION).reward(obs) is None


def test_primary_only_with_all_secondaries_missing_equals_primary_rate() -> None:
    obs = _obs(reads=None, shares=None)
    reward = policy_for(ContentObjective.FOLLOWS).reward(obs)
    assert reward == pytest.approx(20 / 1000)


def test_learner_treats_unavailable_primary_metric_as_unexplored() -> None:
    learner = TrafficLearner()
    history = [
        PerformanceObservation(
            post_id=f"{angle.value}-{sample}",
            angle=angle,
            impressions=100,
            reads=10,
        )
        for angle in EmotionAngle
        for sample in range(3)
    ]
    follow_estimates = learner.estimates(history, ContentObjective.FOLLOWS)
    assert all(item.reward is None for item in follow_estimates)
    assert all(item.scored_samples == 0 for item in follow_estimates)
    # READS 目标下同一份历史有得分，会正常利用……
    assert learner.recommend(history, ContentObjective.READS) is EmotionAngle.RESONANCE
    # ……而 FOLLOWS 目标下没有任何奖励证据，必须继续探索，
    # 绝不能把缺失当 0 分利用（也不得选出 READS 会利用的同一个角度）。
    assert learner.recommend(history, ContentObjective.FOLLOWS) is not EmotionAngle.RESONANCE
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_objective_policy.py -v`
Expected: 全部 FAIL（`objectives` 模块不存在；`conversions`/可选指标尚不存在）

- [ ] **Step 3: 实现 models.py 变更**

把 `PerformanceObservation` 整个类替换为（删除 `estimated_open_rate` 与 `reward` 两个 property）：

```python
class PerformanceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    post_id: str = Field(min_length=1)
    title: str = ""
    angle: EmotionAngle
    impressions: int | None = Field(default=None, ge=0)
    reads: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    follows: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    published_at: datetime | None = None
```

把 `ContentPackage` 替换为（`experiment_file` 先可选，T4 重写 packager 后恒被填充）：

```python
class ContentPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    root: Path
    markdown: Path
    json_file: Path
    preview_html: Path
    experiment_file: Path | None = None
```

- [ ] **Step 4: 实现 objectives.py**

Create `src/wechat_traffic_agent/objectives.py`：

```python
"""Objective-aware policies: composition guidance, metric priorities, reward."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import ContentObjective, PerformanceObservation


class ObjectiveMetric(str, Enum):
    READS = "reads"
    FOLLOWS = "follows"
    DISCUSSIONS = "discussions"
    SHARES = "shares"
    CONVERSIONS = "conversions"


@dataclass(frozen=True)
class ObjectivePolicy:
    """Decides how one experiment objective guides composition and reward."""

    objective: ContentObjective
    guidance: str
    primary_metric: ObjectiveMetric
    secondary_metrics: tuple[ObjectiveMetric, ...]
    weights: tuple[tuple[ObjectiveMetric, float], ...]

    def _reached(self, obs: PerformanceObservation) -> int:
        return max(obs.impressions or 0, obs.reads or 0, 1)

    def _metric_rate(
        self, metric: ObjectiveMetric, obs: PerformanceObservation
    ) -> float | None:
        if metric is ObjectiveMetric.READS:
            if obs.reads is None:
                return None
            return min(1.0, (obs.reads + 2.0) / (self._reached(obs) + 20.0))
        raw = {
            ObjectiveMetric.FOLLOWS: obs.follows,
            ObjectiveMetric.DISCUSSIONS: obs.comments,
            ObjectiveMetric.SHARES: obs.shares,
            ObjectiveMetric.CONVERSIONS: obs.conversions,
        }[metric]
        if raw is None:
            return None
        return min(1.0, raw / self._reached(obs))

    def reward(self, obs: PerformanceObservation) -> float | None:
        """Weighted metric mix over available metrics, gated by the primary metric.

        An observation without the objective's primary metric carries no
        evidence for that objective: it is UNKNOWN (None), never zero. When
        the primary metric is available, remaining weights renormalize over
        whatever other metrics exist.
        """
        primary = self._metric_rate(self.primary_metric, obs)
        if primary is None:
            return None
        pairs = [
            (weight, self._metric_rate(metric, obs)) for metric, weight in self.weights
        ]
        available = [(weight, rate) for weight, rate in pairs if rate is not None]
        total = sum(weight for weight, _ in available)
        return sum(weight * rate for weight, rate in available) / total


READS_POLICY = ObjectivePolicy(
    objective=ContentObjective.READS,
    guidance=(
        "本轮目标是提高阅读与完读表现：标题制造值得点开的具体悬念，"
        "正文每一句都给出继续读下去的理由，把最有信息量的句子放在前面。"
    ),
    primary_metric=ObjectiveMetric.READS,
    secondary_metrics=(
        ObjectiveMetric.SHARES,
        ObjectiveMetric.FOLLOWS,
        ObjectiveMetric.DISCUSSIONS,
    ),
    weights=(
        (ObjectiveMetric.READS, 0.65),
        (ObjectiveMetric.SHARES, 0.15),
        (ObjectiveMetric.FOLLOWS, 0.10),
        (ObjectiveMetric.DISCUSSIONS, 0.10),
    ),
)

FOLLOWS_POLICY = ObjectivePolicy(
    objective=ContentObjective.FOLLOWS,
    guidance=(
        "本轮目标是提高关注转化：让读者产生「这个号值得持续看」的判断，"
        "互动句自然带出继续关注的收益，不乞求、不承诺、不强迫。"
    ),
    primary_metric=ObjectiveMetric.FOLLOWS,
    secondary_metrics=(ObjectiveMetric.READS, ObjectiveMetric.SHARES),
    weights=(
        (ObjectiveMetric.FOLLOWS, 0.60),
        (ObjectiveMetric.READS, 0.25),
        (ObjectiveMetric.SHARES, 0.15),
    ),
)

DISCUSSION_POLICY = ObjectivePolicy(
    objective=ContentObjective.DISCUSSION,
    guidance=(
        "本轮目标是引发讨论：互动问题要门槛低、有真实分歧空间，"
        "让不同处境的读者都想开口，禁止用对骂或站队制造评论。"
    ),
    primary_metric=ObjectiveMetric.DISCUSSIONS,
    secondary_metrics=(ObjectiveMetric.SHARES, ObjectiveMetric.READS),
    weights=(
        (ObjectiveMetric.DISCUSSIONS, 0.60),
        (ObjectiveMetric.SHARES, 0.25),
        (ObjectiveMetric.READS, 0.15),
    ),
)

CONVERSION_POLICY = ObjectivePolicy(
    objective=ContentObjective.CONVERSION,
    guidance=(
        "本轮目标是后端转化：只围绕 audience 里的 backend_offer 做自然引导，"
        "先给足信息价值再提到提供物；没有 backend_offer 时退化为关注引导。"
    ),
    primary_metric=ObjectiveMetric.CONVERSIONS,
    secondary_metrics=(ObjectiveMetric.FOLLOWS, ObjectiveMetric.READS),
    weights=(
        (ObjectiveMetric.CONVERSIONS, 0.70),
        (ObjectiveMetric.FOLLOWS, 0.20),
        (ObjectiveMetric.READS, 0.10),
    ),
)

OBJECTIVE_POLICIES = {
    ContentObjective.READS: READS_POLICY,
    ContentObjective.FOLLOWS: FOLLOWS_POLICY,
    ContentObjective.DISCUSSION: DISCUSSION_POLICY,
    ContentObjective.CONVERSION: CONVERSION_POLICY,
}


def policy_for(objective: ContentObjective) -> ObjectivePolicy:
    return OBJECTIVE_POLICIES[objective]
```

（READS 权重 0.65/0.15/0.10/0.10 与原固定公式一致，保证旧行为不回归。奖励采用**主指标门控**：primary 缺失 → 整个观察视为 UNKNOWN 返回 None；primary 存在 → 其余可用指标重归一化。没有主指标数据的观察对"该目标"没有证据力。）

- [ ] **Step 5: 实现 learning.py**

整个文件替换为：

```python
"""Evidence-based selection of emotional angles, per experiment objective."""
from __future__ import annotations

from dataclasses import dataclass

from .models import ContentObjective, EmotionAngle, PerformanceObservation
from .objectives import policy_for


@dataclass(frozen=True)
class AngleEstimate:
    angle: EmotionAngle
    samples: int
    scored_samples: int
    impressions: int
    reward: float | None


class TrafficLearner:
    """Pure learner: history is supplied by Runtime sensors, never stored here."""

    def estimates(
        self,
        history: list[PerformanceObservation],
        objective: ContentObjective = ContentObjective.READS,
    ) -> list[AngleEstimate]:
        policy = policy_for(objective)
        estimates: list[AngleEstimate] = []
        for angle in EmotionAngle:
            items = [item for item in history if item.angle is angle]
            impressions = sum(item.impressions or 0 for item in items)
            scored: list[tuple[float, int]] = []
            for item in items:
                reward = policy.reward(item)
                if reward is not None:
                    scored.append((reward, max(item.impressions or 0, 20)))
            if scored:
                weight = sum(weight for _, weight in scored)
                reward = sum(r * w for r, w in scored) / weight
            else:
                reward = None
            estimates.append(
                AngleEstimate(
                    angle=angle,
                    samples=len(items),
                    scored_samples=len(scored),
                    impressions=impressions,
                    reward=reward,
                )
            )
        return estimates

    def recommend(
        self,
        history: list[PerformanceObservation],
        objective: ContentObjective = ContentObjective.READS,
    ) -> EmotionAngle:
        estimates = self.estimates(history, objective)
        # Explore every angle twice before exploiting measured performance.
        # An angle with samples but no scorable metric is still unexplored:
        # "no data" must never be read as "bad performance".
        under_sampled = [
            item for item in estimates if item.samples < 2 or item.scored_samples == 0
        ]
        if under_sampled:
            return min(
                under_sampled,
                key=lambda item: (item.samples, item.impressions, item.angle.value),
            ).angle
        return max(
            estimates,
            key=lambda item: (item.reward or 0.0, item.impressions, item.angle.value),
        ).angle
```

- [ ] **Step 6: 运行全量确认通过**

Run: `python -m pytest -q`
Expected: `15 passed in ...`（9 旧 + 1 版本 + 5 新增；旧测试 `test_learner_explores_then_uses_observed_reward` 在 READS 默认策略下行为不变）

- [ ] **Step 7: Commit**

```bash
git add src/wechat_traffic_agent/models.py src/wechat_traffic_agent/objectives.py src/wechat_traffic_agent/learning.py tests/test_objective_policy.py
git commit -m "feat: objective-aware reward policy with UNKNOWN-safe metrics

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Experiment 模型与 Ledger

**Files:**
- Create: `src/wechat_traffic_agent/experiments.py`
- Test: `tests/test_experiment_ledger.py`（新建，只测 ledger 分配与 Experiment 模型本身，不依赖 packager）

- [ ] **Step 1: 写失败测试**

Create `tests/test_experiment_ledger.py`：

```python
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.experiments import Experiment, ExperimentLedger
from wechat_traffic_agent.models import (
    AudienceProfile,
    ContentObjective,
    EmotionAngle,
    TietuDraft,
)


def _audience() -> AudienceProfile:
    return AudienceProfile(
        people="独自在大城市工作的职场人",
        scene="工作日深夜回家的十分钟",
        recurring_need="被理解并获得一个不消耗自己的提醒",
    )


def _draft() -> TietuDraft:
    return TietuDraft(
        title="别让副业偷走你的下班",
        lines=(
            "你盯着阅读量，不是在复盘，",
            "是在等一个数字证明今晚没白熬。",
            "今晚先写清楚你在替谁说话。",
        ),
        interaction_hook="你做内容最累的，是没流量，还是不知道为谁写？",
        angle=EmotionAngle.GENTLE_REBELLION,
        topic="做内容的人正在被流量绑架",
        pain_point="深夜用更新数量对抗流量焦虑",
        viewpoint="增加发布数量之前，先弄清楚内容在替哪个具体的人说话。",
        source_urls=("https://x.com/WolfMoneyNotes/status/2077624635105980561",),
        rationale="命中副业创作者深夜看数据的场景。",
    )


def test_ledger_allocates_next_free_slot_without_overwrite(tmp_path) -> None:
    ledger = ExperimentLedger()
    today = date(2026, 8, 26)
    ids: list[str] = []
    for _ in range(3):
        experiment_id, target = ledger.allocate(tmp_path, today)
        target.mkdir(parents=False, exist_ok=False)  # 必须能成功创建全新目录
        ids.append(experiment_id)
    assert ids == ["2026-08-26-exp-01", "2026-08-26-exp-02", "2026-08-26-exp-03"]


def test_ledger_continues_after_existing_slots(tmp_path) -> None:
    ledger = ExperimentLedger()
    today = date(2026, 8, 26)
    day_dir = tmp_path / today.isoformat()
    for name in ("exp-01", "exp-02", "exp-03"):
        (day_dir / name).mkdir(parents=True, exist_ok=False)
    experiment_id, target = ledger.allocate(tmp_path, today)
    assert experiment_id == "2026-08-26-exp-04"
    target.mkdir(parents=False, exist_ok=False)  # 续跑不覆盖历史槽位


def test_experiment_is_frozen_and_round_trips() -> None:
    experiment = Experiment(
        experiment_id="2026-08-26-exp-01",
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        audience=_audience(),
        objective=ContentObjective.READS,
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        draft=_draft(),
        source_refs=(),
        runtime_run_id="run-0",
    )
    restored = Experiment.model_validate_json(experiment.model_dump_json())
    assert restored == experiment
    assert restored.candidate_id is None
    assert restored.pattern_id is None
    with pytest.raises((TypeError, ValidationError)):
        experiment.experiment_id = "mutated"  # frozen：历史不可变
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_experiment_ledger.py -v`
Expected: 全部 FAIL（`experiments` 模块不存在）

- [ ] **Step 3: 实现 experiments.py**

Create `src/wechat_traffic_agent/experiments.py`：

```python
"""Immutable experiment records and per-day slot allocation."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .models import AudienceProfile, ContentObjective, TietuDraft


class Experiment(BaseModel):
    """One immutable content experiment. Fields never change after creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    created_at: datetime
    audience: AudienceProfile
    objective: ContentObjective
    candidate_id: str | None = None
    pattern_id: str | None = None
    strategy: dict[str, Any]
    draft: TietuDraft
    source_refs: tuple[dict[str, Any], ...]
    runtime_run_id: str


class ExperimentLedger:
    """Allocates the next free experiment slot under output/<date>/exp-NN."""

    _SLOT = re.compile(r"^exp-(\d+)$")

    def allocate(self, output_root: Path, today: date) -> tuple[str, Path]:
        """Return the next free slot for today; creates the day directory if absent."""
        day_dir = output_root / today.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        numbers = [
            int(match.group(1))
            for name in day_dir.iterdir()
            if name.is_dir() and (match := self._SLOT.match(name.name))
        ]
        slot = max(numbers, default=0) + 1
        experiment_id = f"{today.isoformat()}-exp-{slot:02d}"
        return experiment_id, day_dir / f"exp-{slot:02d}"
```

（`experiment_id` 带日期前缀，保证跨天全局唯一——PR6 的反馈回填以它为主键；目录名与用户规格一致为 `exp-NN`。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_experiment_ledger.py -v`
Expected: `3 passed`；再跑全量 `python -m pytest -q` → `18 passed in ...`（15 旧 + 3 新）

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/experiments.py tests/test_experiment_ledger.py
git commit -m "feat: immutable Experiment record and per-day slot ledger

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Packager 新布局（exp-NN + experiment.json）

**Files:**
- Modify: `src/wechat_traffic_agent/packaging.py`（整体重写 create；markdown 增加按 objective 的回填指标段）
- Modify: `src/wechat_traffic_agent/service.py`（仅 `create_content_package` 透传新参数，保持运行时链路不红；policy/prompt 接线留给 T5）
- Modify: `tests/test_wechat_traffic_agent.py`（旧 `test_packager_creates_copy_ready_files` 更新到新布局；import 增加 `datetime`、`timezone`）
- Test: `tests/test_experiment_packager.py`（新建：3 次同日运行互不覆盖 + packager 落盘的 provenance 记录）

- [ ] **Step 1: 写失败测试**

Create `tests/test_experiment_packager.py`：

```python
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from wechat_traffic_agent.experiments import Experiment
from wechat_traffic_agent.models import (
    AudienceProfile,
    ContentObjective,
    EmotionAngle,
    TietuDraft,
)
from wechat_traffic_agent.packaging import ContentPackager


def _audience() -> AudienceProfile:
    return AudienceProfile(
        people="独自在大城市工作的职场人",
        scene="工作日深夜回家的十分钟",
        recurring_need="被理解并获得一个不消耗自己的提醒",
    )


def _draft() -> TietuDraft:
    return TietuDraft(
        title="别让副业偷走你的下班",
        lines=(
            "你盯着阅读量，不是在复盘，",
            "是在等一个数字证明今晚没白熬。",
            "今晚先写清楚你在替谁说话。",
        ),
        interaction_hook="你做内容最累的，是没流量，还是不知道为谁写？",
        angle=EmotionAngle.GENTLE_REBELLION,
        topic="做内容的人正在被流量绑架",
        pain_point="深夜用更新数量对抗流量焦虑",
        viewpoint="增加发布数量之前，先弄清楚内容在替哪个具体的人说话。",
        source_urls=("https://x.com/WolfMoneyNotes/status/2077624635105980561",),
        rationale="命中副业创作者深夜看数据的场景。",
    )


def _create(packager: ContentPackager, tmp_path, index: int):
    return packager.create(
        _draft(),
        _audience(),
        tmp_path,
        source_refs=(),
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        runtime_run_id=f"run-{index}",
        today=date(2026, 8, 26),
    )


def test_three_packages_same_day_are_three_distinct_experiments(tmp_path) -> None:
    packager = ContentPackager()
    packages = [_create(packager, tmp_path, index) for index in range(3)]
    assert [package.root.name for package in packages] == ["exp-01", "exp-02", "exp-03"]
    experiment_ids = {
        json.loads(package.experiment_file.read_text(encoding="utf-8"))["experiment_id"]
        for package in packages
    }
    assert experiment_ids == {
        "2026-08-26-exp-01",
        "2026-08-26-exp-02",
        "2026-08-26-exp-03",
    }
    for package in packages:
        assert {item.name for item in package.root.iterdir()} == {
            "experiment.json",
            "tietu.json",
            "tietu.md",
            "preview.html",
        }


def test_package_experiment_record_carries_full_provenance(tmp_path) -> None:
    package = _create(ContentPackager(), tmp_path, 0)
    experiment = json.loads(package.experiment_file.read_text(encoding="utf-8"))
    assert experiment["experiment_id"] == "2026-08-26-exp-01"
    assert experiment["objective"] == "reads"
    assert experiment["candidate_id"] is None
    assert experiment["pattern_id"] is None
    assert experiment["runtime_run_id"] == "run-0"
    assert experiment["strategy"] == {
        "angle": "gentle_rebellion",
        "topic_mode": "provided_topics",
    }
    assert experiment["source_refs"] == []
    assert experiment["draft"]["title"] == _draft().title
    assert experiment["created_at"] == "2026-08-26T12:00:00Z"
    restored = Experiment.model_validate_json(
        package.experiment_file.read_text(encoding="utf-8")
    )
    assert restored.experiment_id == "2026-08-26-exp-01"
    assert restored.created_at == datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def test_slot_collision_raises_instead_of_overwriting(tmp_path) -> None:
    packager = ContentPackager()
    day_dir = tmp_path / "2026-08-26"
    day_dir.mkdir(parents=True, exist_ok=False)
    (day_dir / "exp-01").write_text("stray file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="已被占用"):
        _create(packager, tmp_path, 0)


@pytest.mark.parametrize(
    ("objective", "primary_label"),
    [
        (ContentObjective.READS, "阅读数"),
        (ContentObjective.FOLLOWS, "新增关注"),
        (ContentObjective.DISCUSSION, "评论数"),
        (ContentObjective.CONVERSION, "后端转化数"),
    ],
)
def test_markdown_backfill_section_reflects_objective(
    tmp_path, objective, primary_label
) -> None:
    audience = _audience().model_copy(update={"objective": objective})
    package = ContentPackager().create(
        _draft(),
        audience,
        tmp_path,
        source_refs=(),
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        runtime_run_id="run-md",
        today=date(2026, 8, 26),
    )
    markdown = package.markdown.read_text(encoding="utf-8")
    assert f"- 主指标：{primary_label}" in markdown
    assert "不要填 0" in markdown
```

- [ ] **Step 2: 更新旧 packager 测试到新布局**

`tests/test_wechat_traffic_agent.py` 顶部把 `from datetime import date` 改为：

```python
from datetime import date, datetime, timezone
```

把 `test_packager_creates_copy_ready_files` 整个函数替换为：

```python
def test_packager_creates_copy_ready_files(tmp_path) -> None:
    package = ContentPackager().create(
        _draft(),
        _audience(),
        tmp_path,
        source_refs=(),
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        runtime_run_id="run-test",
        today=date(2026, 8, 26),
    )
    assert package.root == tmp_path / "2026-08-26" / "exp-01"
    assert {item.name for item in package.root.iterdir()} == {
        "experiment.json",
        "tietu.md",
        "tietu.json",
        "preview.html",
    }
    assert "别让副业偷走你的下班" in package.markdown.read_text(encoding="utf-8")
    assert json.loads(package.json_file.read_text(encoding="utf-8"))["topic"] == _topic().title
    assert "你做内容最累的" in package.preview_html.read_text(encoding="utf-8")
    experiment = json.loads(package.experiment_file.read_text(encoding="utf-8"))
    assert experiment["experiment_id"] == "2026-08-26-exp-01"
    assert experiment["runtime_run_id"] == "run-test"
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_experiment_packager.py tests/test_wechat_traffic_agent.py::test_packager_creates_copy_ready_files -v`
Expected: 全部 FAIL（`create()` 不接受 `source_refs` 等参数，且尚无 `experiment.json`）

- [ ] **Step 4: 重写 packaging.py**

整个文件替换为：

```python
"""Create a copy-ready, local content package with no external side effects."""
from __future__ import annotations

import html
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .experiments import Experiment, ExperimentLedger
from .models import AudienceProfile, ContentPackage, TietuDraft
from .objectives import ObjectiveMetric, policy_for


class ContentPackager:
    def __init__(self, ledger: ExperimentLedger | None = None) -> None:
        self.ledger = ledger or ExperimentLedger()

    def create(
        self,
        draft: TietuDraft,
        audience: AudienceProfile,
        output_root: Path,
        *,
        source_refs: tuple[dict[str, Any], ...],
        strategy: dict[str, Any],
        created_at: datetime,
        runtime_run_id: str,
        today: date | None = None,
    ) -> ContentPackage:
        experiment_id, target = self.ledger.allocate(output_root, today or date.today())
        try:
            target.mkdir(parents=False, exist_ok=False)  # 槽位冲突直接失败，绝不覆盖
        except FileExistsError:
            raise RuntimeError(
                f"实验槽位 {experiment_id} 已被占用，拒绝覆盖"
            ) from None
        markdown = target / "tietu.md"
        json_file = target / "tietu.json"
        experiment_file = target / "experiment.json"
        preview = target / "preview.html"
        try:
            markdown.write_text(_markdown(draft, audience), encoding="utf-8")
            json_file.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
            preview.write_text(_preview(draft), encoding="utf-8")
            # experiment.json 最后写：文件存在即代表槽位完整（完成标记）
            experiment = Experiment(
                experiment_id=experiment_id,
                created_at=created_at,
                audience=audience,
                objective=audience.objective,
                strategy=strategy,
                draft=draft,
                source_refs=source_refs,
                runtime_run_id=runtime_run_id,
            )
            experiment_file.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)  # 不留半写入槽位
            raise
        return ContentPackage(
            root=target,
            markdown=markdown,
            json_file=json_file,
            experiment_file=experiment_file,
            preview_html=preview,
        )


_METRIC_LABELS = {
    ObjectiveMetric.READS: "阅读数",
    ObjectiveMetric.FOLLOWS: "新增关注",
    ObjectiveMetric.DISCUSSIONS: "评论数",
    ObjectiveMetric.SHARES: "分享数",
    ObjectiveMetric.CONVERSIONS: "后端转化数",
}


def _markdown(draft: TietuDraft, audience: AudienceProfile) -> str:
    sources = "\n".join(f"- {url}" for url in draft.source_urls) or "- 无外部事实引用"
    policy = policy_for(audience.objective)
    primary = _METRIC_LABELS[policy.primary_metric]
    secondary = "、".join(_METRIC_LABELS[metric] for metric in policy.secondary_metrics)
    return f"""# 题图内容包

## 复制到公众号

标题：{draft.title}

文字海报：

{draft.poster_text}

描述/互动句：

{draft.interaction_hook}

## 本轮实验记录

- 人群：{audience.people}
- 场景：{audience.scene}
- 持续需求：{audience.recurring_need}
- 目标：{audience.objective.value}
- 选题：{draft.topic}
- 痛点：{draft.pain_point}
- 观点：{draft.viewpoint}
- 情绪角度：{draft.angle.value}
- 选择理由：{draft.rationale}

## 回填指标（反馈学习用）

- 主指标：{primary}
- 次要指标：{secondary}
- 拿不到的指标在 history.json 里留 null，不要填 0

## 样本来源

{sources}

## 手动发布检查

- [ ] 标题不超过 20 字
- [ ] 文字海报只有一个痛点和一个观点
- [ ] 没有照搬样本文案
- [ ] 没有恐慌、孤独收割或强诱导互动
- [ ] 在公众号题图编辑器中选择“文字海报”生成题图
- [ ] 预览无误后再保存或发表
"""


def _preview(draft: TietuDraft) -> str:
    lines = "".join(f"<p>{html.escape(line)}</p>" for line in draft.lines)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(draft.title)}</title>
<style>body{{font-family:system-ui;background:#f4f1ec;color:#25221f}}main{{max-width:520px;margin:48px auto;padding:44px 34px;background:#fff;border-radius:18px;box-shadow:0 12px 40px #0001}}h1{{font-size:26px}}p{{font-size:20px;line-height:1.8;margin:10px 0}}footer{{margin-top:28px;color:#766;font-size:16px}}</style></head>
<body><main><h1>{html.escape(draft.title)}</h1>{lines}<footer>{html.escape(draft.interaction_hook)}</footer></main></body></html>"""
```

- [ ] **Step 5: service.py 透传新参数（保持运行时链路不红）**

`service.py` 的 import 区加：

```python
from datetime import date, datetime, timezone
from uuid import uuid4
```

（`from pathlib import Path` 与 `import json` 已存在。）把 `create_content_package` 整个方法替换为：

```python
    async def create_content_package(
        self,
        topics: list[TopicSignal],
        history: list[PerformanceObservation],
        audience: AudienceProfile,
        output_root: Path,
        *,
        today: date | None = None,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> tuple[TietuDraft, ContentPackage]:
        draft = await self.compose(topics, history, audience)
        strategy = {"angle": draft.angle.value, "topic_mode": "provided_topics"}
        return draft, self.packager.create(
            draft,
            audience,
            output_root,
            source_refs=tuple(item.model_dump(mode="json") for item in topics),
            strategy=strategy,
            created_at=now or datetime.now(timezone.utc),
            runtime_run_id=run_id or uuid4().hex,
            today=today,
        )
```

（`compose` 与 `_composition_task` 的 policy 接线是 T5 的事，本任务不碰。）

- [ ] **Step 6: 运行全量确认通过**

Run: `python -m pytest -q`
Expected: `25 passed in ...`（含 Task 3 的 3 项与 Task 4 的 7 项新测试、更新的旧 packager 测试）

- [ ] **Step 7: Commit**

```bash
git add src/wechat_traffic_agent/packaging.py src/wechat_traffic_agent/service.py tests/test_experiment_packager.py tests/test_wechat_traffic_agent.py
git commit -m "feat: immutable per-experiment ledger with exp-NN slots

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: service 接线（objective guidance + strategy + run id）

**Files:**
- Modify: `src/wechat_traffic_agent/service.py`
- Modify: `src/wechat_traffic_agent/__init__.py`（补充导出 `ContentObjective`——原计划遗漏，测试从包顶层导入与既有测试风格一致；仅 2 行：import + `__all__`，按字母序）
- Test: `tests/test_wechat_traffic_agent.py`（新增 prompt 反映 objective 的测试）

- [ ] **Step 1: 写失败测试**

`tests/test_wechat_traffic_agent.py` 的 `from wechat_traffic_agent import (...)` 中加 `ContentObjective`，文件末尾加：

```python
@pytest.mark.parametrize(
    ("objective", "guidance_token"),
    [
        (ContentObjective.READS, "提高阅读与完读表现"),
        (ContentObjective.FOLLOWS, "提高关注转化"),
        (ContentObjective.DISCUSSION, "引发讨论"),
        (ContentObjective.CONVERSION, "后端转化"),
    ],
)
def test_composition_prompt_reflects_objective_policy(
    tmp_path, objective, guidance_token
) -> None:
    audience = _audience().model_copy(update={"objective": objective})
    selected_angle = TrafficLearner().recommend([], objective)
    response = _draft(angle=selected_angle).model_dump_json()
    runtime, backend = _runtime(tmp_path, response)
    asyncio.run(WeChatTrafficAgent(runtime).compose([_topic()], [], audience))
    request = backend.requests[-1]
    assert guidance_token in request.task
    assert "主指标" in request.task
    assert "次要指标：。" not in request.task  # 次要指标必须非空
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_wechat_traffic_agent.py::test_composition_prompt_reflects_objective_policy -v`
Expected: FAIL（prompt 仍是固定 reads 文案）

- [ ] **Step 3: 实现 service.py**

`service.py` 的 import 区在 `.packaging` import 后加一行（`date/datetime/timezone/uuid4` 已在 T4 加入）：

```python
from .objectives import ObjectivePolicy, policy_for
```

`compose` 方法体前两行改为：

```python
        policy = policy_for(audience.objective)
        angle = self.learner.recommend(history, audience.objective)
        raw = await self.runtime.cognitive_probe(
            _composition_task(topics, history, angle.value, audience, policy),
            max_tokens=900,
        )
```

`create_content_package` 已在 T4 完成新签名与实验记录透传，本任务不动它。

`_composition_task` 的签名加 `policy: ObjectivePolicy` 参数，函数体内在 `recent` 之后加：

```python
    secondary = "、".join(metric.value for metric in policy.secondary_metrics)
```

并把 return 的 f-string 中这行：

```
目标是提高真实点击和阅读意愿；不得虚构事实、冒用真人经历、制造恐慌、利用孤独焦虑收割、羞辱读者、强迫互动或作结果保证。
```

替换为：

```
{policy.guidance}
本实验主指标：{policy.primary_metric.value}；次要指标：{secondary}。主指标拿不到时宁可留空也不要编造。
不得虚构事实、冒用真人经历、制造恐慌、利用孤独焦虑收割、羞辱读者、强迫互动或作结果保证。
```

其余行（含"只写文案""低粉高互动"）保持不变。

- [ ] **Step 4: 运行全量确认通过**

Run: `python -m pytest -q`
Expected: `29 passed in ...`（26 − 1 单项 + 4 参数化）

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/service.py src/wechat_traffic_agent/__init__.py tests/test_wechat_traffic_agent.py
git commit -m "feat: wire ObjectivePolicy into composition and experiment records

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: README 更新 + 全量验收

**Files:**
- Modify: `README.md`（输出目录节与反馈学习节）

- [ ] **Step 1: 更新 README 输出目录节**

把 README 中：

````markdown
输出目录：

```text
output/YYYY-MM-DD/
  tietu.md       # 直接复制与手动发布说明
  tietu.json     # 结构化记录，后续反馈学习使用
  preview.html   # 本地文字预览
```
````

替换为：

````markdown
输出目录（每次运行产生一个不可变实验槽位，同日多次运行互不覆盖）：

```text
output/YYYY-MM-DD/exp-NN/
  experiment.json  # 实验记录：experiment_id/created_at/audience/objective/
                   # candidate_id/pattern_id/strategy/draft/source_refs/runtime_run_id
  tietu.md         # 直接复制与手动发布说明
  tietu.json       # 题图文案结构化记录
  preview.html     # 本地文字预览
```
````

并在"反馈学习"节末尾追加：

```markdown
每次实验的目标（`audience.json` 里的 `objective`：reads/follows/discussion/conversion）
通过 `ObjectivePolicy` 决定组合指导、主次指标与奖励计算。回填 `PerformanceObservation`
时拿不到的指标留 `null`——系统把缺失视为"未知"而不是 0，避免学习器把
"没数据"误判成"表现差"。
```

- [ ] **Step 2: 全量验收**

Run:
```bash
python -m pytest -q
git status --short
git log --oneline -8
```
Expected:
- `29 passed in ...`
- 工作树干净（`docs/` 与源码均已提交）
- 分支 `feat/wechat-experiment-ledger` 上有 6 个左右小 commit（docs + 4 个 feat/chore + README）

- [ ] **Step 3: PR1 验收清单核对（对照用户规格）**

- [ ] 同一天连续运行 3 次产生 3 个 experiment：`test_three_packages_same_day_are_three_distinct_experiments` ✓
- [ ] 旧 9 项测试继续通过：全量 `29 passed` 包含原 9 项（其中 packager 一项按新布局更新）✓
- [ ] 不同 objective 得到不同 reward policy：`test_objectives_yield_different_rewards_for_the_same_observation` ✓
- [ ] 缺失指标不当作 0：`test_missing_secondary_metric_renormalizes_remaining_weights`、`test_primary_only_with_all_secondaries_missing_equals_primary_rate`、`test_all_metrics_missing_is_unknown_not_zero`、`test_learner_treats_unavailable_primary_metric_as_unexplored` ✓
- [ ] 版本一致：`test_agent_version_matches_package_version`（0.3.0）✓
- [ ] experiment 不可变字段齐全：`test_experiment_record_is_frozen_and_carries_full_provenance` ✓

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document experiment ledger layout and objective policies

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## PR1 完成后的衔接（不在本计划内）

PR1 合并并验收后，按用户规格顺序另写计划：

- **PR2** `feat/wechat-internet-sources`：`HotItem`/`ContentSnapshot`/`EngagementSnapshot`/`AuthorSnapshot` DTO（缺失一律 `None`）+ `HotFeedProvider`/`ContentReader`/`CommentReader`/`AuthorReader` 四个极薄协议 + `DailyHotApiProvider`/`AgentReachProvider`/`FixtureProvider` + `SourceResult` 故障隔离（单平台失败不中断）。写计划时先抓取 Agent-Reach llms.txt 与 DailyHotApi README 确定真实 API 形状。
- **PR3** `feat/wechat-viral-radar`：`ViralRadar`（不复用 `ContentOpportunity`）、deterministic 平台内百分位打分（rank 35% / engagement 25% / breakout 25% / freshness 15%，缺失项重归一化）、absolute popularity 与 relative breakout 分列保存、LLM 不参与数字比较。

在这两个 PR 完成并检视真实采集结果之前，不碰 `opportunity-agent`、不做自动发布、不创建公共互联网仓库。
