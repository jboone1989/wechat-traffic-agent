# PR2 极薄互联网 Port（Internet Sources）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 WeChat 当前真正需要的四个极薄互联网 contracts（`HotFeedProvider`/`ContentReader`/`CommentReader`/`AuthorReader`）与 domain-neutral DTO，第一版只接 DailyHotApi（广撒网）与 Agent-Reach（深抓）两个 OSS adapter + 离线 FixtureProvider，所有来源故障按 `SourceResult` 隔离。

**Architecture:** 一个 `sources.py` 模块承载全部 DTO/protocol/`SourceResult`（不建"大一统互联网框架"）；`dailyhot.py`（HTTP，httpx + 可注入 transport）、`agent_reach.py`（进程适配器，命令模板可配置 + 可注入 runner）、`fixture.py`（离线数据 + 故障注入）各自极薄。所有平台没有的数据一律 `None / unavailable`，绝不强迫统一、绝不把缺失当 0。

**已核实的外部事实（2026-08-26 抓取 OSS 文档）：**
- DailyHotApi：自托管 Docker 默认端口 `6688`；路由为纯 `GET /{platform}`（weibo/zhihu/bilibili/douyin/toutiao 等；小红书/Reddit/X **不在**其中）；默认缓存 60 分钟；README 未记录响应字段 → adapter 防御式解析，当作 best-effort 源。
- Agent-Reach：**不是 HTTP 服务**。`agent-reach doctor --json` 输出每 channel 健康状态与 `active_backend`；内容经其管理的上游工具直接调用（官方示例：`curl -s "https://r.jina.ai/URL"` 读网页正文、`bili search`、`twitter read`（需 TWITTER_AUTH_TOKEN 环境变量）、`mcporter call exa.web_search_exa`）。因此 provider 是 subprocess 适配器。

**Tech Stack:** Python 3.11、pydantic v2、httpx（新增依赖）、asyncio subprocess。测试全部离线（`httpx.MockTransport` / 假 runner），无网络。

**基线（开始前确认）：** `main` @ `4ea5ce8`（PR1 + serve + wecom pipeline 已合并），`python -m pytest -q` → `36 passed`，工作树干净。分支：`feat/wechat-internet-sources`。注意用户可能并行开别的会话改 main——切分支前先 `git status` 核对。

**PR2 的明确边界（写进验收）：** `CommentReader`/`AuthorReader` 的**协议**在 PR2 定义并被 FixtureProvider 实现；`AgentReachProvider` 的 comments/author 在 PR2 返回 `UNAVAILABLE`（真实平台 CLI 解析属 PR3，需 bili/twitter 等上游工具接好后才能写有意义的解析器——现在写解析是猜 API）。`EngagementSnapshot` 同样只定义契约，首个生产者是 PR3 的 deep fetch。

---

### Task 0: 建分支、确认基线

- [ ] **Step 1:** `git status --short` 确认干净（若仍有其他会话的未提交产物，停下来向用户确认）
- [ ] **Step 2:** `git checkout -b feat/wechat-internet-sources`
- [ ] **Step 3:** `python -m pytest -q` → `36 passed in ...`

---

### Task 1: sources.py 契约模块

**Files:**
- Create: `src/wechat_traffic_agent/sources.py`
- Test: `tests/test_sources.py`（新建）

- [ ] **Step 1: 写失败测试**

Create `tests/test_sources.py`：

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    EngagementSnapshot,
    HotItem,
    SourceResult,
    SourceStatus,
)


def test_hot_item_missing_fields_default_to_none() -> None:
    item = HotItem(source_id="h1", platform="weibo", title="标题")
    assert item.url is None
    assert item.rank is None
    assert item.observed_at is None
    assert item.raw_hot_value is None


def test_content_snapshot_round_trips() -> None:
    snapshot = ContentSnapshot(
        content_id="c1",
        platform="generic_web",
        url="https://example.com/a",
        author_id=None,
        title="标题",
        text="正文",
        published_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )
    restored = ContentSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot


def test_engagement_author_comment_allow_all_missing() -> None:
    engagement = EngagementSnapshot(content_id="c1", platform="weibo")
    assert all(
        value is None
        for value in (
            engagement.views,
            engagement.likes,
            engagement.comments,
            engagement.shares,
            engagement.saves,
        )
    )
    author = AuthorSnapshot(author_id="a1", platform="bilibili")
    assert author.followers is None
    comment = Comment(content_id="c1", platform="zhihu", text="评论")
    assert comment.author_id is None
    assert comment.likes is None


def test_source_result_carries_provenance_and_is_frozen() -> None:
    observed_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    result = SourceResult(
        source="fixture",
        platform="weibo",
        status=SourceStatus.OK,
        items=(HotItem(source_id="h1", platform="weibo", title="标题"),),
        error=None,
        observed_at=observed_at,
    )
    assert result.source == "fixture"
    assert result.platform == "weibo"
    assert result.error is None
    assert result.observed_at == observed_at
    assert len(result.items) == 1
    with pytest.raises((TypeError, ValidationError)):
        result.status = SourceStatus.FAILED  # frozen
    with pytest.raises(ValidationError):
        SourceResult(
            source="fixture",
            platform="weibo",
            status=SourceStatus.OK,
            items=(),
            observed_at=observed_at,
            unknown_field=1,  # extra="forbid"：拼错字段必须炸
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_sources.py -v`
Expected: 全部 FAIL（`sources` 模块不存在）

- [ ] **Step 3: 实现 sources.py**

Create `src/wechat_traffic_agent/sources.py`：

```python
"""Thin, platform-neutral internet-source contracts.

PR2 establishes only the contracts WeChat currently needs. No universal
internet framework: providers adapt existing OSS tools; data a platform
does not have is always None (unavailable), never a forced zero.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict


class SourceStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


T = TypeVar("T")


class SourceResult(BaseModel, Generic[T]):
    """One provider call with provenance and failure isolation.

    One platform failing must never abort a scan: callers collect
    SourceResult objects and handle FAILED/UNAVAILABLE per platform.
    FAILED/UNAVAILABLE results carry ``items=()`` for tuple-typed sources
    and a minimal degenerate instance for scalar-typed sources (e.g. a
    ContentSnapshot with empty text); consumers must branch on ``status``
    before touching ``items``. ``observed_at`` is the batch call time;
    item-level ``observed_at`` fields, where present, are per item.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str  # e.g. "dailyhot", "agent-reach", "fixture"
    platform: str  # e.g. "weibo", "zhihu", "generic_web"
    status: SourceStatus
    items: T
    error: str | None = None
    observed_at: datetime


class HotItem(BaseModel):
    """One hot-list entry, as observed at one point in time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str  # id of this item within its platform
    platform: str
    title: str
    url: str | None = None
    rank: int | None = None
    observed_at: datetime | None = None
    raw_hot_value: float | None = None  # 平台原始热度，禁止跨平台横比


class ContentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str
    platform: str
    url: str
    title: str | None = None
    text: str
    author_id: str | None = None
    published_at: datetime | None = None


class EngagementSnapshot(BaseModel):
    """PR2 defines the contract; PR3's deep fetch produces the first values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str
    platform: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    observed_at: datetime | None = None


class AuthorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author_id: str
    platform: str
    followers: int | None = None


class Comment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str
    platform: str
    text: str
    comment_id: str | None = None
    author_id: str | None = None
    likes: int | None = None


# fetch 的 platform 是唯一主参数，保持位置参数；read/comments/author
# 已有资源标识符作主参数，platform 统一 keyword-only 防错位。


class HotFeedProvider(Protocol):
    async def fetch(self, platform: str) -> SourceResult[tuple[HotItem, ...]]: ...


class ContentReader(Protocol):
    async def read(
        self, url: str, *, platform: str = "generic_web"
    ) -> SourceResult[ContentSnapshot]: ...


class CommentReader(Protocol):
    async def comments(
        self, content_id: str, *, platform: str
    ) -> SourceResult[tuple[Comment, ...]]: ...


class AuthorReader(Protocol):
    async def author(
        self, author_id: str, *, platform: str
    ) -> SourceResult[AuthorSnapshot]: ...
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_sources.py -v`
Expected: `4 passed`；全量 `python -m pytest -q` → `40 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/sources.py tests/test_sources.py
git commit -m "feat: thin internet-source contracts with provenance

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: FixtureProvider（离线三平台 + 故障注入）

**Files:**
- Create: `src/wechat_traffic_agent/fixture.py`
- Test: `tests/test_fixture.py`（新建）

- [ ] **Step 1: 写失败测试**

Create `tests/test_fixture.py`：

```python
from __future__ import annotations

import asyncio

from wechat_traffic_agent.fixture import FixtureProvider
from wechat_traffic_agent.sources import SourceStatus


def test_fixture_supports_at_least_three_platforms() -> None:
    provider = FixtureProvider()

    async def fetch_all():
        return {platform: await provider.fetch(platform) for platform in ("weibo", "zhihu", "bilibili")}

    results = asyncio.run(fetch_all())
    assert set(results) == {"weibo", "zhihu", "bilibili"}
    for platform, result in results.items():
        assert result.status is SourceStatus.OK
        assert result.source == "fixture"
        assert result.platform == platform
        assert result.observed_at is not None
        assert len(result.items) >= 2
        assert all(item.platform == platform for item in result.items)


def test_fixture_broken_platform_fails_in_isolation() -> None:
    provider = FixtureProvider(broken_platforms=frozenset({"weibo"}))

    async def fetch():
        weibo = await provider.fetch("weibo")
        zhihu = await provider.fetch("zhihu")
        return weibo, zhihu

    weibo, zhihu = asyncio.run(fetch())
    assert weibo.status is SourceStatus.FAILED
    assert weibo.items == ()
    assert weibo.error is not None
    # 单平台失败绝不中断其他平台
    assert zhihu.status is SourceStatus.OK


def test_fixture_unknown_platform_is_unavailable() -> None:
    provider = FixtureProvider()
    result = asyncio.run(provider.fetch("douyin"))
    assert result.status is SourceStatus.UNAVAILABLE
    assert result.items == ()
    assert result.error is not None


def test_fixture_read_comments_author() -> None:
    provider = FixtureProvider()

    async def run():
        content = await provider.read("https://fixture.example/weibo/1")
        comments = await provider.comments("fixture-weibo-1", platform="weibo")
        author = await provider.author("fixture-author-1", platform="weibo")
        return content, comments, author

    content, comments, author = asyncio.run(run())
    assert content.status is SourceStatus.OK
    assert content.items.text
    assert comments.status is SourceStatus.OK
    assert len(comments.items) >= 1
    assert author.status is SourceStatus.OK
    assert author.items.followers is not None
    # 数据外的 id → unavailable
    missing = asyncio.run(provider.comments("not-in-fixture", platform="weibo"))
    assert missing.status is SourceStatus.UNAVAILABLE
    assert asyncio.run(provider.read("https://not-in-fixture.example/x")).status is SourceStatus.UNAVAILABLE
    assert asyncio.run(provider.author("not-in-fixture", platform="weibo")).status is SourceStatus.UNAVAILABLE


def test_fixture_deep_fetch_walk_connects_hot_to_content() -> None:
    provider = FixtureProvider()

    async def walk():
        feed = await provider.fetch("weibo")
        item = feed.items[0]
        content = await provider.read(item.url or "", platform="weibo")
        comments = await provider.comments(content.items.content_id, platform="weibo")
        author = await provider.author(content.items.author_id or "", platform="weibo")
        return feed, content, comments, author

    feed, content, comments, author = asyncio.run(walk())
    assert feed.status is SourceStatus.OK
    assert content.status is SourceStatus.OK
    assert content.items.text
    assert comments.status is SourceStatus.OK
    assert len(comments.items) >= 1
    assert author.status is SourceStatus.OK
    assert author.items.followers is not None


def test_fixture_broken_platform_fails_leaf_methods_too() -> None:
    provider = FixtureProvider(broken_platforms=frozenset({"weibo"}))

    async def run():
        read = await provider.read("https://fixture.example/weibo/1", platform="weibo")
        comments = await provider.comments("wb-1", platform="weibo")
        author = await provider.author("fixture-author-1", platform="weibo")
        return read, comments, author

    read, comments, author = asyncio.run(run())
    for result in (read, comments, author):
        assert result.status is SourceStatus.FAILED
        assert result.error is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_fixture.py -v`
Expected: 全部 FAIL（`fixture` 模块不存在）

- [ ] **Step 3: 实现 fixture.py**

Create `src/wechat_traffic_agent/fixture.py`：

```python
"""Deterministic in-repo fixture implementing all four source contracts.

Used by CI and unit tests; also lets PR3 build a scan pipeline before any
real service is reachable. One fixture platform can be marked broken to
prove failure isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    HotItem,
    SourceResult,
    SourceStatus,
)

FIXTURE_HOT: dict[str, tuple[HotItem, ...]] = {
    "weibo": (
        HotItem(source_id="wb-1", platform="weibo", title="微博样本一", url="https://fixture.example/weibo/1", rank=1, raw_hot_value=9500.0),
        HotItem(source_id="wb-2", platform="weibo", title="微博样本二", url="https://fixture.example/weibo/2", rank=2, raw_hot_value=7200.0),
        HotItem(source_id="wb-3", platform="weibo", title="微博样本三", rank=3, raw_hot_value=4100.0),
    ),
    "zhihu": (
        HotItem(source_id="zh-1", platform="zhihu", title="知乎样本一", url="https://zhihu.com/fixture/1", rank=1, raw_hot_value=3200.0),
        HotItem(source_id="zh-2", platform="zhihu", title="知乎样本二", url="https://zhihu.com/fixture/2", rank=2),
    ),
    "bilibili": (
        HotItem(source_id="bili-1", platform="bilibili", title="B站样本一", url="https://bilibili.com/fixture/1", rank=1, raw_hot_value=8800.0),
        HotItem(source_id="bili-2", platform="bilibili", title="B站样本二", url="https://bilibili.com/fixture/2", rank=2, raw_hot_value=5100.0),
    ),
}

# 数据图必须连通：fetch 的 url → read；read 的 content_id（= source_id）→ comments；
# read 的 author_id → author。PR3 离线深抓管线依赖这条链路。
FIXTURE_CONTENT: dict[str, ContentSnapshot] = {
    "https://fixture.example/weibo/1": ContentSnapshot(
        content_id="wb-1",
        platform="weibo",
        url="https://fixture.example/weibo/1",
        title="微博样本一",
        text="这是微博样本一的正文，包含一个真实观察。",
        author_id="fixture-author-1",
    ),
}

FIXTURE_COMMENTS: dict[str, tuple[Comment, ...]] = {
    "wb-1": (
        Comment(content_id="wb-1", platform="weibo", comment_id="c-1", text="说的就是我", author_id="fixture-author-2", likes=12),
        Comment(content_id="wb-1", platform="weibo", comment_id="c-2", text="收藏了"),
    ),
}

FIXTURE_AUTHORS: dict[str, AuthorSnapshot] = {
    "fixture-author-1": AuthorSnapshot(author_id="fixture-author-1", platform="weibo", followers=18000),
}


class FixtureProvider:
    """Offline fixture with canned data and per-platform failure injection."""

    def __init__(self, *, broken_platforms: frozenset[str] = frozenset()) -> None:
        self._broken = set(broken_platforms)

    def _observed(self) -> datetime:
        return datetime.now(timezone.utc)

    def _unavailable(self, platform: str, reason: str, items) -> SourceResult:
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.UNAVAILABLE,
            items=items,
            error=reason,
            observed_at=self._observed(),
        )

    def _broken(self, platform: str, items) -> SourceResult:
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.FAILED,
            items=items,
            error="fixture: platform marked broken",
            observed_at=self._observed(),
        )

    async def fetch(self, platform: str) -> SourceResult[tuple[HotItem, ...]]:
        if platform in self._broken:
            return self._broken(platform, ())
        items = FIXTURE_HOT.get(platform)
        if items is None:
            return self._unavailable(platform, f"fixture has no hot data for {platform!r}", ())
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=items,
            observed_at=self._observed(),
        )

    async def read(self, url: str, *, platform: str = "generic_web") -> SourceResult[ContentSnapshot]:
        if platform in self._broken:
            return self._broken(platform, ContentSnapshot(content_id="", platform=platform, url="", text=""))
        snapshot = FIXTURE_CONTENT.get(url)
        if snapshot is None:
            return self._unavailable(
                platform, f"fixture has no content for {url!r}",
                ContentSnapshot(content_id="", platform=platform, url="", text=""),
            )
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=snapshot,
            observed_at=self._observed(),
        )

    async def comments(self, content_id: str, *, platform: str) -> SourceResult[tuple[Comment, ...]]:
        if platform in self._broken:
            return self._broken(platform, ())
        items = FIXTURE_COMMENTS.get(content_id)
        if items is None:
            return self._unavailable(platform, f"fixture has no comments for {content_id!r}", ())
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=items,
            observed_at=self._observed(),
        )

    async def author(self, author_id: str, *, platform: str) -> SourceResult[AuthorSnapshot]:
        if platform in self._broken:
            return self._broken(platform, AuthorSnapshot(author_id="", platform=platform))
        snapshot = FIXTURE_AUTHORS.get(author_id)
        if snapshot is None:
            return self._unavailable(
                platform, f"fixture has no author for {author_id!r}",
                AuthorSnapshot(author_id="", platform=platform),
            )
        return SourceResult(
            source="fixture",
            platform=platform,
            status=SourceStatus.OK,
            items=snapshot,
            observed_at=self._observed(),
        )
```

（`_unavailable` 返回类型标注为 `SourceResult` 未参数化——pyright/mypy 会提示缺参但运行时无碍；如 IDE 警告可写成 `SourceResult[tuple]`。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_fixture.py -v`
Expected: `6 passed`；全量 `python -m pytest -q` → `46 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/fixture.py tests/test_fixture.py
git commit -m "feat: offline FixtureProvider for CI and offline tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: DailyHotApiProvider

**Files:**
- Create: `src/wechat_traffic_agent/dailyhot.py`
- Test: `tests/test_dailyhot.py`（新建）
- Modify: `pyproject.toml`（dependencies 加 `httpx>=0.27`）

- [ ] **Step 1: 写失败测试**

Create `tests/test_dailyhot.py`：

```python
from __future__ import annotations

import asyncio

import httpx
import pytest

from wechat_traffic_agent.dailyhot import DailyHotApiProvider
from wechat_traffic_agent.sources import SourceStatus


def _provider(by_platform: dict[str, object]) -> DailyHotApiProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if path in by_platform:
            return httpx.Response(200, json=by_platform[path])
        return httpx.Response(500, json={"error": "boom"})

    return DailyHotApiProvider(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )


def test_fetch_maps_items_with_rank_and_observed_at() -> None:
    provider = _provider(
        {
            "weibo": {
                "code": 200,
                "data": [
                    {"id": 11, "title": "热搜一", "url": "https://weibo.com/11", "hot": 100},
                    {"id": 12, "title": "热搜二", "hot": "98"},
                ],
            }
        }
    )
    result = asyncio.run(provider.fetch("weibo"))
    assert result.status is SourceStatus.OK
    assert result.source == "dailyhot"
    assert result.platform == "weibo"
    assert result.observed_at is not None
    first, second = result.items
    assert first.source_id == "11"
    assert first.title == "热搜一"
    assert first.url == "https://weibo.com/11"
    assert first.rank == 1
    assert first.raw_hot_value == pytest.approx(100.0)
    assert second.rank == 2
    assert second.raw_hot_value == pytest.approx(98.0)  # 字符串热度可转浮点
    assert second.url is None


def test_platform_failure_returns_failed_result_not_exception() -> None:
    provider = _provider(
        {
            "zhihu": {
                "code": 200,
                "data": [{"id": 1, "title": "知乎热搜", "url": "https://zhihu.com/1", "hot": 10}],
            }
        }
    )

    async def run():
        weibo = await provider.fetch("weibo")  # 500
        zhihu = await provider.fetch("zhihu")
        return weibo, zhihu

    weibo, zhihu = asyncio.run(run())
    assert weibo.status is SourceStatus.FAILED
    assert weibo.items == ()
    assert weibo.error is not None
    # 微博失败 ≠ 整个 scan 失败
    assert zhihu.status is SourceStatus.OK
    assert len(zhihu.items) == 1


def test_malformed_payload_returns_failed() -> None:
    provider = _provider({"weibo": {"code": 200, "data": "not-a-list"}})
    result = asyncio.run(provider.fetch("weibo"))
    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert result.items == ()


def test_missing_fields_stay_none_and_bad_rows_skipped() -> None:
    provider = _provider(
        {
            "weibo": {
                "code": 200,
                "data": [
                    {"title": "只有标题"},
                    "not-a-dict",
                    {"id": 3, "title": "热度第三", "hot": "一万"},
                    {"id": 4, "hot": 5},  # 缺标题 → 跳过
                ],
            }
        }
    )
    result = asyncio.run(provider.fetch("weibo"))
    assert result.status is SourceStatus.OK
    assert len(result.items) == 2  # 非 dict 行被跳过；缺标题行被跳过
    first = result.items[0]
    assert first.title == "只有标题"
    assert first.raw_hot_value is None
    assert result.items[1].raw_hot_value is None  # "一万" 不可转浮点 → None


def test_transport_and_parse_errors_return_failed() -> None:
    def raising_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def run_transport():
        provider = DailyHotApiProvider(
            base_url="http://test", transport=httpx.MockTransport(raising_handler)
        )
        return await provider.fetch("weibo")

    def html_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async def run_parse():
        provider = DailyHotApiProvider(
            base_url="http://test", transport=httpx.MockTransport(html_handler)
        )
        return await provider.fetch("weibo")

    transport_result = asyncio.run(run_transport())
    parse_result = asyncio.run(run_parse())
    assert transport_result.status is SourceStatus.FAILED
    assert "ConnectError" in (transport_result.error or "")
    assert parse_result.status is SourceStatus.FAILED
    assert parse_result.error is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_dailyhot.py -v`
Expected: 全部 FAIL（`dailyhot` 模块不存在；httpx 未安装则报依赖错误——先执行 Step 3a）

- [ ] **Step 3a: 加依赖**

`pyproject.toml` 的 `dependencies` 改为：

```toml
dependencies = [
    "agent-runtime>=0.3,<1.0",
    "httpx>=0.27",
    "pydantic>=2.5",
]
```

Run: `python -m pip install -e . --no-deps 2>&1 | tail -1` 或直接 `python -m pip install "httpx>=0.27"`（若 httpx 已可用则跳过）。

- [ ] **Step 3b: 实现 dailyhot.py**

Create `src/wechat_traffic_agent/dailyhot.py`：

```python
"""DailyHotApi adapter: cheap best-effort hot feeds.

DailyHotApi is a best-effort source (60-min cache, some backends scrape
pages). This adapter parses defensively: unexpected shapes become FAILED
results, missing fields stay None, and one platform's failure never
aborts a scan.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx

from .sources import HotItem, SourceResult, SourceStatus

DEFAULT_BASE_URL = "http://localhost:6688"


def _coerce_hot(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):  # NaN/Inf 会毒化 PR3 的百分位计算
        return None
    return parsed


class DailyHotApiProvider:
    """GET /{platform} against a DailyHotApi deployment (Docker default port 6688)."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    async def fetch(self, platform: str) -> SourceResult[tuple[HotItem, ...]]:
        observed_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, transport=self._transport
            ) as client:
                response = await client.get(f"/{platform}")
                response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return SourceResult(
                source="dailyhot",
                platform=platform,
                status=SourceStatus.FAILED,
                items=(),
                error=f"{type(exc).__name__}: {exc}",
                observed_at=observed_at,
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return SourceResult(
                source="dailyhot",
                platform=platform,
                status=SourceStatus.FAILED,
                items=(),
                error="unexpected payload: data is not a list",
                observed_at=observed_at,
            )
        items: list[HotItem] = []
        for rank, row in enumerate(data, start=1):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            items.append(
                HotItem(
                    source_id=str(row.get("id") or rank),  # 真实载荷恒有 id；无 id 时以原始位置兜底
                    platform=platform,
                    title=title,
                    url=row.get("url"),
                    rank=rank,
                    observed_at=observed_at,
                    raw_hot_value=_coerce_hot(row.get("hot")),
                )
            )
        # 空列表也是 OK：平台活着但当前无热点，与 FAILED 区分
        return SourceResult(
            source="dailyhot",
            platform=platform,
            status=SourceStatus.OK,
            items=tuple(items),
            observed_at=observed_at,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_dailyhot.py -v`
Expected: `5 passed`；全量 `python -m pytest -q` → `51 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/dailyhot.py tests/test_dailyhot.py pyproject.toml
git commit -m "feat: DailyHotApi provider with per-platform failure isolation

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: AgentReachProvider（进程适配器）

**Files:**
- Create: `src/wechat_traffic_agent/agent_reach.py`
- Test: `tests/test_agent_reach.py`（新建）

- [ ] **Step 1: 写失败测试**

Create `tests/test_agent_reach.py`：

```python
from __future__ import annotations

import asyncio
import json

from wechat_traffic_agent.agent_reach import AgentReachProvider
from wechat_traffic_agent.sources import SourceStatus


def _runner(script: dict[tuple[str, ...], tuple[int, str, str]]):
    async def run(cmd: list[str]) -> tuple[int, str, str]:
        return script.get(tuple(cmd), (1, "", "command not mocked"))

    return run


def test_doctor_parses_json_output() -> None:
    doctor_output = json.dumps(
        {"channels": {"bilibili": "✅", "weibo": "❌"}, "active_backend": {"bilibili": "cli"}}
    )
    runner = _runner({("agent-reach", "doctor", "--json"): (0, doctor_output, "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.doctor())
    assert result.status is SourceStatus.OK
    assert result.source == "agent-reach"
    assert result.platform == "doctor"
    assert result.items["channels"]["bilibili"] == "✅"
    assert result.observed_at is not None


def test_doctor_nonzero_exit_returns_failed() -> None:
    runner = _runner({("agent-reach", "doctor", "--json"): (127, "", "agent-reach: not found")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.doctor())
    assert result.status is SourceStatus.FAILED
    assert "not found" in (result.error or "")


def test_read_uses_configured_template_and_returns_snapshot() -> None:
    url = "https://example.com/article"
    runner = _runner(
        {("curl", "-s", f"https://r.jina.ai/{url}"): (0, "# 标题\n\n正文段落", "")}
    )
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.read(url))
    assert result.status is SourceStatus.OK
    assert result.platform == "generic_web"
    assert result.items.content_id == url
    assert result.items.text.startswith("# 标题")


def test_read_failure_returns_failed() -> None:
    runner = _runner({})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.read("https://example.com/missing"))
    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert result.items.text == ""  # 标量类型 FAILED 携带退化实例（契约约定）


def test_comments_and_author_unconfigured_return_unavailable() -> None:
    provider = AgentReachProvider(runner=_runner({}))

    async def run():
        comments = await provider.comments("some-id", platform="bilibili")
        author = await provider.author("some-author", platform="bilibili")
        return comments, author

    comments, author = asyncio.run(run())
    for result in (comments, author):
        assert result.status is SourceStatus.UNAVAILABLE
        assert result.error is not None
    assert comments.items == ()
    assert author.items.author_id == ""


def test_doctor_non_json_output_returns_failed() -> None:
    runner = _runner({("agent-reach", "doctor", "--json"): (0, "not json", "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.doctor())
    assert result.status is SourceStatus.FAILED
    assert "not JSON" in (result.error or "")


def test_read_empty_content_returns_failed() -> None:
    url = "https://example.com/empty"
    runner = _runner({("curl", "-s", f"https://r.jina.ai/{url}"): (0, "   ", "")})
    provider = AgentReachProvider(runner=runner)
    result = asyncio.run(provider.read(url))
    assert result.status is SourceStatus.FAILED
    assert "empty content" in (result.error or "")


def test_commands_override_merges_with_defaults() -> None:
    url = "https://example.com/custom"
    doctor_output = json.dumps({"channels": {}})
    runner = _runner(
        {
            ("fake-reader", url): (0, "自定义读取器正文", ""),
            ("agent-reach", "doctor", "--json"): (0, doctor_output, ""),
        }
    )
    provider = AgentReachProvider(
        runner=runner, commands={"read_web": ["fake-reader", "{url}"]}
    )
    read_result = asyncio.run(provider.read(url))
    doctor_result = asyncio.run(provider.doctor())
    assert read_result.status is SourceStatus.OK
    assert read_result.items.text == "自定义读取器正文"
    assert doctor_result.status is SourceStatus.OK  # 未覆盖的 doctor 仍走默认模板
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_agent_reach.py -v`
Expected: 全部 FAIL（`agent_reach` 模块不存在）

- [ ] **Step 3: 实现 agent_reach.py**

Create `src/wechat_traffic_agent/agent_reach.py`：

```python
"""Agent-Reach adapter: a CLI capability layer, not an HTTP service.

`agent-reach doctor --json` reports per-channel health and the active
backend; content is fetched by invoking the tools Agent-Reach manages
(docs show `curl -s https://r.jina.ai/{url}` for web reading, `bili` /
`twitter` CLIs per platform). This adapter runs configured command
templates through an injectable runner, so tests stay offline.

PR2 boundary: comments/author parsing needs real per-platform CLI output
shapes, which arrive in PR3 when backends are wired — until then those
protocols return UNAVAILABLE instead of guessing at parser formats.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    SourceResult,
    SourceStatus,
)

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
"""Runner contract: (returncode, stdout, stderr); callers branch on nonzero returncode.
A timeout returns returncode 124 (curl's convention) with the message in stderr."""

DEFAULT_COMMANDS: dict[str, list[str]] = {
    "doctor": ["agent-reach", "doctor", "--json"],
    "read_web": ["curl", "-s", "https://r.jina.ai/{url}"],
}

DEFAULT_TIMEOUT = 30.0


async def _subprocess_runner(cmd: list[str], timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return (124, "", f"command timed out after {timeout}s")
    returncode = process.returncode
    assert returncode is not None  # communicate() 完成后必有值
    return (
        returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class AgentReachProvider:
    def __init__(
        self,
        *,
        commands: dict[str, list[str]] | None = None,
        runner: Runner | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.commands = {**DEFAULT_COMMANDS, **(commands or {})}
        self.runner = runner or functools.partial(_subprocess_runner, timeout=timeout)

    async def _run(self, name: str, placeholders: dict[str, str]) -> tuple[int, str, str] | None:
        template = self.commands.get(name)
        if not template:
            return None
        cmd: list[str] = []
        for part in template:
            try:
                cmd.append(part.format(**placeholders))
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"command template {name!r} has invalid placeholders: {exc}"
                ) from exc
        return await self.runner(cmd)

    async def doctor(self) -> SourceResult[dict[str, Any]]:
        """Health of all channels; PR3 uses active_backend to pick backends."""
        observed_at = datetime.now(timezone.utc)
        try:
            outcome = await self._run("doctor", {})
        except ValueError as exc:
            return self._failed("doctor", str(exc), observed_at, {})
        if outcome is None:
            return self._failed("doctor", "doctor template not configured", observed_at, {})
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                "doctor", stderr.strip() or f"exit code {returncode}", observed_at, {}
            )
        try:
            data = json.loads(stdout)
        except ValueError:
            return self._failed("doctor", "doctor output is not JSON", observed_at, {})
        return SourceResult(
            source="agent-reach",
            platform="doctor",
            status=SourceStatus.OK,
            items=data,
            observed_at=observed_at,
        )

    async def read(self, url: str, *, platform: str = "generic_web") -> SourceResult[ContentSnapshot]:
        observed_at = datetime.now(timezone.utc)
        degenerate = ContentSnapshot(content_id="", platform=platform, url="", text="")
        try:
            outcome = await self._run("read_web", {"url": url})
        except ValueError as exc:
            return self._failed(platform, str(exc), observed_at, degenerate)
        if outcome is None:
            return self._failed(platform, "read_web template not configured", observed_at, degenerate)
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                platform, stderr.strip() or f"exit code {returncode}", observed_at, degenerate
            )
        text = stdout.strip()
        if not text:
            return self._failed(platform, "empty content", observed_at, degenerate)
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.OK,
            items=ContentSnapshot(content_id=url, platform=platform, url=url, text=text),
            observed_at=observed_at,
        )

    async def comments(self, content_id: str, *, platform: str) -> SourceResult[tuple[Comment, ...]]:
        return self._unavailable(
            platform,
            "comments backend not wired (needs platform CLI output shape; see PR3)",
            (),
        )

    async def author(self, author_id: str, *, platform: str) -> SourceResult[AuthorSnapshot]:
        return self._unavailable(
            platform,
            "author backend not wired (needs platform CLI output shape; see PR3)",
            AuthorSnapshot(author_id="", platform=platform),
        )

    def _failed(self, platform: str, error: str, observed_at: datetime, items) -> SourceResult:
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.FAILED,
            items=items,
            error=error,
            observed_at=observed_at,
        )

    def _unavailable(self, platform: str, reason: str, items) -> SourceResult:
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.UNAVAILABLE,
            items=items,
            error=reason,
            observed_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_agent_reach.py -v`
Expected: `8 passed`；全量 `python -m pytest -q` → `59 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add src/wechat_traffic_agent/agent_reach.py tests/test_agent_reach.py
git commit -m "feat: Agent-Reach subprocess adapter with doctor and web read

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: README + PR2 验收

**Files:**
- Modify: `README.md`（新增"互联网来源"节）

- [ ] **Step 1: README 新增章节**

在"反馈学习"节之后追加：

```markdown
## 互联网来源（PR2）

平台访问复用 OSS，不自建 scraper。所有来源通过 `SourceResult` 隔离故障：
单个平台失败不会中断扫描；平台没有的数据一律 `None`（unavailable），不强迫统一。

- **DailyHotApi**（广撒网热榜，best-effort）：自托管 `docker run -p 6688:6688 imsyy/dailyhotapi`，
  适配器走 `GET /{platform}`（weibo/zhihu/bilibili/douyin/toutiao 等），防御式解析。
- **Agent-Reach**（Top-N 深抓，CLI 能力层）：`agent-reach doctor --json` 报各 channel
  健康与 active_backend；正文读取用其管理的 `curl -s https://r.jina.ai/{url}`；
  评论/作者协议已定义，等 PR3 接好平台 CLI 后端再解析。
- **FixtureProvider**：离线三平台数据 + 故障注入，CI 与单元测试专用。
```

- [ ] **Step 2: PR2 验收清单核对（对照用户规格）**

Run:
```bash
python -m pytest -q
git status --short
git log --oneline -6
```
Expected:
- `59 passed in ...`
- 工作树只有 README.md 待提交（若出现非本会话产物，停下向用户确认）

- [ ] 验收项：
- [ ] fixture 能模拟至少 3 平台：`test_fixture_supports_at_least_three_platforms` ✓
- [ ] 单个平台失败不会中断：`test_fixture_broken_platform_fails_in_isolation`、`test_platform_failure_returns_failed_result_not_exception` ✓
- [ ] 所有结果保留 provenance：`test_source_result_carries_provenance_and_is_frozen`（source/platform/observed_at 全量断言）✓
- [ ] 无网络单元测试全通过：全部测试离线（MockTransport / 假 runner / fixture），无真实网络调用 ✓

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document PR2 internet sources

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## PR2 完成后的衔接

PR3（`feat/wechat-viral-radar`）消费本 PR：`ViralRadar` 用 DailyHotApi 广撒网 →
normalize/dedup/platform-local percentile 排名（rank 35 / engagement 25 / breakout 25 /
freshness 15，缺失重归一化，LLM 不排数字）→ Top 20 用 Agent-Reach 深抓 →
`ViralCandidate`。届时需要为 AgentReachProvider 增加真实平台 CLI 的 comments/author
模板与解析器，并把 `EngagementSnapshot` 的 producer 落在 deep fetch 上。
