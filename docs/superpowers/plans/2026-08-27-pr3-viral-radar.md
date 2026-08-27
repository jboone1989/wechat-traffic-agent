# PR3 Viral Radar 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 `ViralRadar` 扫描管线——热榜广撒网 → normalize/dedup → platform-local 百分位打分 → Top-N 深抓（正文 + 作者 + 互动）→ `ViralCandidate`；数字排序全部 deterministic，LLM 不碰任何数字；全管线先用 FixtureProvider 离线建好。

**Architecture:** 新增两个模块：`scoring.py`（纯函数：percentile / dedup / 权重合成，无 IO，单独单测）与 `viral_radar.py`（`ViralCandidate`/`ScanReport` 模型 + `ViralRadar` 管线）。`sources.py` 增加 `EngagementReader` 协议（PR2 只定义了 `EngagementSnapshot` 契约，本 PR 把第一个 producer 落在 deep fetch 上，离线 producer 是 FixtureProvider）。`fixture.py` 扩充到 128 条 hot items 并埋入精心设计的深抓场景（低粉爆款、纯大号普通内容、缺失互动、缺失粉丝数、同平台同题、跨平台同题）。`agent_reach.py` 接真实平台 CLI 模板（bilibili 的 comments/author）并支持共享 httpx client 深抓。

**Tech Stack:** Python 3.11、pydantic v2、httpx、asyncio。测试全部离线（fixture / MockTransport / 假 runner），无网络、无子进程。

**基线（开始前确认）：** `main` @ `8980b89`，`python -m pytest -q` → `59 passed`，工作树干净。分支：`feat/wechat-viral-radar`。

**已核实的外部事实（2026-08-27 抓取 OSS 文档）：**
- Agent-Reach 是 scaffolding 不是框架：实际读取由 Agent 直接调用上游 CLI（twitter-cli / rdt-cli / xhs-cli / bilibili-cli / yt-dlp 等）完成；`agent-reach doctor` 只做健康检查。
- bilibili-cli（public-clis/bilibili-cli）文档化的命令：`bili video {id} --comments --json`（视频详情 + 热门评论，规范化 JSON envelope）、`bili user {author_id} --json`（UP 主资料）。SCHEMA.md 定义 envelope：成功 `{ok: true, schema_version: "1", data: {...}}`，失败 `{ok: false, error: {code, message}}`；`video` 的 data 含 `data.comments`，错误码如 `not_found`。
- xhs-cli 有文档化命令 `xhs comments NOTE_ID`，但输出格式未文档化 → **不给默认模板**（避免猜 API），用户可用 `commands={"comments_xiaohongshu": [...]}` 注入。
- weibo 渠道能力（热搜/用户/评论）在 Agent-Reach README 有列出，但上游 CLI 与输出形状均未文档化 → 同上，configurable-only。

**设计决定（实现者不需要重新决策）：**
1. **权重与重归一化**：rank 35% / engagement 25% / breakout 25% / freshness 15%；某子分缺失 → 从加权平均中剔除该项并把剩余权重归一化，绝不把 unavailable 当 0。全部子分缺失 → `final_score=None`（排序时排在最后）。
2. **platform-local percentile**（average-rank，ties 取中位）：所有子分只在同平台内比较；不同平台 raw 值永不横比。单元素 population 得 1.0（它就是平台内最佳）。
3. **engagement_value = likes + comments + shares + saves（不含 views）**：views 是 reach 不是互动，且并非所有平台暴露；views 原样透传到 `ViralCandidate.views`。部分字段缺失时对在场字段求和（部分下界），全缺失 → None。
4. **breakout = engagement_value / author_followers**（"表现远超账号自身分发能力"）；followers 缺失或 ≤0 → None。absolute_hot_score 单独保存（raw_hot_value 的平台内百分位），与 breakout 分开。
5. **Top-N 选取用 pre-score = rank_score**（此时只有热榜数据）；深抓后按 final_score 重排。engagement/breakout/freshness 的百分位 population = 该平台被深抓的候选集合（v1 已知限制，写入 docstring）。
6. **深抓并发**：所有 Top-N 候选 `asyncio.gather`；单候选内部 read → author → engagement 串行（author_id 依赖 read）。任何叶子失败只记入 `errors`，不中断。
7. **radar 深抓不调 comments**：PR3 规格只要 content + author + engagement；comments 的 CLI 模板在 provider 层接好（Task 5），PR4 消费。
8. **排序确定性**：final desc → rank asc → platform → candidate_id；两次 scan 输出完全一致（freshness 只用相对年龄，与墙钟无关）。
9. **PARTIAL 状态**：当前无 provider 产生它；radar 只把 OK 当可用，其余记入 PlatformScan（防御式）。
10. 规格中 `ViralCandidate.author` 实现为 `author_id: str | None`；额外声明 `rank_score`/`engagement_score` 子分字段（验收与调试需要）。

---

### Task 0: 建分支、确认基线

- [ ] **Step 1:** `git status --short` 确认干净（若出现非本会话产物，停下来向用户确认）
- [ ] **Step 2:** `git checkout -b feat/wechat-viral-radar`
- [ ] **Step 3:** `python -m pytest -q` → `59 passed in ...`

---

### Task 1: EngagementReader 协议 + FixtureProvider.engagement()

**Files:**
- Modify: `src/wechat_traffic_agent/sources.py`（文件末尾追加协议）
- Modify: `src/wechat_traffic_agent/fixture.py`（EngagementSnapshot import、FIXTURE_ENGAGEMENT、engagement() 方法）
- Test: `tests/test_sources.py`、`tests/test_fixture.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_sources.py` 追加（文件顶部 import 区补 `EngagementReader`）：

```python
def test_engagement_reader_protocol_is_structural() -> None:
    class _Reader:
        async def engagement(
            self, content_id: str, *, platform: str
        ) -> SourceResult[EngagementSnapshot]: ...

    reader: EngagementReader = _Reader()  # 结构化满足协议即可
```

`tests/test_fixture.py` 追加（顶部 import 区补 `EngagementSnapshot`）：

```python
def test_fixture_engagement_reads_canned_data() -> None:
    provider = FixtureProvider()
    result = asyncio.run(provider.engagement("wb-1", platform="weibo"))
    assert result.status is SourceStatus.OK
    assert result.source == "fixture"
    assert result.platform == "weibo"
    assert result.observed_at is not None
    assert result.items.likes == 8000


def test_fixture_engagement_missing_is_unavailable_and_broken_is_failed() -> None:
    provider = FixtureProvider()
    missing = asyncio.run(provider.engagement("not-in-fixture", platform="weibo"))
    assert missing.status is SourceStatus.UNAVAILABLE
    assert missing.error is not None
    assert missing.items == EngagementSnapshot(content_id="", platform="weibo")  # 退化实例纪律
    broken_provider = FixtureProvider(broken_platforms=frozenset({"weibo"}))
    failed = asyncio.run(broken_provider.engagement("wb-1", platform="weibo"))
    assert failed.status is SourceStatus.FAILED
    assert failed.error is not None
    assert failed.items == EngagementSnapshot(content_id="", platform="weibo")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_sources.py tests/test_fixture.py -q`
Expected: 新增 3 个测试 FAIL（协议不存在 / 方法不存在）

- [ ] **Step 3: 实现**

`src/wechat_traffic_agent/sources.py` 末尾（`AuthorReader` 之后）追加：

```python
class EngagementReader(Protocol):
    async def engagement(
        self, content_id: str, *, platform: str
    ) -> SourceResult[EngagementSnapshot]: ...
```

`src/wechat_traffic_agent/fixture.py`：
(a) import 行改为：

```python
from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    EngagementSnapshot,
    HotItem,
    SourceResult,
    SourceStatus,
)
```

(b) 在 `FIXTURE_AUTHORS` 之后新增：

```python
# PR3: deep fetch 的离线互动数据（producer 落在 deep fetch 上）。
FIXTURE_ENGAGEMENT: dict[str, EngagementSnapshot] = {
    "wb-1": EngagementSnapshot(
        content_id="wb-1", platform="weibo", views=250000, likes=8000, comments=900, shares=400
    ),
}
```

(c) `FixtureProvider` 内、`author` 方法之后新增（与叶子方法同构：broken → FAILED，缺数据 → UNAVAILABLE）：

```python
    async def engagement(
        self, content_id: str, *, platform: str
    ) -> SourceResult[EngagementSnapshot]:
        if platform in self._broken:
            return SourceResult(
                source="fixture",
                platform=platform,
                status=SourceStatus.FAILED,
                items=EngagementSnapshot(content_id="", platform=platform),
                error="fixture: platform marked broken",
                observed_at=self._observed(),
            )
        snapshot = FIXTURE_ENGAGEMENT.get(content_id)
        if snapshot is None:
            return self._unavailable(
                platform,
                f"fixture has no engagement for {content_id!r}",
                EngagementSnapshot(content_id="", platform=platform),
            )
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=snapshot,
            observed_at=self._observed(),
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_sources.py tests/test_fixture.py -q`
Expected: 全部通过；全量 `python -m pytest -q` → `62 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/sources.py src/wechat_traffic_agent/fixture.py tests/test_sources.py tests/test_fixture.py
git commit -m "feat: EngagementReader contract with offline fixture producer"
```

---

### Task 2: scoring.py 纯函数模块

**Files:**
- Create: `src/wechat_traffic_agent/scoring.py`
- Test: `tests/test_scoring.py`（新建）

- [ ] **Step 1: 写失败测试**

Create `tests/test_scoring.py`：

```python
from __future__ import annotations

import pytest

from wechat_traffic_agent.scoring import (
    breakout_ratio,
    combine_final,
    dedup_items,
    engagement_value,
    normalize_title,
    percentile_score,
    rank_score,
)
from wechat_traffic_agent.sources import EngagementSnapshot, HotItem


def test_percentile_score_orders_values_and_shares_ties() -> None:
    population = [10.0, 20.0, 20.0, 30.0]
    assert percentile_score(30.0, population) == pytest.approx(1.0)
    assert percentile_score(10.0, population) == pytest.approx(0.0)
    assert percentile_score(20.0, population) == pytest.approx(0.5)
    assert percentile_score(20.0, [20.0]) == pytest.approx(1.0)  # 单元素即平台内最佳


def test_rank_score_clamps_and_handles_missing() -> None:
    assert rank_score(1, 43) == pytest.approx(1.0)
    assert rank_score(43, 43) == pytest.approx(0.0)
    assert rank_score(99, 43) == pytest.approx(0.0)  # rank 超界 → 钳到 0
    assert rank_score(1, 1) == pytest.approx(1.0)
    assert rank_score(None, 43) is None


def test_engagement_value_sums_present_and_ignores_views() -> None:
    snapshot = EngagementSnapshot(
        content_id="c", platform="weibo", views=250000, likes=100, comments=20, shares=5, saves=3
    )
    assert engagement_value(snapshot) == 128
    assert engagement_value(EngagementSnapshot(content_id="c", platform="weibo")) is None
    assert engagement_value(EngagementSnapshot(content_id="c", platform="weibo", views=10)) is None


def test_breakout_ratio_requires_engagement_and_positive_followers() -> None:
    assert breakout_ratio(6000, 600) == pytest.approx(10.0)
    assert breakout_ratio(None, 600) is None
    assert breakout_ratio(6000, None) is None
    assert breakout_ratio(6000, 0) is None


def test_combine_final_renormalizes_missing_weights() -> None:
    # 只有 rank + engagement：权重 35/25 归一化为 0.5833/0.4167
    score = combine_final({"rank": 1.0, "engagement": 0.0})
    assert score == pytest.approx(0.35 / 0.60)


def test_combine_final_missing_is_not_zero() -> None:
    missing = combine_final({"rank": 0.8, "engagement": None, "freshness": 0.5})
    zeroed = combine_final({"rank": 0.8, "engagement": 0.0, "freshness": 0.5})
    assert missing is not None and zeroed is not None
    assert missing > zeroed  # 缺失剔除权重，而非按 0 计入


def test_combine_final_all_missing_is_none() -> None:
    assert combine_final({"rank": None, "engagement": None}) is None


def test_normalize_title_is_whitespace_and_case_insensitive() -> None:
    assert normalize_title("  Hello   World ") == normalize_title("helloworld")
    assert normalize_title("微博 样本 一") == normalize_title("微博样本一")


def test_dedup_items_keeps_best_rank_and_preserves_first_seen_order() -> None:
    items = (
        HotItem(source_id="a", platform="weibo", title="同题", rank=5),
        HotItem(source_id="b", platform="weibo", title=" 同题 ", rank=2),
        HotItem(source_id="c", platform="weibo", title="另一题", rank=1),
        HotItem(source_id="d", platform="weibo", title="同题", rank=9),
        HotItem(source_id="e", platform="weibo", title="无排名", rank=None),
    )
    kept = dedup_items(items)
    assert [item.source_id for item in kept] == ["b", "c", "e"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_scoring.py -q`
Expected: 全部 FAIL（模块不存在）

- [ ] **Step 3: 实现 scoring.py**

Create `src/wechat_traffic_agent/scoring.py`：

```python
"""Deterministic viral scoring primitives: no LLM ever touches numbers.

All sub-scores are platform-local percentiles, so raw platform metrics are
never compared cross-platform. Missing data stays missing: an unavailable
sub-score drops out of the weighted average and the remaining weights are
re-normalized (unavailable is never zero).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .sources import EngagementSnapshot, HotItem

WEIGHTS: dict[str, float] = {
    "rank": 0.35,
    "engagement": 0.25,
    "breakout": 0.25,
    "freshness": 0.15,
}


def normalize_title(title: str) -> str:
    """Whitespace-insensitive, case-insensitive dedup key."""
    return "".join(title.split()).casefold()


def percentile_score(value: float, population: Sequence[float]) -> float:
    """Average-rank percentile of ``value`` inside ``population`` (higher = better).

    Ties share the mid-percentile. A single-element population scores 1.0:
    that item is the platform-local best by definition.
    """
    n = len(population)
    if n <= 1:
        return 1.0
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    average_rank = below + (equal + 1) / 2.0  # 1-based, ascending
    return (average_rank - 1.0) / (n - 1.0)


def rank_score(rank: int | None, population_size: int) -> float | None:
    """Hot-list position strength, platform-local. Rank 1 is best."""
    if rank is None:
        return None
    if population_size <= 1:
        return 1.0
    score = (population_size - rank) / (population_size - 1)
    return max(0.0, min(1.0, score))


def engagement_value(snapshot: EngagementSnapshot) -> int | None:
    """Interaction magnitude: likes + comments + shares + saves.

    Views are excluded: reach is not engagement and not every platform
    exposes views. Partial fields sum to a lower bound; all-missing is None.
    """
    parts = [
        value
        for value in (snapshot.likes, snapshot.comments, snapshot.shares, snapshot.saves)
        if value is not None
    ]
    return sum(parts) if parts else None


def breakout_ratio(engagement: int | None, followers: int | None) -> float | None:
    """Performance versus the author's own distribution capability."""
    if engagement is None or followers is None or followers <= 0:
        return None
    return engagement / followers


def combine_final(subscores: Mapping[str, float | None]) -> float | None:
    """Weighted mean over available sub-scores, re-normalized over present weights."""
    available = {name: value for name, value in subscores.items() if value is not None}
    if not available:
        return None
    weight_sum = sum(WEIGHTS[name] for name in available)
    return sum(WEIGHTS[name] * value for name, value in available.items()) / weight_sum


def dedup_items(items: Sequence[HotItem]) -> tuple[HotItem, ...]:
    """Drop same-title duplicates within one platform, keeping the best rank."""
    best: dict[str, HotItem] = {}
    for item in items:
        key = normalize_title(item.title)
        current = best.get(key)
        if current is None or _better_rank(item, current):
            best[key] = item
    return tuple(best.values())


def _better_rank(candidate: HotItem, current: HotItem) -> bool:
    if candidate.rank is None:
        return False
    if current.rank is None:
        return True
    return candidate.rank < current.rank
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: `9 passed`；全量 `python -m pytest -q` → `71 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/scoring.py tests/test_scoring.py
git commit -m "feat: deterministic platform-local scoring primitives"
```

---

### Task 3: fixture 扩充（128 条 hot items + 精心设计的深抓场景）

**Files:**
- Modify: `src/wechat_traffic_agent/fixture.py`（bulk 生成 + 深数据）
- Test: `tests/test_fixture.py`（追加）

**场景设计表（实现者照抄，评审者对照验收）：**

| 内容 | 位置 | 设计意图 |
| --- | --- | --- |
| 低粉爆款 | weibo-5（600 粉，67000 互动）、zhihu-3（800 粉，500 互动）、bili-3（950 粉，8160 互动） | breakout/engagement 双满分 → 全局前三 |
| 纯大号普通内容 | weibo-4（250 万粉，32800 互动，3 天前） | 高绝对互动但 ratio 极低 → 输给低粉爆款 |
| 缺失互动 | weibo-6（无 engagement 条目） | 重归一化路径 |
| 缺失粉丝数 | weibo-7（作者 followers=None） | breakout 不可用路径 |
| 无 url 深抓 | wb-3（无 url，但有 engagement 条目） | engagement 走 source_id 兜底 |
| 同平台同题 | weibo-dup（rank 11 复制 weibo-10 标题） | dedup 保留 rank 更好的 |
| 跨平台同题 | weibo-4 与 zhihu-4 同题"跨平台同题：通勤效率观察" | cross_platform_score = 2/3 |
| 绝对热度缺失 | rank % 7 == 0 的 bulk 条目（含 weibo-7） | absolute_hot_score=None 路径 |

深抓范围内各条数据（点赞/评论/分享/收藏 → engagement_value；发布时间用于 freshness）：

- weibo：wb-1 8000/900/400（250000 views）；wb-2 12000/1500/600；wb-3 6000/700/250；weibo-4 30000/2000/800；weibo-5 52000/7800/3100+4100 saves；weibo-7 1500/100/50
- zhihu：zh-1 300/40/10；zh-2 90/12/3；zhihu-3 420/60/20；zhihu-4 200/25/8；zhihu-5 60/8/2；zhihu-6 260/33/9
- bilibili：bili-1 9000/1200/500；bili-2 3000/400/150；bili-3 7000/900/260；bili-4 4000/300/90；bili-5 2200/260/70；bili-6 1500/180/40；bili-7 800/95/20

- [ ] **Step 1: 写失败测试**

`tests/test_fixture.py` 追加（顶部 import 补 `FIXTURE_AUTHORS, FIXTURE_ENGAGEMENT, FIXTURE_HOT` 与 `from wechat_traffic_agent.scoring import normalize_title`）：

```python
def test_fixture_bulk_hot_reaches_100_items() -> None:
    total = sum(len(items) for items in FIXTURE_HOT.values())
    assert total >= 100
    # rank % 7 == 0 的 bulk 条目刻意缺失 raw_hot_value（绝对热度缺失路径）
    assert any(item.raw_hot_value is None for item in FIXTURE_HOT["weibo"])


def test_fixture_crafted_deep_fetch_scenarios_exist() -> None:
    # 低粉爆款 vs 纯大号普通内容
    assert FIXTURE_ENGAGEMENT["weibo-5"].likes == 52000
    assert FIXTURE_AUTHORS["fixture-weibo-author-5"].followers == 600
    assert FIXTURE_AUTHORS["fixture-weibo-author-4"].followers == 2_500_000
    # weibo-6 无互动数据（重归一化）；weibo-7 作者无粉丝数（breakout 缺失）
    assert "weibo-6" not in FIXTURE_ENGAGEMENT
    assert FIXTURE_AUTHORS["fixture-weibo-author-7"].followers is None
    # wb-3 无 url：engagement 需要走 source_id 兜底
    wb3 = next(item for item in FIXTURE_HOT["weibo"] if item.source_id == "wb-3")
    assert wb3.url is None
    assert "wb-3" in FIXTURE_ENGAGEMENT
    # 同平台重复标题 + 跨平台同题
    weibo_titles = [normalize_title(item.title) for item in FIXTURE_HOT["weibo"]]
    assert len(weibo_titles) > len(set(weibo_titles))
    assert normalize_title("跨平台同题：通勤效率观察") in {
        normalize_title(item.title) for item in FIXTURE_HOT["zhihu"]
    }
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_fixture.py -q`
Expected: 新增 2 个测试 FAIL（数据不存在）

- [ ] **Step 3: 实现 fixture 扩充**

`src/wechat_traffic_agent/fixture.py`：

(a) 现有 `wb-1` 的 ContentSnapshot 字面量追加 `published_at=datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc),`（freshness 场景）。

(b) `FIXTURE_AUTHORS` 字面量末尾追加（保留现有 `fixture-author-1` 条目）：

```python
FIXTURE_AUTHORS.update({
    "fixture-weibo-author-2": AuthorSnapshot(author_id="fixture-weibo-author-2", platform="weibo", followers=120000),
    "fixture-weibo-author-4": AuthorSnapshot(author_id="fixture-weibo-author-4", platform="weibo", followers=2_500_000),
    "fixture-weibo-author-5": AuthorSnapshot(author_id="fixture-weibo-author-5", platform="weibo", followers=600),
    "fixture-weibo-author-6": AuthorSnapshot(author_id="fixture-weibo-author-6", platform="weibo", followers=9000),
    "fixture-weibo-author-7": AuthorSnapshot(author_id="fixture-weibo-author-7", platform="weibo", followers=None),
    "fixture-zhihu-author-1": AuthorSnapshot(author_id="fixture-zhihu-author-1", platform="zhihu", followers=5000),
    "fixture-zhihu-author-2": AuthorSnapshot(author_id="fixture-zhihu-author-2", platform="zhihu", followers=900),
    "fixture-zhihu-author-3": AuthorSnapshot(author_id="fixture-zhihu-author-3", platform="zhihu", followers=800),
    "fixture-zhihu-author-4": AuthorSnapshot(author_id="fixture-zhihu-author-4", platform="zhihu", followers=30000),
    "fixture-zhihu-author-5": AuthorSnapshot(author_id="fixture-zhihu-author-5", platform="zhihu", followers=2000),
    "fixture-zhihu-author-6": AuthorSnapshot(author_id="fixture-zhihu-author-6", platform="zhihu", followers=4500),
    "fixture-bili-author-1": AuthorSnapshot(author_id="fixture-bili-author-1", platform="bilibili", followers=220000),
    "fixture-bili-author-2": AuthorSnapshot(author_id="fixture-bili-author-2", platform="bilibili", followers=15000),
    "fixture-bili-author-3": AuthorSnapshot(author_id="fixture-bili-author-3", platform="bilibili", followers=950),
    "fixture-bili-author-4": AuthorSnapshot(author_id="fixture-bili-author-4", platform="bilibili", followers=800000),
    "fixture-bili-author-5": AuthorSnapshot(author_id="fixture-bili-author-5", platform="bilibili", followers=5200),
    "fixture-bili-author-6": AuthorSnapshot(author_id="fixture-bili-author-6", platform="bilibili", followers=2600),
    "fixture-bili-author-7": AuthorSnapshot(author_id="fixture-bili-author-7", platform="bilibili", followers=60000),
})
```

(c) `FIXTURE_CONTENT` 字面量之后追加：

```python
FIXTURE_CONTENT.update({
    "https://fixture.example/weibo/2": ContentSnapshot(
        content_id="wb-2", platform="weibo", url="https://fixture.example/weibo/2",
        title="微博样本二", text="这是微博样本二的正文。", author_id="fixture-weibo-author-2",
        published_at=datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/4": ContentSnapshot(
        content_id="weibo-4", platform="weibo", url="https://fixture.example/weibo/4",
        title="跨平台同题：通勤效率观察", text="这是大号普通内容的正文，数据平稳。",
        author_id="fixture-weibo-author-4",
        published_at=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/5": ContentSnapshot(
        content_id="weibo-5", platform="weibo", url="https://fixture.example/weibo/5",
        title="weibo热点样本005", text="这是低粉爆款的正文，互动远超账号体量。",
        author_id="fixture-weibo-author-5",
        published_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/6": ContentSnapshot(
        content_id="weibo-6", platform="weibo", url="https://fixture.example/weibo/6",
        title="weibo热点样本006", text="这是无互动数据样本的正文。",
        author_id="fixture-weibo-author-6",
        published_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/weibo/7": ContentSnapshot(
        content_id="weibo-7", platform="weibo", url="https://fixture.example/weibo/7",
        title="weibo热点样本007", text="这是作者粉丝数未知样本的正文。",
        author_id="fixture-weibo-author-7",
        published_at=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
    ),
    "https://zhihu.com/fixture/1": ContentSnapshot(
        content_id="zh-1", platform="zhihu", url="https://zhihu.com/fixture/1",
        title="知乎样本一", text="这是知乎样本一的正文。", author_id="fixture-zhihu-author-1",
        published_at=datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc),
    ),
    "https://zhihu.com/fixture/2": ContentSnapshot(
        content_id="zh-2", platform="zhihu", url="https://zhihu.com/fixture/2",
        title="知乎样本二", text="这是知乎样本二的正文。", author_id="fixture-zhihu-author-2",
        published_at=datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/3": ContentSnapshot(
        content_id="zhihu-3", platform="zhihu", url="https://fixture.example/zhihu/3",
        title="zhihu热点样本003", text="这是知乎低粉爆款的正文。", author_id="fixture-zhihu-author-3",
        published_at=datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/4": ContentSnapshot(
        content_id="zhihu-4", platform="zhihu", url="https://fixture.example/zhihu/4",
        title="跨平台同题：通勤效率观察", text="这是知乎跨平台同题的正文。",
        author_id="fixture-zhihu-author-4",
        published_at=datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/5": ContentSnapshot(
        content_id="zhihu-5", platform="zhihu", url="https://fixture.example/zhihu/5",
        title="zhihu热点样本005", text="这是知乎样本五的正文。", author_id="fixture-zhihu-author-5",
        published_at=datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc),
    ),
    "https://fixture.example/zhihu/6": ContentSnapshot(
        content_id="zhihu-6", platform="zhihu", url="https://fixture.example/zhihu/6",
        title="zhihu热点样本006", text="这是知乎样本六的正文。", author_id="fixture-zhihu-author-6",
        published_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/1": ContentSnapshot(
        content_id="bili-1", platform="bilibili", url="https://bilibili.com/fixture/1",
        title="B站样本一", text="这是B站样本一的正文。", author_id="fixture-bili-author-1",
        published_at=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/2": ContentSnapshot(
        content_id="bili-2", platform="bilibili", url="https://bilibili.com/fixture/2",
        title="B站样本二", text="这是B站样本二的正文。", author_id="fixture-bili-author-2",
        published_at=datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/3": ContentSnapshot(
        content_id="bili-3", platform="bilibili", url="https://bilibili.com/fixture/3",
        title="bilibili热点样本003", text="这是B站低粉爆款的正文。", author_id="fixture-bili-author-3",
        published_at=datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/4": ContentSnapshot(
        content_id="bili-4", platform="bilibili", url="https://bilibili.com/fixture/4",
        title="bilibili热点样本004", text="这是B站样本四的正文。", author_id="fixture-bili-author-4",
        published_at=datetime(2026, 8, 24, 22, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/5": ContentSnapshot(
        content_id="bili-5", platform="bilibili", url="https://bilibili.com/fixture/5",
        title="bilibili热点样本005", text="这是B站样本五的正文。", author_id="fixture-bili-author-5",
        published_at=datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/6": ContentSnapshot(
        content_id="bili-6", platform="bilibili", url="https://bilibili.com/fixture/6",
        title="bilibili热点样本006", text="这是B站样本六的正文。", author_id="fixture-bili-author-6",
        published_at=datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc),
    ),
    "https://bilibili.com/fixture/7": ContentSnapshot(
        content_id="bili-7", platform="bilibili", url="https://bilibili.com/fixture/7",
        title="bilibili热点样本007", text="这是B站样本七的正文。", author_id="fixture-bili-author-7",
        published_at=datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc),
    ),
})
```

(d) `FIXTURE_ENGAGEMENT`（Task 1 建立的字典）改为完整数据：

```python
FIXTURE_ENGAGEMENT: dict[str, EngagementSnapshot] = {
    "wb-1": EngagementSnapshot(content_id="wb-1", platform="weibo", views=250000, likes=8000, comments=900, shares=400),
    "wb-2": EngagementSnapshot(content_id="wb-2", platform="weibo", likes=12000, comments=1500, shares=600),
    "wb-3": EngagementSnapshot(content_id="wb-3", platform="weibo", likes=6000, comments=700, shares=250),
    "weibo-4": EngagementSnapshot(content_id="weibo-4", platform="weibo", likes=30000, comments=2000, shares=800),
    "weibo-5": EngagementSnapshot(content_id="weibo-5", platform="weibo", likes=52000, comments=7800, shares=3100, saves=4100),
    "weibo-7": EngagementSnapshot(content_id="weibo-7", platform="weibo", likes=1500, comments=100, shares=50),
    "zh-1": EngagementSnapshot(content_id="zh-1", platform="zhihu", likes=300, comments=40, shares=10),
    "zh-2": EngagementSnapshot(content_id="zh-2", platform="zhihu", likes=90, comments=12, shares=3),
    "zhihu-3": EngagementSnapshot(content_id="zhihu-3", platform="zhihu", likes=420, comments=60, shares=20),
    "zhihu-4": EngagementSnapshot(content_id="zhihu-4", platform="zhihu", likes=200, comments=25, shares=8),
    "zhihu-5": EngagementSnapshot(content_id="zhihu-5", platform="zhihu", likes=60, comments=8, shares=2),
    "zhihu-6": EngagementSnapshot(content_id="zhihu-6", platform="zhihu", likes=260, comments=33, shares=9),
    "bili-1": EngagementSnapshot(content_id="bili-1", platform="bilibili", likes=9000, comments=1200, shares=500),
    "bili-2": EngagementSnapshot(content_id="bili-2", platform="bilibili", likes=3000, comments=400, shares=150),
    "bili-3": EngagementSnapshot(content_id="bili-3", platform="bilibili", likes=7000, comments=900, shares=260),
    "bili-4": EngagementSnapshot(content_id="bili-4", platform="bilibili", likes=4000, comments=300, shares=90),
    "bili-5": EngagementSnapshot(content_id="bili-5", platform="bilibili", likes=2200, comments=260, shares=70),
    "bili-6": EngagementSnapshot(content_id="bili-6", platform="bilibili", likes=1500, comments=180, shares=40),
    "bili-7": EngagementSnapshot(content_id="bili-7", platform="bilibili", likes=800, comments=95, shares=20),
}
```

(e) 文件末尾（类定义之后）追加 bulk 生成，让 `FIXTURE_HOT` 扩到 128 条：

```python
# ---------------------------------------------------------------------------
# PR3: bulk hot data（确定性生成，离线 ≥100 条）+ 深抓场景。
# 低粉爆款：weibo-5 / zhihu-3 / bili-3；纯大号普通内容：weibo-4。
_BULK_COUNT = 40

_CROSS_PLATFORM_TITLE = "跨平台同题：通勤效率观察"
_TITLE_OVERRIDES: dict[tuple[str, int], str] = {
    ("weibo", 4): _CROSS_PLATFORM_TITLE,
    ("zhihu", 4): _CROSS_PLATFORM_TITLE,
}


def _bulk_hot(platform: str, start_rank: int, count: int) -> tuple[HotItem, ...]:
    items: list[HotItem] = []
    denominator = start_rank + count + 20
    for offset in range(count):
        rank = start_rank + offset
        raw_hot = None if rank % 7 == 0 else round(10000.0 * (1.0 - rank / denominator), 1)
        title = _TITLE_OVERRIDES.get((platform, rank), f"{platform}热点样本{rank:03d}")
        items.append(
            HotItem(
                source_id=f"{platform}-{rank}",
                platform=platform,
                title=title,
                url=f"https://fixture.example/{platform}/{rank}",
                rank=rank,
                raw_hot_value=raw_hot,
            )
        )
    return tuple(items)


FIXTURE_HOT["weibo"] = (
    FIXTURE_HOT["weibo"]
    + _bulk_hot("weibo", 4, _BULK_COUNT)
    + (
        # 同平台同题（rank 11 > weibo-10 的 rank 10 → dedup 时被丢弃）
        HotItem(
            source_id="weibo-dup", platform="weibo", title="weibo热点样本010",
            url="https://fixture.example/weibo/dup", rank=11, raw_hot_value=500.0,
        ),
    )
)
FIXTURE_HOT["zhihu"] = FIXTURE_HOT["zhihu"] + _bulk_hot("zhihu", 3, _BULK_COUNT)
FIXTURE_HOT["bilibili"] = FIXTURE_HOT["bilibili"] + _bulk_hot("bilibili", 3, _BULK_COUNT)
```

注意：bulk 生成放在 `FixtureProvider` 类定义之后执行也可以（模块级数据，与类无关）；但 `FIXTURE_CONTENT.update(...)` 等必须保持在类定义之前的原位置（它们是对既有字面量的追加）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_fixture.py -v`
Expected: 全部通过（含 PR2 旧测试——它们只加不破）；全量 `python -m pytest -q` → `73 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/fixture.py tests/test_fixture.py
git commit -m "feat: fixture bulk hot data with crafted deep-fetch scenarios"
```

---

### Task 4: viral_radar.py（ViralCandidate + ScanReport + ViralRadar）

**Files:**
- Create: `src/wechat_traffic_agent/viral_radar.py`
- Test: `tests/test_viral_radar.py`（新建）

**fixture 场景下的期望值（评审对照；实现者不需要断言全部，只断言测试里的关系）：**
- 全局前三 = weibo:weibo-5（≈0.967）、zhihu:zhihu-3（≈0.953）、bilibili:bili-3（≈0.916）
- weibo:weibo-4（大号）final ≈ 0.555，breakout_score = 0.0；weibo:weibo-5 breakout_score = 1.0
- Top-20 深抓范围 = weibo ranks 1–7、zhihu ranks 1–6、bilibili ranks 1–7
- weibo PlatformScan：items_fetched=44、items_after_dedup=43

- [ ] **Step 1: 写失败测试**

Create `tests/test_viral_radar.py`：

```python
from __future__ import annotations

import asyncio

import httpx
import pytest

from wechat_traffic_agent.dailyhot import DailyHotApiProvider
from wechat_traffic_agent.fixture import FixtureProvider
from wechat_traffic_agent.sources import SourceStatus
from wechat_traffic_agent.viral_radar import ViralRadar

PLATFORMS = ("weibo", "zhihu", "bilibili")


def _scan(*, top_n: int = 20, broken: frozenset[str] = frozenset()):
    provider = FixtureProvider(broken_platforms=broken)
    radar = ViralRadar(hot_provider=provider, reader=provider, platforms=PLATFORMS, top_n=top_n)
    return asyncio.run(radar.scan())


def _by_id(report):
    return {candidate.candidate_id: candidate for candidate in report.candidates}


def test_scan_over_100_fixture_items_outputs_top_n_stably() -> None:
    report = _scan()
    assert sum(p.items_fetched for p in report.platforms) >= 100
    assert len(report.candidates) == 20
    again = _scan()
    assert [c.candidate_id for c in again.candidates] == [c.candidate_id for c in report.candidates]
    assert [c.final_score for c in again.candidates] == [c.final_score for c in report.candidates]


def test_low_follower_breakout_beats_big_account_normal_content() -> None:
    report = _scan()
    candidates = _by_id(report)
    breakout = candidates["weibo:weibo-5"]  # 600 粉丝、6.7 万互动
    big_account = candidates["weibo:weibo-4"]  # 250 万粉丝、3.28 万互动的普通内容
    assert breakout.author_followers == 600
    assert big_account.author_followers == 2_500_000
    assert breakout.breakout_score == pytest.approx(1.0)
    assert big_account.breakout_score == pytest.approx(0.0)
    assert breakout.final_score is not None and big_account.final_score is not None
    assert breakout.final_score > big_account.final_score
    assert breakout.final_score > candidates["weibo:wb-1"].final_score
    assert report.candidates[0].candidate_id == "weibo:weibo-5"
    # 全局前三都是各自平台的中低粉爆款
    assert {c.candidate_id for c in report.candidates[:3]} == {
        "weibo:weibo-5", "zhihu:zhihu-3", "bilibili:bili-3",
    }


def test_raw_engagement_is_never_compared_cross_platform() -> None:
    report = _scan()
    candidates = _by_id(report)
    small = candidates["zhihu:zhihu-3"]  # 500 互动即知乎第一
    big = candidates["weibo:weibo-5"]  # 6.7 万互动
    assert (small.likes or 0) < (big.likes or 0)
    assert small.engagement_score == pytest.approx(1.0)
    assert big.engagement_score == pytest.approx(1.0)


def test_missing_engagement_renormalizes_instead_of_zero() -> None:
    report = _scan()
    candidates = _by_id(report)
    missing = candidates["weibo:weibo-6"]  # 无互动数据
    lowest = candidates["weibo:weibo-7"]  # 平台内互动最低
    assert missing.engagement_score is None
    assert lowest.engagement_score == pytest.approx(0.0)
    assert missing.final_score is not None and lowest.final_score is not None
    assert missing.final_score > lowest.final_score


def test_platform_failure_does_not_abort_scan() -> None:
    report = _scan(broken=frozenset({"weibo"}))
    statuses = {p.platform: p.status for p in report.platforms}
    assert statuses["weibo"] is SourceStatus.FAILED
    assert statuses["zhihu"] is SourceStatus.OK
    assert report.candidates
    assert {c.platform for c in report.candidates} == {"zhihu", "bilibili"}


def test_all_platforms_broken_returns_empty_report() -> None:
    report = _scan(broken=frozenset(PLATFORMS))
    assert report.candidates == ()
    assert all(p.status is SourceStatus.FAILED for p in report.platforms)


def test_top_n_is_respected() -> None:
    report = _scan(top_n=5)
    assert len(report.candidates) == 5


def test_dedup_and_cross_platform_scores() -> None:
    report = _scan()
    candidates = _by_id(report)
    assert "weibo:weibo-dup" not in candidates  # 同平台同题被去重
    weibo_platform = next(p for p in report.platforms if p.platform == "weibo")
    assert weibo_platform.items_fetched == 44
    assert weibo_platform.items_after_dedup == 43
    # 跨平台同题：weibo-4 与 zhihu-4 同题 → 2/3
    assert candidates["weibo:weibo-4"].cross_platform_score == pytest.approx(2 / 3)
    assert candidates["zhihu:zhihu-4"].cross_platform_score == pytest.approx(2 / 3)
    assert candidates["weibo:weibo-5"].cross_platform_score == pytest.approx(0.0)


def test_candidate_carries_provenance_and_excerpt() -> None:
    report = _scan()
    candidates = _by_id(report)
    breakout = candidates["weibo:weibo-5"]
    assert breakout.text_excerpt is not None
    assert breakout.text_excerpt.startswith("这是低粉爆款")
    assert "hot:weibo:weibo-5" in breakout.evidence_refs
    assert "engagement:weibo:weibo-5" in breakout.evidence_refs
    assert breakout.saves == 4100  # views 不计入互动量，但原样保留
    wb3 = candidates["weibo:wb-3"]  # 无 url：read 跳过，engagement 走 source_id 兜底
    assert wb3.likes == 6000
    assert wb3.author_followers is None
    assert wb3.final_score is not None  # rank + engagement 重归一化


def test_scan_accepts_dailyhot_as_hot_provider() -> None:
    payload = {
        "code": 200,
        "data": [
            {"id": i, "title": f"热搜{i}", "url": f"https://weibo.com/{i}", "hot": 1000 - i}
            for i in range(1, 31)
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    hot = DailyHotApiProvider(base_url="http://test", transport=httpx.MockTransport(handler))
    radar = ViralRadar(hot_provider=hot, reader=FixtureProvider(), platforms=("weibo",), top_n=5)
    report = asyncio.run(radar.scan())
    assert report.platforms[0].source == "dailyhot"
    assert report.platforms[0].items_fetched == 30
    assert len(report.candidates) == 5
    # fixture 没有这些 url 的深数据 → 候选仍产出（rank-only 评分，缺失不归零）
    assert all(candidate.final_score is not None for candidate in report.candidates)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_viral_radar.py -q`
Expected: 全部 FAIL（模块不存在）

- [ ] **Step 3: 实现 viral_radar.py**

Create `src/wechat_traffic_agent/viral_radar.py`：

```python
"""Viral Radar: what content is abnormally succeeding right now.

Scan pipeline (all numeric scoring is deterministic — no LLM ranks numbers):
hot scan -> normalize + dedup -> platform-local ranking -> Top-N selection
-> deep fetch (read + author + engagement) -> ViralCandidate with weighted
sub-scores (rank .35 / engagement .25 / breakout .25 / freshness .15,
re-normalized over whatever is actually available; unavailable is never 0).

Known v1 limitation: engagement/breakout/freshness percentiles are computed
over the deep-fetched Top-N of each platform, not the full hot list.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from .scoring import (
    breakout_ratio,
    combine_final,
    dedup_items,
    engagement_value,
    normalize_title,
    percentile_score,
    rank_score,
)
from .sources import (
    AuthorSnapshot,
    ContentSnapshot,
    EngagementSnapshot,
    HotItem,
    SourceStatus,
)

EXCERPT_LENGTH = 120


class ViralCandidate(BaseModel):
    """One content item that is abnormally succeeding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    platform: str
    content_id: str
    url: str | None = None
    title: str
    text_excerpt: str | None = None
    rank: int | None = None
    published_at: datetime | None = None
    observed_at: datetime
    author_id: str | None = None
    author_followers: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    absolute_hot_score: float | None = None
    rank_score: float | None = None
    engagement_score: float | None = None
    breakout_score: float | None = None
    freshness_score: float | None = None
    cross_platform_score: float = 0.0
    final_score: float | None = None
    evidence_refs: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class PlatformScan(BaseModel):
    """Per-platform hot-scan provenance and dedup accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    source: str
    status: SourceStatus
    items_fetched: int
    items_after_dedup: int = 0
    error: str | None = None


class ScanReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scan_id: str
    observed_at: datetime
    platforms: tuple[PlatformScan, ...]
    candidates: tuple[ViralCandidate, ...]


@dataclass
class _DeepResult:
    platform: str
    item: HotItem
    content: ContentSnapshot | None = None
    author: AuthorSnapshot | None = None
    engagement: EngagementSnapshot | None = None
    evidence: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ViralRadar:
    """Deterministic hot scan + Top-N deep fetch -> ViralCandidates."""

    def __init__(
        self,
        *,
        hot_provider,
        reader,
        platforms: Sequence[str],
        top_n: int = 20,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.hot_provider = hot_provider
        self.reader = reader
        self.platforms = tuple(platforms)
        self.top_n = top_n
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def scan(self) -> ScanReport:
        now = self._now()
        fetches = await asyncio.gather(
            *(self.hot_provider.fetch(platform) for platform in self.platforms)
        )

        platform_scans: list[PlatformScan] = []
        deduped: dict[str, tuple[HotItem, ...]] = {}
        for platform, result in zip(self.platforms, fetches):
            if result.status is not SourceStatus.OK:
                platform_scans.append(
                    PlatformScan(
                        platform=platform,
                        source=result.source,
                        status=result.status,
                        items_fetched=0,
                        error=result.error,
                    )
                )
                continue
            items = dedup_items(result.items)
            deduped[platform] = items
            platform_scans.append(
                PlatformScan(
                    platform=platform,
                    source=result.source,
                    status=result.status,
                    items_fetched=len(result.items),
                    items_after_dedup=len(items),
                )
            )

        cross_scores = self._cross_platform_scores(deduped)
        selected = self._select_top(deduped)
        deep = await asyncio.gather(
            *(self._deep_fetch(platform, item) for platform, item in selected)
        )
        candidates = self._build_candidates(deep, deduped, cross_scores, now)
        return ScanReport(
            scan_id=uuid.uuid4().hex,
            observed_at=now,
            platforms=tuple(platform_scans),
            candidates=candidates,
        )

    def _cross_platform_scores(
        self, deduped: dict[str, tuple[HotItem, ...]]
    ) -> dict[str, float]:
        """Same normalized title appearing on several platforms (not merged)."""
        platform_count = len(deduped)
        if platform_count <= 1:
            return {}
        clusters: dict[str, set[str]] = {}
        for platform, items in deduped.items():
            for item in items:
                clusters.setdefault(normalize_title(item.title), set()).add(platform)
        return {
            title: len(platforms) / platform_count for title, platforms in clusters.items()
        }

    def _select_top(
        self, deduped: dict[str, tuple[HotItem, ...]]
    ) -> list[tuple[str, HotItem]]:
        entries: list[tuple[float | None, str, HotItem]] = []
        for platform, items in deduped.items():
            population_size = len(items)
            for item in items:
                entries.append(
                    (rank_score(item.rank, population_size), platform, item)
                )
        entries.sort(
            key=lambda e: (e[0] is None, -(e[0] or 0.0), e[1], e[2].source_id)
        )
        return [(platform, item) for _, platform, item in entries[: self.top_n]]

    async def _deep_fetch(self, platform: str, item: HotItem) -> _DeepResult:
        result = _DeepResult(
            platform=platform, item=item, evidence=(f"hot:{platform}:{item.source_id}",)
        )
        content_id = item.source_id
        if item.url:
            read = await self.reader.read(item.url, platform=platform)
            if read.status is SourceStatus.OK:
                result.content = read.items
                content_id = read.items.content_id
                result.evidence = result.evidence + (f"read:{platform}:{content_id}",)
            else:
                result.errors = result.errors + (
                    f"read:{read.error or read.status.value}",
                )
        if result.content is not None and result.content.author_id:
            author = await self.reader.author(result.content.author_id, platform=platform)
            if author.status is SourceStatus.OK:
                result.author = author.items
                result.evidence = result.evidence + (
                    f"author:{platform}:{author.items.author_id}",
                )
            else:
                result.errors = result.errors + (
                    f"author:{author.error or author.status.value}",
                )
        engagement_reader = getattr(self.reader, "engagement", None)
        if engagement_reader is not None:
            engagement = await engagement_reader(content_id, platform=platform)
            if engagement.status is SourceStatus.OK:
                result.engagement = engagement.items
                result.evidence = result.evidence + (
                    f"engagement:{platform}:{content_id}",
                )
            else:
                result.errors = result.errors + (
                    f"engagement:{engagement.error or engagement.status.value}",
                )
        return result

    def _build_candidates(
        self,
        deep: Sequence[_DeepResult],
        deduped: dict[str, tuple[HotItem, ...]],
        cross_scores: dict[str, float],
        now: datetime,
    ) -> tuple[ViralCandidate, ...]:
        by_platform: dict[str, list[_DeepResult]] = {}
        for result in deep:
            by_platform.setdefault(result.platform, []).append(result)

        built: list[ViralCandidate] = []
        for platform, group in by_platform.items():
            hot_items = deduped[platform]
            raw_hots = [
                item.raw_hot_value for item in hot_items if item.raw_hot_value is not None
            ]
            eng_values = [
                value
                for value in (
                    engagement_value(result.engagement)
                    for result in group
                    if result.engagement is not None
                )
                if value is not None
            ]
            ratios = [
                ratio
                for ratio in (
                    breakout_ratio(
                        engagement_value(result.engagement)
                        if result.engagement is not None
                        else None,
                        result.author.followers if result.author is not None else None,
                    )
                    for result in group
                )
                if ratio is not None
            ]
            ages = [
                self._age_seconds(result.content.published_at, now)
                for result in group
                if result.content is not None and result.content.published_at is not None
            ]
            for result in group:
                built.append(
                    self._build_candidate(
                        result,
                        population_size=len(hot_items),
                        raw_hots=raw_hots,
                        eng_values=eng_values,
                        ratios=ratios,
                        ages=ages,
                        now=now,
                        cross_scores=cross_scores,
                    )
                )
        built.sort(
            key=lambda c: (
                c.final_score is None,
                -(c.final_score or 0.0),
                c.rank is None,
                c.rank or 0,
                c.platform,
                c.candidate_id,
            )
        )
        return tuple(built)

    def _age_seconds(self, published_at: datetime, now: datetime) -> float:
        published = (
            published_at
            if published_at.tzinfo is not None
            else published_at.replace(tzinfo=timezone.utc)
        )
        return (now - published).total_seconds()

    def _build_candidate(
        self,
        result: _DeepResult,
        *,
        population_size: int,
        raw_hots: list[float],
        eng_values: list[int],
        ratios: list[float],
        ages: list[float],
        now: datetime,
        cross_scores: dict[str, float],
    ) -> ViralCandidate:
        item = result.item
        content = result.content
        author = result.author
        engagement = result.engagement
        content_id = content.content_id if content is not None else item.source_id
        eng = engagement_value(engagement) if engagement is not None else None
        ratio = breakout_ratio(
            eng, author.followers if author is not None else None
        )
        age = (
            self._age_seconds(content.published_at, now)
            if content is not None and content.published_at is not None
            else None
        )

        subscores: dict[str, float | None] = {
            "rank": rank_score(item.rank, population_size),
            "engagement": percentile_score(eng, eng_values) if eng is not None else None,
            "breakout": percentile_score(ratio, ratios) if ratio is not None else None,
            "freshness": (
                percentile_score(-age, [-a for a in ages]) if age is not None else None
            ),
        }
        return ViralCandidate(
            candidate_id=f"{item.platform}:{content_id}",
            platform=item.platform,
            content_id=content_id,
            url=item.url,
            title=item.title,
            text_excerpt=(
                content.text[:EXCERPT_LENGTH]
                if content is not None and content.text
                else None
            ),
            rank=item.rank,
            published_at=content.published_at if content is not None else None,
            observed_at=now,
            author_id=(
                author.author_id
                if author is not None
                else (content.author_id if content is not None else None)
            ),
            author_followers=author.followers if author is not None else None,
            views=engagement.views if engagement is not None else None,
            likes=engagement.likes if engagement is not None else None,
            comments=engagement.comments if engagement is not None else None,
            shares=engagement.shares if engagement is not None else None,
            saves=engagement.saves if engagement is not None else None,
            absolute_hot_score=(
                percentile_score(item.raw_hot_value, raw_hots)
                if item.raw_hot_value is not None
                else None
            ),
            rank_score=subscores["rank"],
            engagement_score=subscores["engagement"],
            breakout_score=subscores["breakout"],
            freshness_score=subscores["freshness"],
            cross_platform_score=cross_scores.get(normalize_title(item.title), 0.0),
            final_score=combine_final(subscores),
            evidence_refs=result.evidence,
            errors=result.errors,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_viral_radar.py -v`
Expected: `10 passed`；全量 `python -m pytest -q` → `83 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/viral_radar.py tests/test_viral_radar.py
git commit -m "feat: ViralRadar deterministic scan pipeline with re-normalized scoring"
```

---

### Task 5: AgentReachProvider 真实 CLI 模板 + 共享 httpx 深抓

**Files:**
- Modify: `src/wechat_traffic_agent/agent_reach.py`
- Test: `tests/test_agent_reach.py`（修改 1 个旧测试 + 追加新测试）

**背景（已核实）：** bilibili-cli 文档化命令 `bili video {id} --comments --json` / `bili user {author_id} --json`，输出 `{ok, schema_version, data, error}` envelope（`video` 的 data 含 `data.comments`；错误 `{ok: false, error: {code, message}}`）。其余平台（weibo/xiaohongshu/twitter）命令或输出形状未文档化 → 无默认模板，用户可注入。

- [ ] **Step 1: 修改旧测试 + 写失败测试**

`tests/test_agent_reach.py`：

(a) 旧测试 `test_comments_and_author_unconfigured_return_unavailable` 中 `platform="bilibili"` 改为 `platform="weibo"`（bilibili 现在有默认模板，不再 UNAVAILABLE；weibo 仍是 configurable-only），函数名与断言结构保持不变。

(b) 顶部 import 区补：

```python
import httpx

from wechat_traffic_agent.sources import EngagementSnapshot
```

(c) 文件末尾追加：

```python
async def _boom_runner(cmd: list[str]) -> tuple[int, str, str]:
    raise AssertionError("subprocess runner must not be used when http_client is set")


def test_comments_bilibili_default_template_parses_envelope() -> None:
    payload = json.dumps(
        {
            "ok": True,
            "schema_version": "1",
            "data": {
                "comments": [
                    {"rpid": 101, "content": {"message": "太真实了"}, "like": 34},
                    {"rpid": 102, "text": "已收藏"},
                    {"rpid": 103},  # 无正文 → 跳过
                    "not-a-dict",  # 非法行 → 跳过
                ]
            },
        }
    )
    runner = _runner(
        {("bili", "video", "BV1", "--comments", "--json"): (0, payload, "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.comments("BV1", platform="bilibili"))
    assert result.status is SourceStatus.OK
    assert result.source == "agent-reach"
    first, second = result.items
    assert first.text == "太真实了"
    assert first.comment_id == "101"
    assert first.likes == 34
    assert first.content_id == "BV1"
    assert second.text == "已收藏"


def test_comments_bilibili_envelope_error_returns_failed() -> None:
    payload = json.dumps(
        {"ok": False, "schema_version": "1",
         "error": {"code": "not_found", "message": "未找到视频"}}
    )
    runner = _runner(
        {("bili", "video", "BV404", "--comments", "--json"): (0, payload, "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.comments("BV404", platform="bilibili"))
    assert result.status is SourceStatus.FAILED
    assert "未找到视频" in (result.error or "")
    assert result.items == ()


def test_comments_non_json_output_returns_failed() -> None:
    runner = _runner(
        {("bili", "video", "BV1", "--comments", "--json"): (0, "not json", "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.comments("BV1", platform="bilibili"))
    assert result.status is SourceStatus.FAILED
    assert "not JSON" in (result.error or "")


def test_comments_custom_template_is_configurable() -> None:
    runner = _runner({("xhs", "comments", "note-1"): (0, json.dumps([{"text": "评论甲"}]), "")})
    provider = AgentReachProvider(
        runner=runner, commands={"comments_xiaohongshu": ["xhs", "comments", "{content_id}"]}
    )
    result = asyncio.run(provider.comments("note-1", platform="xiaohongshu"))
    assert result.status is SourceStatus.OK
    assert result.items[0].text == "评论甲"


def test_author_bilibili_default_template_parses_user() -> None:
    payload = json.dumps(
        {"ok": True, "schema_version": "1",
         "data": {"user": {"mid": 946974, "follower": 123456}}}
    )
    runner = _runner({("bili", "user", "946974", "--json"): (0, payload, "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.author("946974", platform="bilibili"))
    assert result.status is SourceStatus.OK
    assert result.items.author_id == "946974"
    assert result.items.followers == 123456


def test_author_missing_followers_stays_none() -> None:
    payload = json.dumps({"ok": True, "schema_version": "1", "data": {"user": {"mid": 1}}})
    runner = _runner({("bili", "user", "1", "--json"): (0, payload, "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.author("1", platform="bilibili"))
    assert result.status is SourceStatus.OK  # 调用成功但数据缺失 ≠ 失败
    assert result.items.followers is None


def test_engagement_returns_unavailable_with_degenerate_snapshot() -> None:
    provider = AgentReachProvider(runner=_runner({}))
    result = asyncio.run(provider.engagement("any", platform="bilibili"))
    assert result.status is SourceStatus.UNAVAILABLE
    assert result.error is not None
    assert result.items == EngagementSnapshot(content_id="", platform="bilibili")


def test_read_uses_shared_http_client_for_all_reads() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=f"# 标题\n\n正文 {len(requested)}")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AgentReachProvider(runner=_boom_runner, http_client=client)
        first = await provider.read("https://example.com/a")
        second = await provider.read("https://example.com/b")
        await client.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first.status is SourceStatus.OK
    assert first.items.text.startswith("# 标题")
    assert second.status is SourceStatus.OK
    assert requested == [
        "https://r.jina.ai/https://example.com/a",
        "https://r.jina.ai/https://example.com/b",
    ]


def test_read_http_error_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AgentReachProvider(runner=_boom_runner, http_client=client)
        result = await provider.read("https://example.com/x")
        await client.aclose()
        return result

    result = asyncio.run(run())
    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert result.items.text == ""  # 退化实例纪律


def test_read_http_empty_content_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="   ")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AgentReachProvider(runner=_boom_runner, http_client=client)
        result = await provider.read("https://example.com/x")
        await client.aclose()
        return result

    result = asyncio.run(run())
    assert result.status is SourceStatus.FAILED
    assert "empty content" in (result.error or "")
```

注意：`test_comments_custom_template_is_configurable` 用顶层 JSON 数组载荷——解析器必须同时接受"裸 list"与 envelope 两种形状。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_agent_reach.py -q`
Expected: 新增 10 个测试 FAIL；改过的 `test_comments_and_author_unconfigured_return_unavailable` FAIL（bilibili→weibo 已改，但 provider 还没实现——先改测试再实现，此刻我们ibo 尚无模板所以它其实会 PASS；无论如何以 Step 4 全绿为准）

- [ ] **Step 3: 实现 agent_reach.py**

(a) 模块 docstring 替换为：

```python
"""Agent-Reach adapter: a CLI capability layer, not an HTTP service.

`agent-reach doctor --json` reports per-channel health and the active
backend; content is fetched by invoking the tools Agent-Reach manages.
This adapter runs configured command templates through an injectable
runner, so tests stay offline.

PR3: comments/author ship with documented bilibili-cli templates
(`bili video {id} --comments --json` / `bili user {author_id} --json`),
parsed defensively against the `{ok, data, error}` envelope. Other
platforms have no documented CLI output shape yet — inject them via
``commands={"comments_<platform>": [...]}``. ``engagement`` has no
documented producer and stays UNAVAILABLE. Deep fetch can share one
httpx client by passing ``http_client``: ``read`` then goes through
``https://r.jina.ai/{url}`` over that client instead of one curl
subprocess per item.
"""
```

(b) import 区补 `import httpx`，并把 sources import 改为含 `EngagementSnapshot`：

```python
from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    EngagementSnapshot,
    SourceResult,
    SourceStatus,
)
```

(c) `DEFAULT_COMMANDS` 替换为：

```python
DEFAULT_COMMANDS: dict[str, list[str]] = {
    "doctor": ["agent-reach", "doctor", "--json"],
    "read_web": ["curl", "-s", "https://r.jina.ai/{url}"],
    "comments_bilibili": ["bili", "video", "{content_id}", "--comments", "--json"],
    "author_bilibili": ["bili", "user", "{author_id}", "--json"],
}
```

(d) `DEFAULT_TIMEOUT` 之后追加解析助手：

```python
def _envelope_error(payload: object) -> str | None:
    """bilibili-cli style envelope error: {ok: false, error: {code, message}}."""
    if isinstance(payload, dict) and payload.get("ok") is False:
        error = payload.get