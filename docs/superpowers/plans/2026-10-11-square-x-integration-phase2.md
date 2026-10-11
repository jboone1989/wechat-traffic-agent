# Square/X 两线打通（个性分化二期任务 1）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** X 与 Square 消费同一份 mind projection 与同一个立场账本；X 观察回流为账本证据、可登记 X 原生立场；账本唯一、结算唯一。

**Architecture:** 把 square_observation 的 mind 投影提升为 `content_mind.public_mind_context` 共享函数；`XAutonomyLoop` 增加 `mind` 参数并在表达 payload 注入（主进程只有这一个注入面）；`PositionLedger` 升级（source 归因、attach_post 跨面记账、x_observe 证据回流、threading+fcntl 两层写锁）；observe 周期做零 LLM 话题匹配回流证据 + 一次 X_POSITION_SCAN 结构化调用登记 X 原生立场。

**Tech Stack:** Python 3.10+ / pydantic / 标准库（threading, fcntl, contextmanager）。无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-11-square-x-integration-design.md`（范围 C 已获用户批准）

**测试命令（每个任务都用这条，替换 `<files>`）：**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest <files> -v
```

**部署拓扑红线（Task 6 用）：** 服务器 ubuntu@43.133.181.149，`current` → releases/05c99a1，叠加部署不翻新 worktree；`git -c core.autocrlf=false archive`（CRLF 陷阱）+ md5 双侧核对；重启 `sudo -n systemctl restart content-agent.service`；不建备份目录；不碰 agent-runtime。

**提交规则：** 只 add 本计划明确列出的文件（仓库有用户未提交 WIP，严禁 `git add -A`/`git add .`）；提交信息以 `Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>` 结尾。

---

### Task 0: 诊断 X 发布管线静默（服务器，前置结论，不阻塞编码任务）

**目的：** 2026-10-11 一期部署后 X expression 调用为零、唯一 post-transaction 停在 `PUBLICATION_UNVERIFIED`（部署前 5 小时）。须查明原因并（若是代码 bug）修复，确认 X 线能承载发帖后才允许 Task 6 部署。

**Files:** 无代码改动（诊断）；若查出 wechat-traffic-agent 代码 bug，修复补丁并入对应任务文件。

- [ ] **Step 1: 收集服务器状态**

```bash
ssh ubuntu@43.133.181.149 '
  echo "=== status ==="; cat content-agent-state/application/content/x/status.json 2>/dev/null | tail -c 2000
  echo "=== blocker ==="; cat content-agent-state/application/content/x/publication_blocker.json 2>/dev/null
  echo "=== transactions ==="; ls -la content-agent-state/application/content/x/post-transactions/ 2>/dev/null | tail -5
  echo "=== env ==="; systemctl show content-agent.service -p Environment | tr " " "\n" | grep -iE "X_|CONTENT_X" ; grep -iE "X_|CONTENT_X" content-agent/current/.env.ferro-llm 2>/dev/null
  echo "=== x dir recent ==="; find content-agent-state/application/content/x -newermt "2026-10-11 02:45" -type f | head -20
'
```

- [ ] **Step 2: 收集服务日志中的 X 表达痕迹**

```bash
ssh ubuntu@43.133.181.149 'sudo -n journalctl -u content-agent.service --since "2026-10-11 02:45" --no-pager | grep -iE "x_expression|X_EXPRESSION|after_content_cycle|L1_X_STRATEGY|publication_blocker|XBrowserPublication|CHECK_X" | tail -60'
```

- [ ] **Step 3: 判定与处置**

对以下候选逐一排除：① `x_publication_enabled()`/`x_writes_enabled()` 环境开关关闭（x_mcp.py 读 env）；② `after_content_cycle` 未被调用（orchestration/social.py:398-411 上游条件）；③ L1 `x_strategy` HOLD 拦截（x_social.py:863-877，状态落盘 `social_expression_drafts.json`/x_strategy 产物）；④ `_active_publication_blocker` ACTIVE 拦截（publication_blocker.json）；⑤ 浏览器登录态失效（identity/publish 失败日志）。

- 环境开关关闭 → 报告用户征询是否打开，不擅自改 env。
- 代码 bug 在本仓 5 个目标文件内 → 修复（补测试）后并入本计划。
- 登录态/浏览器/账号问题 → **升级给用户**，Task 6 部署门改为"用户明确同意在 X 静默下先部署打通改动"（打通改动在 X 静默时是无害休眠的）。
- 结论写进本文件末尾的"Task 0 结论"小节（日期、根因、处置）。

- [ ] **Step 4: 记录结论**

在本计划文档末尾追加 `## Task 0 结论（YYYY-MM-DD）`：根因、证据（日志行）、处置、X 线是否可承载发帖（是/否+理由）。

---

### Task 1: PositionLedger v2 — source 归因、attach_post、append_evidence、post_ids 归一化、两层写锁

**Files:**
- Modify: `src/wechat_traffic_agent/position_ledger.py`
- Modify: `tests/test_position_ledger.py`

**背景：** 一期账本 post_ids 是裸字符串（draft hash）、evidence_refs 是字符串、register 无来源、写路径无锁。二期主进程与 X worker 进程都会写 positions.json，需要跨进程文件锁（服务器 Linux fcntl；Windows 开发机无 fcntl，退化为进程内 threading 锁——测试仍能验证串行化语义）。**关键陷阱：** dict 不可哈希，`list(dict.fromkeys(...))` 遇 dict 条目会 TypeError——现有 transition 的去重必须换掉。

- [ ] **Step 1: 写失败测试**

在 `tests/test_position_ledger.py` 追加（文件顶部已有 NOW/_ledger 助手与 `import json`；需补 `import threading`）：

```python
def test_register_records_source_and_normalized_post_ids(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d1", now=NOW, source="x")
    assert row["source"] == "x"
    assert row["post_ids"] == [{"id": "d1", "surface": "square"}]
    row2 = ledger.register(topic="t2", proposition="p2", falsifier="f2", evidence_refs=(),
                           draft_id="d2", now=NOW)
    assert row2["source"] == "square"


def test_attach_post_is_idempotent_and_tolerates_legacy_strings(tmp_path: Path):
    root = tmp_path / "content_mind"
    root.mkdir(parents=True)
    (root / "positions.json").write_text(json.dumps({"positions": [
        {"position_id": "pos-legacy", "topic": "t", "proposition": "p", "falsifier": "f",
         "status": "OPEN", "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
         "evidence_refs": [], "post_ids": ["legacy-draft"], "resolution": None}]}),
        encoding="utf-8")
    ledger = PositionLedger(root)
    ledger.attach_post("pos-legacy", "x-post:1", surface="x")
    row = ledger.get("pos-legacy")
    assert row["post_ids"] == [{"id": "legacy-draft", "surface": "square"},
                               {"id": "x-post:1", "surface": "x"}]
    ledger.attach_post("pos-legacy", "x-post:1", surface="x")
    assert len(ledger.get("pos-legacy")["post_ids"]) == 2
    with pytest.raises(ValueError):
        ledger.attach_post("pos-missing", "x-post:2", surface="x")


def test_transition_keeps_dict_entries_and_dedups(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d1", now=NOW)
    pid = row["position_id"]
    ledger.attach_post(pid, "x-post:9", surface="x")
    done = ledger.transition(pid, verdict="CONFIRMED", resolution="结算数据符合",
                             evidence_refs=("web:e2",), draft_id="d2", now=NOW)
    assert done["post_ids"] == [{"id": "d1", "surface": "square"},
                                {"id": "x-post:9", "surface": "x"},
                                {"id": "d2", "surface": "square"}]


def test_append_evidence_caps_fifo_and_keeps_string_refs(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f",
                          evidence_refs=("web:s1",), draft_id="d1", now=NOW)
    pid = row["position_id"]
    for i in range(12):
        ledger.append_evidence(pid, {"source": "x_observe", "ref": f"x-post:{i}",
                                     "at": NOW.isoformat(), "note": f"n{i}"})
    refs = ledger.get(pid)["evidence_refs"]
    strings = [r for r in refs if isinstance(r, str)]
    dicts = [r for r in refs if isinstance(r, dict)]
    assert strings == ["web:s1"]
    assert len(dicts) == 10
    assert dicts[0]["ref"] == "x-post:2"
    assert dicts[-1]["ref"] == "x-post:11"
    ledger.append_evidence(pid, {"ref": "x-post:11", "at": NOW.isoformat(), "note": "dup"})
    assert len([r for r in ledger.get(pid)["evidence_refs"] if isinstance(r, dict)]) == 10
    assert len(strings) == 1  # 字符串证据从不被淘汰


def test_append_evidence_requires_ref_and_bounds_note(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    with pytest.raises(ValueError):
        ledger.append_evidence(row["position_id"], {"note": "no ref"})
    ledger.append_evidence(row["position_id"], {"source": "x_observe", "ref": "x-post:1",
                                                "at": NOW.isoformat(), "note": "长" * 300})
    assert len(ledger.get(row["position_id"])["evidence_refs"][-1]["note"]) == 120


def test_topic_matches_tokens_and_chinese():
    assert topic_matches("sol-staking", "SOL staking inflows doubled")
    assert topic_matches("质押", "SOL 质押流入持续扩张")
    assert topic_matches("strk+费率", "strk funding rate flipped positive")
    assert not topic_matches("sol-staking", "eth gas fees dropped")
    assert not topic_matches("", "anything")


def test_writes_serialize_under_lock(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    pid = row["position_id"]
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            for j in range(5):
                ledger.attach_post(pid, f"post-{i}-{j}", surface="x")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(ledger.get(pid)["post_ids"]) == 21  # 1 注册 + 4*5，零丢失
```

同文件顶部 import 行 `from wechat_traffic_agent.position_ledger import PositionLedger` 改为：

```python
from wechat_traffic_agent.position_ledger import PositionLedger, topic_matches
```

并把既有测试 `test_register_creates_open_position` 的断言（第 25 行）改为归一化格式：

```python
    assert row["post_ids"] == [{"id": "d1", "surface": "square"}]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_position_ledger.py -v
```

预期：新测试 FAIL（TypeError/AttributeError/ImportError），既有 8 个 PASS。

- [ ] **Step 3: 实现**

`src/wechat_traffic_agent/position_ledger.py` 顶部 import 区改为：

```python
"""Committed falsifiable positions: continuity and accountability for published claims."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import fcntl
except ImportError:  # Windows 开发/测试环境：只有进程内锁
    fcntl = None

LEDGER_SCHEMA = 1
ABANDON_AFTER = timedelta(days=5)
RESOLUTION_WINDOW = timedelta(days=7)
EVIDENCE_CAP = 10
_TERMINAL = {"CONFIRMED", "REFUTED", "ABANDONED"}

_WRITE_LOCK = threading.RLock()
```

模块级新增（`_aware` 之后）：

```python
def _post_entry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        pid = str(value.get("id") or "").strip()
        return {"id": pid, "surface": str(value.get("surface") or "square")} if pid else None
    text = str(value or "").strip()
    return {"id": text, "surface": "square"} if text else None


def _dedup(values: list[Any]) -> list[Any]:
    # dict 不可哈希，不能用 dict.fromkeys；条目混合 str/dict。
    seen: set[str] = set()
    out: list[Any] = []
    for value in values:
        key = (json.dumps(value, sort_keys=True, ensure_ascii=False)
               if isinstance(value, dict) else str(value))
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def topic_matches(topic: str, text: str) -> bool:
    tokens = [t for t in re.split(r"[^0-9a-z\u4e00-\u9fff]+", str(topic).casefold()) if t]
    if not tokens:
        return False
    hay = str(text).casefold()
    return any(token in hay for token in tokens)
```

`_load()` 的 clean 循环里，`clean.append(item)` 之前插入归一化：

```python
            item["post_ids"] = [e for e in (_post_entry(v) for v in (item.get("post_ids") or [])) if e]
            clean.append(item)
```

类内新增锁上下文（`__init__` 之后；**只用于公共写方法，`_sweep`/`_load`/`_save` 保持无锁内部实现**，`_sweep` 只会被已持锁的公共写方法调用）：

```python
    @contextmanager
    def _locked(self) -> Iterator[None]:
        with _WRITE_LOCK:
            if fcntl is None:
                yield
                return
            handle = os.open(self.root / "positions.lock", os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                os.close(handle)
```

`register` 加 `source` 参数并持锁（签名与行内容变化如下；校验、sweep、sha1 逻辑不变）：

```python
    def register(self, *, topic: str, proposition: str, falsifier: str,
                 evidence_refs: tuple[str, ...] | list[str], draft_id: str,
                 now: datetime, source: str = "square") -> dict[str, Any]:
        topic, proposition, falsifier = topic.strip(), proposition.strip(), falsifier.strip()
        if not topic or not proposition or not falsifier:
            raise ValueError("position requires topic, proposition and falsifier")
        with self._locked():
            self._sweep(now)
            rows = [row for row in self._load() if self._valid(row)]
            digest = hashlib.sha1(f"{topic}:{proposition}".encode("utf-8")).hexdigest()[:12]
            row = {
                "position_id": "pos-" + digest + "-" + now.strftime("%Y%m%d%H%M%S"),
                "topic": topic, "proposition": proposition, "falsifier": falsifier,
                "status": "OPEN", "created_at": now.isoformat(), "updated_at": now.isoformat(),
                "evidence_refs": [str(ref) for ref in evidence_refs],
                "post_ids": [_post_entry(draft_id)], "resolution": None,
                "source": source,
            }
            rows.append(row)
            self._save(rows)
            return dict(row)
```

`transition` 持锁并把两处 `dict.fromkeys` 去重换成 `_dedup`（evidence_refs 与 post_ids）：

```python
    def transition(self, position_id: str, *, verdict: str, resolution: str,
                   evidence_refs: tuple[str, ...] | list[str], draft_id: str,
                   now: datetime) -> dict[str, Any]:
        if verdict not in {"CONFIRMED", "REFUTED", "ABANDONED"}:
            raise ValueError("verdict must be CONFIRMED, REFUTED or ABANDONED")
        with self._locked():
            self._sweep(now)
            rows = self._load()
            for row in rows:
                if row.get("position_id") != position_id:
                    continue
                if row.get("status") != "OPEN":
                    raise ValueError(f"position {position_id} is {row.get('status')}, not OPEN")
                if verdict == "REFUTED" and not str(resolution or "").strip():
                    raise ValueError("REFUTED requires a resolution explaining what was wrong")
                row["status"] = verdict
                row["resolution"] = str(resolution or "").strip() or None
                row["updated_at"] = now.isoformat()
                row["evidence_refs"] = _dedup([
                    *row.get("evidence_refs", []), *(str(ref) for ref in evidence_refs)])
                if str(draft_id):
                    row["post_ids"] = _dedup([
                        *row.get("post_ids", []),
                        {"id": str(draft_id), "surface": "square"}])
                self._save(rows)
                return dict(row)
            raise ValueError(f"position {position_id} not found")
```

类内新增两个公共方法（`transition` 之后）：

```python
    def attach_post(self, position_id: str, post_id: str, *, surface: str = "square") -> dict[str, Any]:
        entry = _post_entry({"id": post_id, "surface": surface})
        if entry is None:
            raise ValueError("attach_post requires a non-empty post_id")
        with self._locked():
            rows = [row for row in self._load() if self._valid(row)]
            for row in rows:
                if row.get("position_id") != position_id:
                    continue
                key = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                existing = [
                    json.dumps(e, sort_keys=True, ensure_ascii=False)
                    for e in row.get("post_ids", [])
                ]
                if key not in existing:
                    row["post_ids"] = [*row.get("post_ids", []), entry]
                    self._save(rows)
                return dict(row)
            raise ValueError(f"position {position_id} not found")

    def append_evidence(self, position_id: str, entry: Mapping[str, Any],
                        *, cap: int = EVIDENCE_CAP) -> dict[str, Any]:
        item = {
            "source": str(entry.get("source") or "x_observe"),
            "ref": str(entry.get("ref") or "").strip(),
            "at": str(entry.get("at") or ""),
            "note": str(entry.get("note") or "")[:120],
        }
        if not item["ref"]:
            raise ValueError("evidence entry requires ref")
        with self._locked():
            rows = [row for row in self._load() if self._valid(row)]
            for row in rows:
                if row.get("position_id") != position_id:
                    continue
                refs = row.get("evidence_refs", [])
                strings = [r for r in refs if not isinstance(r, dict)]
                dicts = _dedup([r for r in refs if isinstance(r, dict)])
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key not in {json.dumps(r, sort_keys=True, ensure_ascii=False) for r in dicts}:
                    dicts = [*dicts, item][-cap:]
                row["evidence_refs"] = [*strings, *dicts]
                self._save(rows)
                return dict(row)
            raise ValueError(f"position {position_id} not found")
```

- [ ] **Step 4: 跑测试确认全绿**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_position_ledger.py -v
```

预期：15 PASS（8 旧 + 7 新）。

- [ ] **Step 5: 跑受波及的既有套件**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_square_observation.py tests/test_owner_decision_positions.py tests/test_content_mind.py -v
```

预期：与一期基线一致（无新增失败）。

- [ ] **Step 6: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/position_ledger.py tests/test_position_ledger.py && git commit -m "$(cat <<'EOF'
feat: position ledger v2 — surface 归因、attach_post、x_observe 证据回流、两层写锁

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 提升 public_mind_context 到 content_mind，Square 委托调用

**Files:**
- Modify: `src/wechat_traffic_agent/content_mind.py`（模块级新增函数）
- Modify: `src/wechat_traffic_agent/square_observation.py`（删除 `_bounded_text`/`_mind_context`，改 import）
- Modify: `tests/test_square_observation.py`（import 改到新家）

**背景：** square_observation.py:62-93 的 `_bounded_text` + `_mind_context` 是 Square 专用实现；二期 X 也用同一投影。提升为 `content_mind.public_mind_context`，行为逐字节不变（含 staleness 过滤、300 字有界、≤6 OPEN、≤4 resolutions）。`_bounded_text` 在 square_observation 中只被 `_mind_context` 使用，可整体搬走。

- [ ] **Step 1: 写失败测试**

在 `tests/test_square_observation.py` 中：第 9 行 import 里的 `_mind_context,` 移除（保留该行其他名字），另起一行加：

```python
from wechat_traffic_agent.content_mind import public_mind_context
```

文件内 3 处调用 `_mind_context(...)`（:337、:345、:363）全部改名 `public_mind_context(...)`。

新增测试（追加到文件尾部，沿用已有 `_FakeMind`）：

```python
def test_public_mind_context_is_shared_entrypoint():
    projection = {
        "beliefs": [], "interests": [], "open_inquiries": [],
        "interest_terrain": [],
        "positions": {"open": [{
            "position_id": "pos-x", "topic": "t", "proposition": "p", "falsifier": "f",
            "age_days": 1.0, "source": "x",
        }], "recent_resolutions": []},
    }
    ctx = public_mind_context(_FakeMind(projection))
    assert ctx["positions_open"][0]["source"] == "x"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_square_observation.py -v
```

预期：ImportError（content_mind 没有 public_mind_context）。

- [ ] **Step 3: 实现**

`src/wechat_traffic_agent/content_mind.py`：

(a) 第 5 行 `from datetime import datetime, timezone` 改为 `from datetime import datetime, timedelta, timezone`；第 15 行 `from .position_ledger import PositionLedger, parse_iso_ts` 改为 `from .position_ledger import ABANDON_AFTER, PositionLedger, parse_iso_ts`；import 区补 `import logging`（按字母序放 `import json` 之前），并在 import 块之后加：

```python
logger = logging.getLogger(__name__)
```

(b) 模块级新增（放在 `_age_days` 之后、`class ContentMind` 之前；函数体从 square_observation.py:62-93 原样搬移，仅改名）：

```python
def _bounded_text(row: Mapping[str, Any], fields: tuple[str, ...], *, limit: int = 300) -> dict[str, Any]:
    # Cap free-text position fields; structured fields (ids, topics, ages) stay intact.
    return {**row, **{field: row[field][:limit] for field in fields if isinstance(row.get(field), str)}}


def public_mind_context(mind: Any) -> dict[str, Any]:
    """Project the agent's mind state for expression prompts (Square and X); degrade to {} on any failure."""
    if mind is None:
        return {}
    try:
        projection = mind.public_projection()
    except Exception as exc:
        logger.warning("mind projection unavailable: %r", exc)
        return {}
    beliefs = [{"proposition": row.get("proposition"), "stance": row.get("stance"),
                "confidence": row.get("confidence")}
               for row in projection.get("beliefs", ())[:8] if row.get("proposition")]
    terrain = [{"name": row.get("name"), "decayed_salience": row.get("decayed_salience"),
                "preference": row.get("preference")}
               for row in projection.get("interests", ())[:5]
               if row.get("name") and row.get("decayed_salience") is not None]
    interests = [str(row.get("name")) for row in projection.get("interests", ())[:8] if row.get("name")]
    questions = [str(row.get("question"))[:300]
                 for row in projection.get("open_inquiries", ())[:5] if row.get("question")]
    positions = projection.get("positions") or {}
    positions_open = [_bounded_text(row, ("proposition", "falsifier"))
                      for row in (positions.get("open") or ())[:6]
                      if row.get("age_days") is None or timedelta(days=row["age_days"]) <= ABANDON_AFTER]
    resolutions = [_bounded_text(row, ("proposition", "resolution"))
                   for row in (positions.get("recent_resolutions") or ())[:4]]
    return {"beliefs": beliefs, "interests": interests, "open_questions": questions,
            "interest_terrain": terrain, "positions_open": positions_open,
            "recent_resolutions": resolutions}
```

`src/wechat_traffic_agent/square_observation.py`：

(c) 删除 `_bounded_text`（:62-64）与 `_mind_context`（:66-93）两个函数。
(d) 第 13 行 `from .position_ledger import ABANDON_AFTER` 删除（已无使用方）；第 11 行附近加 `from .content_mind import public_mind_context`。
(e) `prepare_observation` 内 `mind_context = _mind_context(mind)`（:209）改为 `mind_context = public_mind_context(mind)`。

- [ ] **Step 4: 跑测试确认全绿**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_square_observation.py tests/test_content_mind.py tests/test_owner_decision_positions.py -v
```

预期：全部 PASS（含新测试），无新增失败。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/content_mind.py src/wechat_traffic_agent/square_observation.py tests/test_square_observation.py && git commit -m "$(cat <<'EOF'
refactor: 提升 public_mind_context 共享投影，Square 委托调用

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.5: Square→X 表达门放行 + LLM 合同超长热修（Task 0 结论落地）

**Files:**
- Modify: `src/wechat_traffic_agent/orchestration/social.py:276-397`（3 个守卫，不重排缩进）
- Modify: `src/wechat_traffic_agent/orchestration/content.py:896`（replay 集 +1）
- Modify: `src/wechat_traffic_agent/platforms/x/x_social.py`（门 :838-842、learning 门 :845-848、`_cycle_context` :795-816）
- Modify: `src/wechat_traffic_agent/content_goal_contracts.py`（EvidenceSelection clamp）
- Modify: `src/wechat_traffic_agent/memory_service.py:73-79`（Prefix clamp + prompt 预算）
- Modify: `src/wechat_traffic_agent/evidence_choice_service.py:97-134`（prompt 预算 + recoverable +string_too_long）
- Modify: `src/wechat_traffic_agent/learning_research.py`（why_interested clamp + prompt 预算）
- Test: `tests/test_x_social_square_gate.py`（新）、`tests/test_content_contracts_clamp.py`（新）

**背景（来自 Task 0 结论，必读）**：当前 Square 发布形态是 owner 周期（`v2-owner-*` 目录，只有 result.json，status `COGNITION_ONLY→SQUARE_EXPRESSION_READY`，draft 在 SquareDraftStore）。四处拦截点：social.py:290 早退、content.py:896 replay 集、x_social.py:838-842 门（status + worth_saying）、x_social.py:846 learning 门（服务器 `require_learning_discovery: true` 恒拦）。以下是唯一注入面分析后确定的修复，全部改动不得触碰 website 周期既有行为。

#### Part A：Square→X 放行

- [ ] **A1. social.py 三个守卫**（BINANCE_SQUARE 周期跳过 website 中段、直达 x_loop 钩子）：

```python
# :276 起，BINANCE_SQUARE 分支：把 `return` 改为记录标志（receipt 记录逻辑不变）
        cycle = str(continuous.get("cycle") or "")
        square_surface = continuous.get("publication_surface") == "BINANCE_SQUARE"
        if square_surface:
            # …原 receipt 记录代码原样保留…
            # （原 :290 的 `return` 删除）
# :295 网站中段守卫：
        if site_publisher is not None and not square_surface:
# :351 social_expression_drafts 守卫：
        if social_loop is not None and not direct_x and not square_surface:
# :365 publisher 循环守卫：
            if publisher is None or direct_x or square_surface:
                continue
```

- [ ] **A2. content.py:896** replay 集加 SQUARE_EXPRESSION_READY：

```python
            if str(prior.get("status") or "").upper() in {"PUBLISHED", "X_EXPRESSION_READY", "SQUARE_EXPRESSION_READY"}:
```

- [ ] **A3. x_social.py 门 + learning 豁免**（`after_content_cycle` :838-848）：

```python
        square_ready = result_status == "SQUARE_EXPRESSION_READY"
        if (
            (result_status != "PUBLISHED" and not square_ready and not x_only_admitted and not owner_directed_x)
            or (not cognition.get("worth_saying") and not square_ready)
        ):
            return {"status": "NO_EXPRESSION", "cycle": cycle}
        if self.history.has_event(cycle=cycle, event_type="EXPRESSION"):
            return {"status": "ALREADY_EXPRESSED", "cycle": cycle}
        from wechat_traffic_agent.learning_expression_quality import learning_required_for_sharing
        if not square_ready and not context.get("learning") and learning_required_for_sharing(self.state_root):
            return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle,
                    "reason": "NO_VERIFIED_LEARNING_RECORD"}
```

理由：SQUARE_EXPRESSION_READY 本身就是 owner 编辑评审（result.rationale 为完整审稿意见）对 worth_saying 与质量的裁决，Square 编辑门是该内容的在效质量闸；learning-first 策略继续约束 website/X 原创周期不变。

- [ ] **A4. x_social.py `_cycle_context` 装载 Square 草稿**（artifact 是 X_EXPRESSION_DRAFT prompt 的素材来源，owner 周期原本为空）：

```python
        draft = _read_json(Path(str(result.get("draft_path")))) if result.get("draft_path") else {}
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        publication = result.get("publication") if isinstance(result.get("publication"), dict) else {}
        square_draft: dict[str, Any] = {}
        if result.get("publication_surface") == "BINANCE_SQUARE" and result.get("draft_id"):
            try:
                from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
                doc = SquareDraftStore(self.state_root).get(str(result["draft_id"]))
                if isinstance(doc, dict) and doc.get("body"):
                    artifact = {"title": str(doc.get("thesis") or ""), "body": str(doc["body"])}
                    square_draft = {
                        "draft_id": str(result["draft_id"]),
                        "draft_status": str(doc.get("status") or ""),
                        "symbols": list(doc.get("symbols") or []),
                        "url": doc.get("url"), "post_id": doc.get("post_id"),
                    }
            except Exception:  # noqa: BLE001  草稿缺失不拦表达（红线：容错）
                square_draft = {}
        # 返回 dict 追加一项： "square_draft": square_draft,
```

- [ ] **A5. 核验 `_publish_prediction_outcome` 与 `_l1_x_strategy` 对空 prediction/worldview/cognition 的容忍**：读这两个函数，确认缺失文件路径走 `_read_json`→`{}` 后无 KeyError/AttributeError；若会炸，加与既有风格一致的 `.get(...)` 守卫并在 PR 说明。不改其他行为。

- [ ] **A6. 测试（tests/test_x_social_square_gate.py，tmp_path 造状态树 + stub model_runtime）**：

```python
# 1) owner Square 周期过门：result={"status":"SQUARE_EXPRESSION_READY","cycle":c,
#    "draft_id":<预先用 SquareDraftStore.create 存的草稿>,"publication_surface":"BINANCE_SQUARE"}
#    周期目录无 cognition.json；stub 模型返回合法短帖 JSON →
#    断言返回 status=="PUBLICATION_UNVERIFIED" 或草稿持久化成功（非 NO_EXPRESSION/HELD/SKIPPED），
#    且 x_expression_context_projection payload 含 artifact.body 与 square_draft.draft_id。
# 2) COGNITION_ONLY 拒绝：status 非 SQUARE_EXPRESSION_READY/PUBLISHED → NO_EXPRESSION。
# 3) learning 豁免：square_ready 且 sharing_policy=require_learning_discovery:true 且无
#    learning_result.json → 不返回 HELD_BY_LEARNING_QUALITY。
# 4) social.py 守卫：BINANCE_SQUARE 周期 + x_loop stub → x_loop.after_content_cycle 被调用一次；
#    social_expression_loop/x_browser_publisher stub 若被调用即 raise（断言未触发）。
# 5) website 周期不回归：status=PUBLISHED + publication.json 完备 + publication_event_id →
#    行为与改前一致（x_syndication QUEUED + 早退保留）。
# 6) content.py replay：prior status=SQUARE_EXPRESSION_READY → _run_social_stage 被调（stub）。
```

#### Part B：LLM 合同超长热修（既有弱点，一期放大；根因②）

- [ ] **B1. clamp 校验器（确定性，防周期死亡）**——超长的是生成散文非事实引文，schema 上限已两次呈现给模型（prompt 内嵌 model_json_schema + 修复重试），clamp 语义安全：

```python
# content_goal_contracts.py EvidenceSelection（顶部补 import：from pydantic import field_validator）：
    @field_validator("gap", mode="before")
    @classmethod
    def _clamp_gap(cls, v): return v[:500] if isinstance(v, str) else v
    @field_validator("reader_question", mode="before")
    @classmethod
    def _clamp_reader_question(cls, v): return v[:400] if isinstance(v, str) else v
# memory_service.py Prefix：
    @field_validator("prefix", mode="before")
    @classmethod
    def _clamp_prefix(cls, v): return v[:240] if isinstance(v, str) else v
# learning_research.py：TaskResearchFocus 由 create_model 生成（:152）——在基模型（定义 why_interested 的那个）
# 上加同款 field_validator（截断 600）；若基模型不便加，则 create_model 后重新绑定加过 validator 的模型。
# 实现者读文件后选侵入最小的一种。
```

- [ ] **B2. prompt 数字预算**（降低 clamp 触发频率）：三处 prompt 各加一句预算——evidence_choice_service.py SELECT_EVIDENCE（:97-114 prompt 文本末尾）加 `gap 不超过500字、reader_question 不超过400字，写短写具体。`；memory_service.py contextualize prompt（:88 附近）加 `prefix 必须不超过240字。`；learning_research.py 含 why_interested 的 prompt（grep 定位）加 `why_interested 不超过600字。`

- [ ] **B3. evidence_choice_service.py `recoverable_selection_errors`（:118-125）追加 `"string_too_long"`**——clamp 后不应再触发，纯纵深防御，让其余合同的超长也走确定性兜底而非炸周期。

- [ ] **B7. 测试（tests/test_content_contracts_clamp.py）**：

```python
# EvidenceSelection(gap="x"*600, 其余合法字段) 构造成功且 len(gap)==500；
# reader_question 同理 400；Prefix(prefix="y"*300) → 240；
# TaskResearchFocus(why_interested="z"*700) → 600；
# validate_selection 收到仅含 string_too_long 的 ValidationError → 不 raise（走兜底路径标记）。
```

- [ ] **Step: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/orchestration/social.py src/wechat_traffic_agent/orchestration/content.py src/wechat_traffic_agent/platforms/x/x_social.py src/wechat_traffic_agent/content_goal_contracts.py src/wechat_traffic_agent/memory_service.py src/wechat_traffic_agent/evidence_choice_service.py src/wechat_traffic_agent/learning_research.py tests/test_x_social_square_gate.py tests/test_content_contracts_clamp.py && git commit -m "$(cat <<'EOF'
fix: 放行 Square→X 表达门 + LLM 合同超长热修（Task 0 根因①②）

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: X 表达注入 live mind（唯一注入面 = 主进程 after_content_cycle/_owner_longform）

**Files:**
- Modify: `src/wechat_traffic_agent/platforms/x/x_social.py`
- Modify: `scripts/run_content_agent.py:567-573`
- Create: `tests/test_x_social_mind.py`

**背景：** X 表达只发生在主进程（orchestration/social.py:398-411 → after_content_cycle）。两个 prompt（短帖 x_social.py:909-913、长文 :1157）都 spread `_expression_model_context(context)`，而该函数第一步就是 `projected = dict(context)`（:129）——**只要 context 带 "mind" 键，两个 prompt 自动带上，函数本身零改动**。worker 进程不构造 ContentMind（避免 checkpoint 双写）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_x_social_mind.py`：

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import wechat_traffic_agent.platforms.x.x_social as xs
from wechat_traffic_agent.platforms.x.x_social import XAutonomyLoop, _expression_model_context

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


class _FakeMind:
    def __init__(self, projection=None, fail=False):
        self._projection = projection if projection is not None else {}
        self._fail = fail

    def public_projection(self):
        if self._fail:
            raise RuntimeError("projection exploded")
        return self._projection


def _loop(tmp_path: Path, mind=None) -> XAutonomyLoop:
    return XAutonomyLoop(runtime=None, model_runtime=None, state_root=tmp_path,
                         base_url="https://example.com", mind=mind)


_PROJECTION = {"beliefs": [], "interests": [], "open_inquiries": [],
               "positions": {"open": [], "recent_resolutions": []}}


def test_mind_context_returns_projection_empty_or_degrades(tmp_path: Path):
    assert _loop(tmp_path, mind=_FakeMind(_PROJECTION))._mind_context()["positions_open"] == []
    assert _loop(tmp_path, mind=None)._mind_context() == {}
    assert _loop(tmp_path, mind=_FakeMind(fail=True))._mind_context() == {}


def test_expression_context_carries_mind_through_unified_projector():
    payload = _expression_model_context({"cycle": "c1", "mind": {"beliefs": [{"proposition": "p"}]}})
    assert payload["mind"]["beliefs"] == [{"proposition": "p"}]
    assert "mind" not in _expression_model_context({"cycle": "c1"})


def test_after_content_cycle_attaches_mind_context(tmp_path: Path, monkeypatch):
    loop = _loop(tmp_path, mind=_FakeMind(_PROJECTION))

    async def fake_publish(folder, context, now):
        return {}

    monkeypatch.setattr(loop, "_publish_prediction_outcome", fake_publish)
    monkeypatch.setattr(
        loop, "_cycle_context",
        lambda result: (tmp_path / "cycle-c1", {"cycle": "c1"}))
    outcome = asyncio.run(loop.after_content_cycle(result={"status": "PUBLISHED"}, now=NOW))
    assert outcome["status"] == "NO_EXPRESSION"
    assert "mind" in loop._last_expression_context
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_x_social_mind.py -v
```

预期：TypeError（unexpected keyword `mind`）/ AttributeError。

- [ ] **Step 3: 实现**

`src/wechat_traffic_agent/platforms/x/x_social.py`：

(a) import 区（:11 `import re` 之前）加 `import logging`；模块 import 块之后（`_expression_model_context` 定义之前）加：

```python
logger = logging.getLogger(__name__)
```

(b) `XAutonomyLoop.__init__`（:364）签名加参数、存属性、自建账本（账本属性本任务一并引入，Task 4/5 使用；路径镜像 ContentMind.root，content_mind.py:57）：

```python
    def __init__(self, *, runtime: Any, model_runtime: Any, state_root: Path, base_url: str,
                 browser_mentions_observer: Any | None = None,
                 browser_publish_transport: Any | None = None,
                 growth_mission: Any | None = None,
                 social_observer: Any | None = None,
                 social_context_provider: Any | None = None,
                 l1_decisions: Any | None = None,
                 mind: Any | None = None) -> None:
        self.runtime = runtime
        self.model_runtime = model_runtime
        self.browser_mentions_observer = browser_mentions_observer
        self.browser_publish_transport = browser_publish_transport
        self.growth_mission = growth_mission
        self.social_observer = social_observer
        self.social_context_provider = social_context_provider
        self.l1_decisions = (
            l1_decisions
            if l1_decisions is not None
            else getattr(model_runtime, "l1_decisions", None)
        )
        self.mind = mind
        self.state_root = Path(state_root)
        self.base_url = base_url.rstrip("/")
        self.root = self.state_root / "content" / "x"
        self.history = XHistoryLedger(self.state_root)
        from wechat_traffic_agent.position_ledger import PositionLedger
        self.positions = PositionLedger(self.state_root / "application" / "content_mind")
        self._last_expression_context: dict[str, Any] = {}
```

(c) 类内新增方法（`_l1` 之后）：

```python
    def _mind_context(self) -> dict[str, Any]:
        if self.mind is None:
            return {}
        try:
            from wechat_traffic_agent.content_mind import public_mind_context
            return public_mind_context(self.mind)
        except Exception as exc:
            logger.warning("x expression mind context unavailable: %r", exc)
            return {}
```

(d) `after_content_cycle`（:820-822）在 `_cycle_context` 之后注入并留测试观察点：

```python
        folder, context = self._cycle_context(result)
        if folder is None:
            return {"status": "NO_CYCLE"}
        context["mind"] = self._mind_context()
        self._last_expression_context = context
```

(e) `_owner_longform`（:1116 `cycle=folder.name` 之后）同样注入：

```python
        context = {**context, "mind": self._mind_context()}
```

`scripts/run_content_agent.py`（:567-573）XAutonomyLoop 构造加 `mind=content_mind`：

```python
        x_loop = XAutonomyLoop(
            runtime=app.runtime, model_runtime=app.domain_runtime, state_root=state_root,
            base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
            growth_mission=x_growth_mission,
            social_observer=wakeful_runtime.observe_social_interaction,
            social_context_provider=wakeful_runtime.neural_social.relationship,
            mind=content_mind,
        )
```

**不改** `_expression_model_context`：`dict(context)` 已透传 mind 键（键名 "mind" 不与其改写的 mind_plan/source_evidence/learning 冲突）。

- [ ] **Step 4: 跑测试确认全绿**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_x_social_mind.py tests/test_x_social.py -v
```

预期：新文件 3 PASS；test_x_social.py 与基线一致（XAutonomyLoop 新参数有默认值，旧构造不受影响）。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/platforms/x/x_social.py scripts/run_content_agent.py tests/test_x_social_mind.py && git commit -m "$(cat <<'EOF'
feat: X 表达注入 live mind 投影（主进程唯一注入面，worker 不建 mind）

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: X 发布核验后跨面挂接 attach_post

**Files:**
- Modify: `src/wechat_traffic_agent/platforms/x/x_social.py`（两个发布成功路径）

**背景：** 短帖成功路径在 :1034-1046（拿到 post_id 后 :1064 `_clear_publication_blocker` 附近），长文成功路径在 `_owner_longform` :1203-1208（`result.status in {"POSTED","ALREADY_POSTED"}`）。挂接用 Task 1 的 `topic_matches` 做零 LLM 匹配，只挂 OPEN 立场；失败只告警不炸发布路径。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_x_social_mind.py`：

```python
def test_attach_verified_post_matches_topic_and_is_benign(tmp_path: Path):
    loop = _loop(tmp_path)
    row = loop.positions.register(topic="sol-staking", proposition="流入持续",
                                  falsifier="流入回落", evidence_refs=(),
                                  draft_id="d1", now=NOW)
    loop._attach_verified_post(text="SOL staking inflows remain strong", post_id="123", now=NOW)
    loop._attach_verified_post(text="eth gas fees dropped hard", post_id="456", now=NOW)
    posts = loop.positions.get(row["position_id"])["post_ids"]
    assert {"id": "123", "surface": "x"} in posts
    assert {"id": "456", "surface": "x"} not in posts
    # 空输入直接返回；账本异常由调用方的 try/except 兜住（见实现）
    loop._attach_verified_post(text="", post_id="789", now=NOW)
    assert loop.positions.get(row["position_id"])["post_ids"] == posts
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_x_social_mind.py -v
```

预期：AttributeError（无 `_attach_verified_post`）。

- [ ] **Step 3: 实现**

`src/wechat_traffic_agent/platforms/x/x_social.py`：

(a) 文件顶部 import（:18 附近）加：

```python
from wechat_traffic_agent.position_ledger import topic_matches
```

（`PositionLedger` 已在 Task 3 于 `__init__` 内局部 import，保持不动。）

(b) 类内新增方法（`_mind_context` 之后）：

```python
    def _attach_verified_post(self, *, text: str, post_id: str, now: datetime) -> None:
        """跨面足迹：X 帖核验通过后回记到 topic 命中的 OPEN 立场（零 LLM）。"""
        if not text or not post_id:
            return
        for row in self.positions.open_positions(now=now):
            if topic_matches(str(row.get("topic") or ""), text):
                self.positions.attach_post(str(row["position_id"]), post_id, surface="x")
```

(c) 短帖成功路径：`_clear_publication_blocker`（:1064）之后插入：

```python
        try:
            self._attach_verified_post(text=decision.post_text, post_id=post_id, now=now)
        except Exception as exc:
            logger.warning("x cross-surface position attach failed: %r", exc)
```

(d) 长文成功路径 `_owner_longform`：`self._clear_publication_blocker(now=now,receipt_id=receipt_id)`（:1205）之后、return 之前插入：

```python
            try:
                saved = _read_json(folder / "social_expression_drafts.json")
                longform_text = str(((saved.get("drafts") or {}).get("x") or {}).get("text") or "")
                self._attach_verified_post(text=longform_text, post_id=str(publication["post_id"]), now=now)
            except Exception as exc:
                logger.warning("x cross-surface position attach failed: %r", exc)
```

- [ ] **Step 4: 跑测试确认全绿**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_x_social_mind.py tests/test_x_social.py tests/test_x_post_recovery.py -v
```

预期：全绿（test_x_post_recovery 覆盖短帖成功/失败路径，验证钩子不破坏既有流程；与基线一致）。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/platforms/x/x_social.py tests/test_x_social_mind.py && git commit -m "$(cat <<'EOF'
feat: X 发布核验后按 topic 挂接立场账本（跨面足迹 surface=x）

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: x-observe 证据回流 + X_POSITION_SCAN 原生立场登记

**Files:**
- Modify: `src/wechat_traffic_agent/platforms/x/x_social.py`
- Modify: `tests/test_x_social_mind.py`

**背景：** observe 周期（:507-619）拿到 fresh mentions 后：① 对每个 OPEN position 做零 LLM topic 匹配，命中经 `append_evidence` 回流（FIFO 上限 10，永不触发转移）；② 至多一次 `X_POSITION_SCAN` 结构化调用，产出可选的原生立场并以 `register(source="x")` 登记。全部包裹在 try/except，观察周期永不因此失败。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_x_social_mind.py`：

```python
def test_position_evidence_backflow_matches_and_caps(tmp_path: Path):
    loop = _loop(tmp_path)
    row = loop.positions.register(topic="sol-staking", proposition="流入持续",
                                  falsifier="流入回落", evidence_refs=(),
                                  draft_id="d1", now=NOW)
    mentions = [
        {"evidence_id": "x-post:a1", "summary": "SOL staking inflows at record high"},
        {"evidence_id": "x-post:b2", "summary": "unrelated eth chatter"},
    ]
    loop._backfill_position_evidence(mentions=mentions, now=NOW)
    refs = loop.positions.get(row["position_id"])["evidence_refs"]
    dicts = [r for r in refs if isinstance(r, dict)]
    assert len(dicts) == 1
    assert dicts[0]["source"] == "x_observe"
    assert dicts[0]["ref"] == "x-post:a1"
    assert len(dicts[0]["note"]) <= 120
    assert loop.positions.get(row["position_id"])["status"] == "OPEN"  # 证据不触发转移
    loop._backfill_position_evidence(mentions=[], now=NOW)  # 空观察不炸


def test_register_scanned_position_registers_with_source_x_or_skips(tmp_path: Path):
    from wechat_traffic_agent.platforms.x.x_social import XNativePosition, XPositionScan
    loop = _loop(tmp_path)
    assert loop._register_scanned_position(None, cycle="c1", now=NOW) is None
    assert loop._register_scanned_position(XPositionScan(), cycle="c1", now=NOW) is None
    row = loop._register_scanned_position(
        XPositionScan(position=XNativePosition(topic="strk", proposition="费率将转正",
                                               falsifier="费率持续为负")), cycle="c1", now=NOW)
    assert row["source"] == "x"
    assert row["status"] == "OPEN"


def test_position_scan_models_are_conservative_by_default():
    from wechat_traffic_agent.platforms.x.x_social import XPositionScan
    assert XPositionScan.model_validate({}).position is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_x_social_mind.py -v
```

预期：ImportError（XPositionScan）/ AttributeError。

- [ ] **Step 3: 实现**

`src/wechat_traffic_agent/platforms/x/x_social.py`：

(a) pydantic 模型区（`XExpressionDecision` :112 附近）新增：

```python
class XNativePosition(BaseModel):
    topic: str = Field(min_length=1)
    proposition: str = Field(min_length=1)
    falsifier: str = Field(min_length=1)


class XPositionScan(BaseModel):
    position: XNativePosition | None = None
```

(b) 类内新增方法（`_attach_verified_post` 之后）：

```python
    def _backfill_position_evidence(self, *, mentions: list[dict[str, Any]], now: datetime) -> None:
        """零 LLM：观察命中的 OPEN 立场追加 x_observe 证据；永不触发状态转移。"""
        for row in self.positions.open_positions(now=now):
            topic = str(row.get("topic") or "")
            if not topic:
                continue
            for mention in mentions:
                text = str(mention.get("summary") or "")
                post_id = str(mention.get("evidence_id") or "").removeprefix("x-post:")
                if not text or not post_id or not topic_matches(topic, text):
                    continue
                self.positions.append_evidence(str(row["position_id"]), {
                    "source": "x_observe", "ref": f"x-post:{post_id}",
                    "at": now.isoformat(), "note": text[:120],
                })

    async def _scan_native_position(self, *, mentions: list[dict[str, Any]],
                                    cycle: str, now: datetime) -> XPositionScan:
        runtime = ReceiptRuntime(self.model_runtime,
                                 self.root / "social-model-calls" / cycle / "position-scan")
        prompt = (
            "X_POSITION_SCAN: Review these newly observed public X posts. "
            "If — and only if — they jointly support a NEW falsifiable position worth committing "
            "(a concrete claim with a measurable falsification line, not duplicating any open "
            "position below), return its topic, proposition and falsifier; otherwise return "
            '{"position": null}. Be conservative: at most one position, only when the evidence '
            "is specific and current. Return JSON only.\n"
            + json.dumps({
                "posts": [{"summary": str(m.get("summary") or "")[:300]} for m in mentions],
                "open_positions": [
                    {"topic": r.get("topic"), "proposition": str(r.get("proposition"))[:200]}
                    for r in self.positions.open_positions(now=now)
                ],
            }, ensure_ascii=False)
        )
        return await _structured(runtime, prompt, XPositionScan, max_tokens=800)

    def _register_scanned_position(self, scan: XPositionScan | None, *, cycle: str,
                                   now: datetime) -> dict[str, Any] | None:
        native = getattr(scan, "position", None)
        if native is None:
            return None
        return self.positions.register(topic=native.topic, proposition=native.proposition,
                                       falsifier=native.falsifier, evidence_refs=(),
                                       draft_id=cycle, now=now, source="x")
```

(c) `observe()` 的 mention 循环之后、`_write_json(self.seen_path, ...)`（:599）之前插入：

```python
        if fresh:
            try:
                self._backfill_position_evidence(mentions=fresh, now=now)
            except Exception as exc:
                logger.warning("x position evidence backflow failed: %r", exc)
            try:
                scan = await self._scan_native_position(mentions=fresh[:10], cycle=cycle, now=now)
            except Exception as exc:
                logger.warning("x native position scan failed: %r", exc)
                scan = None
            try:
                self._register_scanned_position(scan, cycle=cycle, now=now)
            except Exception as exc:
                logger.warning("x native position registration failed: %r", exc)
```

- [ ] **Step 4: 跑测试确认全绿**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/test_x_social_mind.py tests/test_x_social.py -v
```

预期：全绿。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/platforms/x/x_social.py tests/test_x_social_mind.py && git commit -m "$(cat <<'EOF'
feat: x-observe 证据回流账本 + X_POSITION_SCAN 原生立场登记（source=x）

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 全量回归 + 服务器脏文件对账 + 叠加部署 + 部署后验证

**Files:** 无新改动（部署门禁与验证）；对账发现的合并进 x_social.py 等目标文件。

- [ ] **Step 1: 全量回归并按"最深错误帧"分类**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src;D:/ferro/virtualbrain/src" .venv/Scripts/python.exe -m pytest tests/ -x -q --tb=line > /tmp/phase2_full.txt 2>&1; tail -3 /tmp/phase2_full.txt
```

判定法（一期验证过）：`pytest --lf --tb=line` 重跑失败项，grep 最深错误帧；只有 traceback 穿过本期 5 个改动文件且断言涉及 position/attach/evidence/mind 的才算新回归。既有基线 ~360（hnswlib 182、WinError 1314 166、wakeful 漂移 44、agent_network 24 等，见记忆 ferro-local-test-env）。

- [ ] **Step 2: 部署门禁**

- Task 0 结论为"X 线可承载发帖" → 继续。
- Task 0 结论为"登录态等超范围问题且用户同意先部署" → 继续（打通改动在 X 静默时无害休眠）。
- 两者皆否 → 停，向用户升级。

- [ ] **Step 3: 服务器脏文件对账（关键，x_social.py 在服务器脏文件清单里）**

```bash
ssh ubuntu@43.133.181.149 'cd content-agent/current && git status --porcelain'
```

对 7 个目标文件（`src/wechat_traffic_agent/position_ledger.py`、`content_mind.py`、`square_observation.py`、`platforms/x/x_social.py`、`orchestration/social.py`、`orchestration/content.py`、`scripts/run_content_agent.py`）逐一：

```bash
ssh ubuntu@43.133.181.149 'cd content-agent/current && git diff -- <file> | head -100'
```

若服务器脏版本含本地没有的线上改动：先在本地把该改动合入对应文件（跑测试），再部署合并后的版本。**零重叠确认后才进入 Step 4。**

- [ ] **Step 4: 叠加部署（LF blob + md5 双侧核对）**

本机逐文件导出 LF blob 并算 md5：

```bash
cd D:/ferro/wechat-traffic-agent && mkdir -p /tmp/phase2-deploy/src/wechat_traffic_agent/platforms/x /tmp/phase2-deploy/scripts
for f in src/wechat_traffic_agent/position_ledger.py src/wechat_traffic_agent/content_mind.py src/wechat_traffic_agent/square_observation.py src/wechat_traffic_agent/platforms/x/x_social.py src/wechat_traffic_agent/orchestration/social.py src/wechat_traffic_agent/orchestration/content.py scripts/run_content_agent.py; do git -c core.autocrlf=false show "main:$f" > "/tmp/phase2-deploy/$f"; done
cd /tmp/phase2-deploy && find . -type f -exec md5sum {} \;
```

scp 到服务器解到 `current`（保持相对路径），逐文件 `md5sum` 核对一致：

```bash
scp -r /tmp/phase2-deploy/src /tmp/phase2-deploy/scripts ubuntu@43.133.181.149:content-agent/current/
```

- [ ] **Step 5: 服务器测试 + 重启**

```bash
ssh ubuntu@43.133.181.149 'cd content-agent/current && PYTHONPATH=current/src content-agent-venv-current/bin/python -m pytest tests/test_position_ledger.py tests/test_square_observation.py tests/test_x_social.py tests/test_x_social_square_gate.py tests/test_content_contracts_clamp.py -q 2>&1 | tail -5'
```

通过后重启并验证：

```bash
ssh ubuntu@43.133.181.149 'sudo -n systemctl restart content-agent.service && sleep 20 && systemctl is-active content-agent.service && sudo -n journalctl -u content-agent.service --since "-2 min" --no-pager | grep -icE "traceback|importerror" || true'
```

预期：active；导入错误 0。

- [ ] **Step 6: 部署后验证**

- Square 线不回退：下一内容周期 llm_audit/requests.jsonl 仍出现 positions_open；25+ 帖节奏保持。
- X 线：CHECK_X tick 后 `content/x/social-model-calls/<cycle>/position-scan/` 出现 receipt（fresh mentions 非空时）；观察周期 status 不因账本故障变 DEGRADED。
- 账本：`application/content_mind/positions.json` 新条目带 `source` 字段；X 帖核验后 OPEN 立场 post_ids 出现 `surface:"x"` 条目；`positions.lock` 文件出现。
- 若 X 仍静默（Task 0 遗留）：记录为已知状态，不回滚（注入是休眠的）。
- 更新记忆：ferro-personality-phase1-deployed 补二期部署记录；观察红线（X 日帖、cross-surface position 数、source="x" 周登记数）。

---

## Task 0 结论（2026-10-11，已本地逐项复核）

**判定：(b) 存在代码 bug，X 线当前无法发帖，修完才可承载。** 不是登录态/浏览器问题（观察路径健康、env 三开关全 ON、publication_blocker 已清空）。

### 根因①（主）：Square 早退挡断 X 表达链

- 2026-10-08 22:30 起 X 无新帖。commit `3bcf322`（10-09 23:44 部署）在 `orchestration/social.py:276-290` 加了 `publication_surface == "BINANCE_SQUARE"` 早退 `return`，位于 `x_loop.after_content_cycle` 钩子（:398-411）之前——Square 周期永远到不了 X 表达。
- **当前 Square 发布形态已迁移为 owner 周期**：服务器实测（10-11 12:16-12:50 持续活动），`content/continuous/cycles/v2-owner-*/result.json` 是唯一活跃形态，字段仅 `{cycle, draft_id, draft_status, publication_confirmed, publication_surface, rationale, status}`，status 流转 `COGNITION_ONLY → SQUARE_EXPRESSION_READY`，目录内**只有 result.json**（无 cognition.json / learning_result.json / mind_plan.json）。调用链：`content.py:410` → `_run_social_stage`（social.py:267）→ 早退。
- 拦截点共四处（修复须全覆盖，见 Task 2.5）：
  1. social.py:290 早退（未到 x_loop）；
  2. content.py:896 replay 集 `{PUBLISHED, X_EXPRESSION_READY}` 不含 SQUARE_EXPRESSION_READY（中断周期无法恢复重放）；
  3. x_social.py:838-842 门 `result_status != "PUBLISHED"` 不认 SQUARE_EXPRESSION_READY，且 `cognition.get("worth_saying")` 对 owner 周期恒空（无 cognition.json）；
  4. x_social.py:846 learning 门：服务器 `sharing_policy.json` `require_learning_discovery: true` → `learning_required_for_sharing()` 恒 True，owner 周期无 learning_result.json → 恒 HELD_BY_LEARNING_QUALITY。
- 旁证：旧 continuous 周期（v2-1789999200，10-08）status=PUBLISHED + cognition.worth_saying=True + mind_plan 存在——旧形态能过门，所以 3bcf322 之前 X 正常；形态迁移 + 早退共同造成静默。

### 根因②（次）：LLM 输出合同超长爆发（既有弱点被放大）

- error_diagnostic.json 按日直方图：Contexts.prefixes>240 ~55 次、EvidenceSelection.gap>500 ~60 次、TaskResearchFocus.why_interested>600 ~4 次，全部集中在 10-11 02:45 部署后；但同类错误 10-06/07 已存在（1-4 次/日）——是既有合同弱点，一期更丰富的 prompt 放大了它。10-10 低活动是资金闸（另一已修问题），非健康基线。
- 机制：`_structured`（content_loop.py:293-306）prompt 已内嵌完整 `model_json_schema()`（含 max_length）+ 一次修复重试；模型连续两次超长时 ValidationError 上抛杀死周期。EvidenceSelection 有确定性兜底（content_goal_loop.py:1135-1169）但 `recoverable_selection_errors`（evidence_choice_service.py:118-125）不含 string_too_long，走不到兜底。
- 处置：随 Task 2.5 一并热修（数字预算 + string_too_long 入可恢复集 + prose 字段 clamp 校验器）。

### 处置

- 新增 **Task 2.5：Square→X 表达门放行 + LLM 合同超长热修**（全文见上文插入节），执行顺序在 Task 2 之后、Task 3 之前。
- Task 6 部署文件清单追加：`orchestration/social.py`、`orchestration/content.py`（服务器脏文件对账范围同步扩大，`x_social.py` 已知为脏文件需先对账）。
- X 线可承载发帖 = Task 2.5 部署后实测首个 owner Square 周期产出 x_social_expression 非跳过记录。
