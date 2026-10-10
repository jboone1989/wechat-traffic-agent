# Website 文章 → X Article 分发 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** website 每篇过审发布的文章通过 durable outbox 自动发布为 X Article（全文+LLM 配图计划），替代纯链接分享。

**Architecture:** 新增 `x_article` outbox 渠道与适配器 `publish_x_article`，复用既有 `prepare_article_media` 媒体管线与 `publish_article` 事务化浏览器传输；三层幂等（图片计划缓存 / 发布事务 / artifact 级 receipt 去重）。规格见 `docs/superpowers/specs/2026-10-10-website-to-x-article-design.md`。

**Tech Stack:** Python 3.11+, pydantic v2, pytest（asyncio.run 风格测试，monkeypatch 注入 fakes）。

**测试环境注意（Windows 本地）:** 运行 pytest 需 `PYTHONPATH="src;D:/ferro/agent-runtime/src"`，工作目录 `D:/ferro/wechat-traffic-agent`。下面所有 pytest 命令均已包含。

**关键既有接口（实现者无需再查）:**
- `_structured(runtime, task, contract, validate=None, *, max_tokens=2400)` — `wechat_traffic_agent/content_loop.py:293`；调 `runtime.cognitive_probe(prompt, max_tokens=...)`，用 `contract.model_validate_json` 解析，ValueError 时带错误再调一次修复。
- `ReceiptRuntime(runtime, root)` — `content_loop.py:188`；请求指纹缓存模型调用（同 prompt 重试不重复计费）。
- `ArticleImageSpec` — `x_article_media.py:13`；`role`/`mode(GRAPHIC|GENERATE|SEARCH)`/`prompt`/`query`/`after_text`/`diagram_labels`/`caption`/`alt_text`，跨字段约束见其 `model_validator`。
- `prepare_article_media(*, draft, folder, search=None, generator=None, selector=None)` — `x_article_media.py:98`；返回 `{"cover": {...,"path"}, "inline": [...]}`；空 images 自动原图封面。
- `XBrowserTransportRouter(state_root=...).publish_article(title=, text=, image_path=, transaction_path=, inline_images=)` — 返回带 `.post_id/.url` 的对象；崩溃可 reconcile，POSTED 短路。
- 测试注入模式：适配器在**函数体内** `from ...x_browser import ...`，测试 monkeypatch `x_browser.XBrowserTransportRouter` / `x_browser.x_browser_writes_enabled`（参考 `tests/test_publication_outbox.py:109-131`）。

---

### Task 1: 新模块骨架 + 正文渲染

**Files:**
- Create: `src/wechat_traffic_agent/platforms/x/x_article_distribution.py`
- Test: `tests/test_x_article_distribution.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_x_article_distribution.py`：

```python
"""Website article -> X Article distribution adapter tests."""
import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from wechat_traffic_agent.platforms.x import x_article_distribution
from wechat_traffic_agent.publication.errors import NonRetryablePublication
from wechat_traffic_agent.publication.event_store import PublicationEventStore
from wechat_traffic_agent.publication.models import DistributionTask, PublicationEvent

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def make_artifact(**overrides):
    values = dict(
        artifact_id="art-1", kind=ContentKind.RESEARCH, slug="example-research",
        title="Example Research Finding",
        summary="A supported finding with a concrete mechanism.",
        sections=(ContentSection(heading="Finding",
                                 paragraphs=("Something concrete and supported.",)),),
        created_at=NOW,
    )
    values.update(overrides)
    return ContentArtifact(**values)


def test_render_includes_headings_and_paragraphs():
    artifact = make_artifact(sections=(
        ContentSection(heading="Finding", paragraphs=("First supported point.", "Second point.")),
        ContentSection(heading="Limits", paragraphs=("Where it stops.",)),
    ))
    text = x_article_distribution.render_article_text(artifact)
    assert text == ("Finding\n\nFirst supported point.\n\nSecond point.\n\n"
                    "Limits\n\nWhere it stops.")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_x_article_distribution.py -v`
Expected: FAIL（`ModuleNotFoundError` 或 `AttributeError: render_article_text`）

- [ ] **Step 3: 最小实现**

创建 `src/wechat_traffic_agent/platforms/x/x_article_distribution.py`：

```python
"""Distribute audited website articles as native X Articles through the outbox."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from wechat_traffic_agent.content_artifact import ContentArtifact
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from wechat_traffic_agent.publication.errors import NonRetryablePublication
from wechat_traffic_agent.publication.models import DistributionTask, PublicationEvent
from wechat_traffic_agent.x_article_media import ArticleImageSpec, prepare_article_media

X_ARTICLE_DISTRIBUTION_DIR = "content/x_article_distribution"


class ArticleImagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: tuple[ArticleImageSpec, ...] = ()


def render_article_text(artifact: ContentArtifact) -> str:
    blocks = []
    for section in artifact.sections:
        blocks.append(section.heading)
        blocks.append("\n\n".join(section.paragraphs))
    return "\n\n".join(blocks)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_x_article_distribution.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/platforms/x/x_article_distribution.py tests/test_x_article_distribution.py && git commit -m "$(cat <<'EOF'
Add X Article distribution module with article text rendering

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 图片计划（校验先于缓存）

**Files:**
- Modify: `src/wechat_traffic_agent/platforms/x/x_article_distribution.py`
- Test: `tests/test_x_article_distribution.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_x_article_distribution.py`：

```python
import asyncio


class FakeRuntime:
    def __init__(self, response='{"images": []}'):
        self.response = response
        self.calls = 0

    async def cognitive_probe(self, prompt, max_tokens=2400):
        self.calls += 1
        return self.response


def test_image_plan_validated_then_cached(tmp_path):
    runtime = FakeRuntime(json.dumps({"images": [
        {"role": "COVER", "mode": "GENERATE", "prompt": "abstract data grid",
         "alt_text": "Abstract cover graphic"}]}))
    artifact = make_artifact()
    folder = tmp_path / "art-1"
    folder.mkdir()
    plan = asyncio.run(x_article_distribution._article_image_plan(runtime, artifact, folder))
    assert plan[0]["mode"] == "GENERATE"
    assert runtime.calls == 1
    assert json.loads((folder / "image_plan.json").read_text(encoding="utf-8"))[0]["mode"] == "GENERATE"
    asyncio.run(x_article_distribution._article_image_plan(runtime, artifact, folder))
    assert runtime.calls == 1


def test_image_plan_two_covers_rejected_and_not_cached(tmp_path):
    runtime = FakeRuntime(json.dumps({"images": [
        {"role": "COVER", "mode": "GRAPHIC", "alt_text": "cover one"},
        {"role": "COVER", "mode": "GRAPHIC", "alt_text": "cover two"}]}))
    folder = tmp_path / "art-1"
    folder.mkdir()
    with pytest.raises(ValueError, match="COVER"):
        asyncio.run(x_article_distribution._article_image_plan(runtime, make_artifact(), folder))
    assert not (folder / "image_plan.json").exists()
```

（`import asyncio` 放在测试文件顶部 import 区；`_article_image_plan` 是 async——`_structured` 为 async def。）

注意：`_structured` 对 ValueError 会做一次修复重调（FakeRuntime 返回同样的坏响应），最终仍抛 ValueError——两个 COVER 的断言不受影响。

- [ ] **Step 2: 运行确认失败**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_x_article_distribution.py -v`
Expected: FAIL（`AttributeError: _article_image_plan`）

- [ ] **Step 3: 实现**

追加到 `x_article_distribution.py`（`ArticleImagePlan` 之后）：

```python
def _image_plan_prompt(artifact: ContentArtifact) -> str:
    outline = "\n".join(
        section.heading + ": " + section.paragraphs[0][:200]
        for section in artifact.sections)
    return (
        "ARTICLE_IMAGE_PLAN: Declare images for Ferro's X Article. "
        'Return JSON only: {"images": [ArticleImageSpec ...]} with at most one COVER. '
        "modes: GRAPHIC (deterministic original cover or labelled inline diagram), "
        "GENERATE (prompt for image generation), SEARCH (query for licensed Commons photo). "
        "GENERATE requires prompt; SEARCH requires query; inline GRAPHIC requires 2-6 diagram_labels; "
        "every image needs alt_text of at least 5 characters; caption is optional. "
        "An empty list is valid and yields an original graphic cover. "
        "Never claim generated art is a real product screenshot or evidence. "
        "Inline images should set after_text to an exact sentence copied from the article text "
        "that the image follows. Choose images only when they aid comprehension.\n"
        + json.dumps({"title": artifact.title, "summary": artifact.summary,
                      "topics": list(artifact.tags), "outline": outline},
                     ensure_ascii=False))


async def _article_image_plan(runtime, artifact: ContentArtifact, artifact_dir: Path) -> list[dict]:
    plan_path = artifact_dir / "image_plan.json"
    if plan_path.exists():
        return json.loads(plan_path.read_text(encoding="utf-8"))
    from wechat_traffic_agent.content_loop import ReceiptRuntime, _structured

    def checked(plan: ArticleImagePlan) -> None:
        if sum(1 for image in plan.images if image.role == "COVER") > 1:
            raise ValueError("an X Article has at most one COVER image")

    plan = await _structured(
        ReceiptRuntime(runtime, artifact_dir / "image-plan-model-calls"),
        _image_plan_prompt(artifact), ArticleImagePlan,
        validate=checked, max_tokens=2000)
    images = [image.model_dump(mode="json") for image in plan.images]
    plan_path.write_text(json.dumps(images, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return images
```

- [ ] **Step 4: 运行确认通过**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_x_article_distribution.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/platforms/x/x_article_distribution.py tests/test_x_article_distribution.py && git commit -m "$(cat <<'EOF'
Add LLM image plan drafting with validate-before-cache for X Articles

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `publish_x_article` 适配器

**Files:**
- Modify: `src/wechat_traffic_agent/platforms/x/x_article_distribution.py`
- Test: `tests/test_x_article_distribution.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_x_article_distribution.py`：

```python
class FakePublication:
    post_id = "555"
    url = "https://x.com/ferro/status/555"


class FakeRouter:
    calls = []
    fail_first = False

    def __init__(self, *, state_root):
        pass

    async def publish_article(self, *, title, text, image_path, transaction_path, inline_images=()):
        if FakeRouter.fail_first:
            FakeRouter.fail_first = False
            raise RuntimeError("X unavailable")
        FakeRouter.calls.append({"title": title, "text": text})
        return FakePublication()


@pytest.fixture
def x_enabled(monkeypatch):
    from wechat_traffic_agent.platforms.x import x_browser
    monkeypatch.setattr(x_browser, "x_browser_writes_enabled", lambda: True)
    monkeypatch.setattr(x_browser, "XBrowserTransportRouter", FakeRouter)


def fake_media(*, draft, folder, **kwargs):
    cover = folder / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"png-bytes")
    return {"cover": {"path": str(cover), "attribution": None}, "inline": []}


def store_article(tmp_path, artifact):
    records = ChannelRecords(tmp_path)
    with records.repo.connect() as db:
        db.execute(
            "INSERT INTO articles(artifact_id,kind,slug,route,title,summary,created_at,updated_at,"
            "content_json,content_sha256,record_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (artifact.artifact_id, artifact.kind.value, artifact.slug,
             "/research/" + artifact.slug, artifact.title, artifact.summary,
             artifact.created_at.isoformat(), NOW.isoformat(),
             artifact.model_dump_json(), "hash", "website"))


def make_event(store, artifact, event_id, content_hash):
    event = PublicationEvent(event_id, artifact.artifact_id,
                             "https://example.org/research/" + artifact.slug,
                             artifact.title, artifact.summary, list(artifact.tags),
                             NOW, content_hash)
    store.save_event(event)
    return event


def test_publishes_verified_article(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    FakeRouter.calls = []
    row = x_article_distribution.publish_x_article(
        event, DistributionTask("t1", "e1", "x_article"),
        state_root=tmp_path, model_runtime=FakeRuntime())
    assert row == {"status": "POSTED", "url": "https://x.com/ferro/status/555", "post_id": "555"}
    assert FakeRouter.calls[0]["title"] == artifact.title
    assert "Finding" in FakeRouter.calls[0]["text"]
    receipt = json.loads(
        (tmp_path / "content/x_article_distribution/art-1/publication_receipt.json")
        .read_text(encoding="utf-8"))
    assert receipt["post_id"] == "555"


def test_image_plan_drafted_once_across_retries(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    FakeRouter.calls = []
    FakeRouter.fail_first = True
    runtime = FakeRuntime()
    task = DistributionTask("t1", "e1", "x_article")
    with pytest.raises(RuntimeError, match="X unavailable"):
        x_article_distribution.publish_x_article(event, task, state_root=tmp_path, model_runtime=runtime)
    row = x_article_distribution.publish_x_article(event, task, state_root=tmp_path, model_runtime=runtime)
    assert row["status"] == "POSTED"
    assert runtime.calls == 1


def test_revised_event_reuses_original_receipt(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    first = make_event(store, artifact, "e1", "h1")
    second = make_event(store, artifact, "e2", "h2")
    FakeRouter.calls = []
    runtime = FakeRuntime()
    x_article_distribution.publish_x_article(
        first, DistributionTask("t1", "e1", "x_article"),
        state_root=tmp_path, model_runtime=runtime)
    row = x_article_distribution.publish_x_article(
        second, DistributionTask("t2", "e2", "x_article"),
        state_root=tmp_path, model_runtime=runtime)
    assert row["url"] == "https://x.com/ferro/status/555"
    assert len(FakeRouter.calls) == 1


def test_media_failure_propagates_for_retry(tmp_path, x_enabled, monkeypatch):
    def broken_media(*, draft, folder, **kwargs):
        raise RuntimeError("no licensed image found")

    monkeypatch.setattr(x_article_distribution, "prepare_article_media", broken_media)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    with pytest.raises(RuntimeError, match="no licensed image"):
        x_article_distribution.publish_x_article(
            event, DistributionTask("t1", "e1", "x_article"),
            state_root=tmp_path, model_runtime=FakeRuntime())


def test_oversized_text_is_not_retryable(tmp_path, x_enabled, monkeypatch):
    monkeypatch.setattr(x_article_distribution, "prepare_article_media", fake_media)
    artifact = make_artifact(sections=(ContentSection(
        heading="Long", paragraphs=("word " * 6000,)),))
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    with pytest.raises(NonRetryablePublication):
        x_article_distribution.publish_x_article(
            event, DistributionTask("t1", "e1", "x_article"),
            state_root=tmp_path, model_runtime=FakeRuntime())


def test_writes_disabled_is_rejected(tmp_path, monkeypatch):
    from wechat_traffic_agent.platforms.x import x_browser
    monkeypatch.setattr(x_browser, "x_browser_writes_enabled", lambda: False)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    with pytest.raises(RuntimeError, match="disabled"):
        x_article_distribution.publish_x_article(
            event, DistributionTask("t1", "e1", "x_article"),
            state_root=tmp_path, model_runtime=FakeRuntime())


def test_inline_caption_and_credits_are_injected(tmp_path, x_enabled, monkeypatch):
    def media_with_inline(*, draft, folder, **kwargs):
        cover = folder / "cover.png"
        cover.write_bytes(b"png")
        inline_png = folder / "inline.png"
        inline_png.write_bytes(b"png2")
        return {"cover": {"path": str(cover), "attribution": None},
                "inline": [{"path": str(inline_png), "caption": "Figure 1: flow",
                            "alt_text": "Diagram of the flow",
                            "after_text": "Something concrete and supported.",
                            "attribution": "Photo by Someone, CC BY 4.0"}]}

    monkeypatch.setattr(x_article_distribution, "prepare_article_media", media_with_inline)
    artifact = make_artifact()
    store_article(tmp_path, artifact)
    store = PublicationEventStore(tmp_path)
    event = make_event(store, artifact, "e1", "h1")
    FakeRouter.calls = []
    x_article_distribution.publish_x_article(
        event, DistributionTask("t1", "e1", "x_article"),
        state_root=tmp_path, model_runtime=FakeRuntime())
    text = FakeRouter.calls[0]["text"]
    assert "Something concrete and supported.\n\nFigure 1: flow" in text
    assert "Image credits:\nPhoto by Someone, CC BY 4.0" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_x_article_distribution.py -v`
Expected: 新增 7 个 FAIL（`AttributeError: publish_x_article`），原有 3 个 PASS

- [ ] **Step 3: 实现适配器**

追加到 `x_article_distribution.py`：

```python
def _load_artifact(state_root: Path, artifact_id: str) -> ContentArtifact:
    records = ChannelRecords(state_root)
    with records.repo.connect() as db:
        row = db.execute("SELECT content_json FROM articles WHERE artifact_id=?",
                         (artifact_id,)).fetchone()
    if row is None:
        raise NonRetryablePublication("article content is missing for " + artifact_id)
    return ContentArtifact.model_validate_json(row["content_json"])


def publish_x_article(event: PublicationEvent, task: DistributionTask, *,
                      state_root: Path, model_runtime) -> dict:
    from wechat_traffic_agent.platforms.x.x_browser import (
        X_PREMIUM_LONG_POST_MAX_CHARS,
        XBrowserTransportRouter,
        x_browser_writes_enabled,
    )
    if task.channel != "x_article":
        raise ValueError("X Article adapter cannot publish other channels")
    if not x_browser_writes_enabled():
        raise RuntimeError("X publishing is disabled")
    artifact_dir = Path(state_root) / X_ARTICLE_DISTRIBUTION_DIR / event.artifact_id
    receipt_path = artifact_dir / "publication_receipt.json"
    if receipt_path.exists():
        prior = json.loads(receipt_path.read_text(encoding="utf-8"))
        return {"status": "POSTED", "url": prior["url"], "post_id": prior["post_id"]}
    artifact = _load_artifact(Path(state_root), event.artifact_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    text = render_article_text(artifact)
    images = asyncio.run(_article_image_plan(model_runtime, artifact, artifact_dir))
    media = asyncio.run(prepare_article_media(
        draft={"title": artifact.title, "images": images}, folder=artifact_dir))
    for image in media["inline"]:
        caption = image.get("caption") or image["alt_text"]
        anchor = image.get("after_text")
        if anchor:
            if text.count(anchor) != 1:
                raise NonRetryablePublication(
                    "image anchor is missing or ambiguous: " + anchor[:80])
            if caption not in text:
                text = text.replace(anchor, anchor + "\n\n" + caption, 1)
        else:
            text += "\n\n" + caption
    credits = [row.get("attribution") for row in [media["cover"], *media["inline"]]
               if row.get("attribution")]
    if credits:
        text += "\n\nImage credits:\n" + "\n".join(credits)
    if len(text) > X_PREMIUM_LONG_POST_MAX_CHARS:
        raise NonRetryablePublication("article text exceeds the X Article length bound")
    publication = asyncio.run(
        XBrowserTransportRouter(state_root=Path(state_root)).publish_article(
            title=artifact.title, text=text, image_path=media["cover"]["path"],
            inline_images=media["inline"],
            transaction_path=artifact_dir / "x_article_transaction.json"))
    post_id = str(publication.post_id or "").strip()
    url = str(publication.url or "").strip()
    if not post_id or not url.startswith("https://x.com/") or post_id not in url:
        raise RuntimeError("Jev did not return a verified X Article URL")
    receipt_path.write_text(json.dumps(
        {"url": url, "post_id": post_id, "event_id": event.event_id,
         "published_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "POSTED", "url": url, "post_id": post_id}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_x_article_distribution.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/platforms/x/x_article_distribution.py tests/test_x_article_distribution.py && git commit -m "$(cat <<'EOF'
Add publish_x_article outbox adapter with three-layer idempotency

Plan cache, publish transaction and per-artifact receipt dedup; media
failures retry per outbox semantics and oversized text is non-retryable.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: dispatch 默认渠道 + 存量测试更新

**Files:**
- Modify: `src/wechat_traffic_agent/publication/dispatch.py:23`
- Modify: `tests/test_binance_square_distribution.py`（两处 channel 断言）
- Test: `tests/test_binance_square_distribution.py`、`tests/test_publication_outbox.py`

- [ ] **Step 1: 更新存量断言（先改测试）**

`tests/test_binance_square_distribution.py` 中两处：

第 43 行附近（`test_producer_has_no_binance_dependency`）：
```python
# 旧
assert store.list_tasks()[0]["channel"] == "x"
# 新
assert store.list_tasks()[0]["channel"] == "x_article"
```

第 122 行附近（`test_retired_consumer_never_queues_new_or_old_website_events`）：
```python
# 旧
assert all(t["channel"] == "x" for t in store.list_tasks())
# 新
assert all(t["channel"] == "x_article" for t in store.list_tasks())
```

- [ ] **Step 2: 运行确认失败**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_binance_square_distribution.py -v`
Expected: 上述两个测试 FAIL（生产者仍 enqueue "x"）

- [ ] **Step 3: 改 dispatch 默认渠道**

`src/wechat_traffic_agent/publication/dispatch.py:23`：

```python
# 旧
    channels: tuple[str, ...] = ("x",)
# 新
    channels: tuple[str, ...] = ("x_article",)
```

- [ ] **Step 4: 运行确认通过（含 outbox 回归）**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_binance_square_distribution.py tests/test_publication_outbox.py -v`
Expected: 全部 PASS（`test_publication_outbox.py` 用的 "x" 是显式 adapters 字典键，不受默认值影响；若 `test_daily_distribution_attempts_replace_x_success_quota` 有默认渠道断言失败，按同样方式把断言更新为 `"x_article"`）

- [ ] **Step 5: Commit**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/publication/dispatch.py tests/test_binance_square_distribution.py && git commit -m "$(cat <<'EOF'
Website publications fan out to x_article instead of link share

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: CHECK_X worker 接线

**Files:**
- Modify: `scripts/run_check_x_worker.py:30`（import）、`:115-120`（adapters）

- [ ] **Step 1: 换 import**

```python
# 旧（第 30 行）
from wechat_traffic_agent.publication.x_distribution import publish_x
# 新
from wechat_traffic_agent.platforms.x.x_article_distribution import publish_x_article
```

- [ ] **Step 2: 换 adapters**

```python
# 旧（第 115-120 行）
                distribution = DistributionWorker(
                    PublicationEventStore(state_root),
                    {"x": lambda event, task: publish_x(
                        event, task, state_root=state_root
                    )},
                ).run_once()
# 新
                distribution = DistributionWorker(
                    PublicationEventStore(state_root),
                    {"x_article": lambda event, task: publish_x_article(
                        event, task, state_root=state_root,
                        model_runtime=cognition_runtime
                    )},
                ).run_once()
```

- [ ] **Step 3: 语法与导入冒烟**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -c "import ast; ast.parse(open('scripts/run_check_x_worker.py', encoding='utf-8').read())"`
Expected: 无输出（解析通过；不 import 整个脚本——它 import 浏览器栈，本地环境可能缺依赖）

- [ ] **Step 4: Commit**

```bash
cd D:/ferro/wechat-traffic-agent && git add scripts/run_check_x_worker.py && git commit -m "$(cat <<'EOF'
CHECK_X worker drains x_article tasks with the cognition runtime

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 全量回归

- [ ] **Step 1: 跑全量测试套件**

Run: `cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/ -x -q`
Expected: 全部 PASS。若出现其他假设默认渠道为 "x" 的断言失败，逐个把该断言的 `"x"` 更新为 `"x_article"` 并纳入本次提交（只改渠道断言，不改测试语义）。

- [ ] **Step 2: 如有修复则提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add -u tests/ && git commit -m "$(cat <<'EOF'
Update remaining channel assertions for x_article default

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

若无修复则跳过提交（无空提交）。

- [ ] **Step 3: 报告完成**

报告：新增/修改的文件清单、测试结果（新增 10 个、更新的断言位置）、遗留事项（服务器部署按 [[ferro-server-topology]]，本计划不含部署）。

---

## 完成后（不在本计划内）

- 服务器部署与重启（supervisor 拉起 CHECK_X worker）——单独执行，参照 ferro-server-topology 记忆。
- 部署后验证：观察下一轮 website 发布后 `state/content/x_article_distribution/<artifact_id>/publication_receipt.json` 出现，且 X 上出现对应 Article。
