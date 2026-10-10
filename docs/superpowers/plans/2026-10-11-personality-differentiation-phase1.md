# Ferro 个性分化第一期 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地个性分化第一期：立场账本 + 自传体上下文 + salience 衰减视图 + 核验优先选择策略，让 Ferro 的兴趣/立场/连续感开始分化。

**Architecture:** 全部改动在 wechat-traffic-agent。新增 `position_ledger.py`（独立状态机模块）；`content_mind.py` 集成账本并在 `public_projection()` 输出衰减视图与 positions；`square_observation.py` 把 positions/兴趣地形注入生成 payload、登记/转移 position、加同话题连续防过拟合闸；`owner_agent_episode.py` 决策上下文接入 positions。VirtualBrain 核心零改动。

**Tech Stack:** Python 3.12, pydantic v2, pytest, stdlib (json/os.replace)。

**Spec:** `docs/superpowers/specs/2026-10-11-personality-differentiation-design.md`

**本地测试命令**（Windows Git Bash，全计划统一）：

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest <file> -v
```

**工作区警告：** 仓库存在大量未 commit 在途改动（见 spec 部署节）。每个任务只 `git add` 本任务明确列出的文件，绝不 `git add -A` / `git add .`。

---

### Task 1: PositionLedger 模块

**Files:**
- Create: `src/wechat_traffic_agent/position_ledger.py`
- Test: `tests/test_position_ledger.py`

- [ ] **Step 1: 写失败测试**

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wechat_traffic_agent.position_ledger import PositionLedger

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _ledger(tmp_path: Path) -> PositionLedger:
    return PositionLedger(tmp_path / "content_mind")


def test_register_creates_open_position(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="sol-staking", proposition="质押流入翻倍将持续",
                          falsifier="流入回落到升级前水平", evidence_refs=("web:s1",),
                          draft_id="d1", now=NOW)
    assert row["status"] == "OPEN"
    assert row["position_id"].startswith("pos-")
    assert row["post_ids"] == ["d1"]
    assert ledger.open_positions(now=NOW)[0]["position_id"] == row["position_id"]


def test_confirm_and_refute_transitions(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f",
                          evidence_refs=(), draft_id="d1", now=NOW)
    pid = row["position_id"]
    confirmed = ledger.transition(pid, verdict="CONFIRMED", resolution="结算数据符合",
                                  evidence_refs=("web:e2",), draft_id="d2", now=NOW)
    assert confirmed["status"] == "CONFIRMED"
    with pytest.raises(ValueError):
        ledger.transition(pid, verdict="REFUTED", resolution="再改", evidence_refs=(), draft_id="d3", now=NOW)
    row2 = ledger.register(topic="t2", proposition="p2", falsifier="f2",
                           evidence_refs=(), draft_id="d4", now=NOW)
    refuted = ledger.transition(row2["position_id"], verdict="REFUTED", resolution="错在忽略了 X",
                                evidence_refs=(), draft_id="d5", now=NOW)
    assert refuted["status"] == "REFUTED"
    assert refuted["resolution"] == "错在忽略了 X"
    with pytest.raises(ValueError):
        ledger.transition(row2["position_id"], verdict="CONFIRMED", resolution="",
                          evidence_refs=(), draft_id="d6", now=NOW)


def test_transition_requires_open_position_and_resolution_for_refuted(tmp_path: Path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.transition("pos-missing", verdict="CONFIRMED", resolution="x",
                          evidence_refs=(), draft_id="d", now=NOW)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    with pytest.raises(ValueError):
        ledger.transition(row["position_id"], verdict="REFUTED", resolution="  ",
                          evidence_refs=(), draft_id="d", now=NOW)


def test_open_positions_sweeps_expired_after_five_days(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    later = NOW + timedelta(days=6)
    open_rows = ledger.open_positions(now=later)
    assert open_rows == []
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "ABANDONED"
    assert "5 days" in stored["resolution"]
    # 已放弃的不能再转移
    with pytest.raises(ValueError):
        ledger.transition(stored["position_id"], verdict="CONFIRMED", resolution="x",
                          evidence_refs=(), draft_id="d", now=later)


def test_recent_resolutions_returns_terminal_within_window(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    ledger.transition(row["position_id"], verdict="REFUTED", resolution="wrong about x",
                      evidence_refs=(), draft_id="d", now=NOW)
    assert len(ledger.recent_resolutions(now=NOW)) == 1
    assert ledger.recent_resolutions(now=NOW + timedelta(days=8)) == []


def test_corrupt_and_malformed_entries_are_skipped(tmp_path: Path):
    root = tmp_path / "content_mind"
    root.mkdir(parents=True)
    (root / "positions.json").write_text(json.dumps({
        "positions": [
            {"position_id": "pos-broken"},                       # 缺字段
            "not-a-dict",                                        # 类型错
            {"position_id": "pos-ok", "topic": "t", "proposition": "p", "falsifier": "f",
             "status": "OPEN", "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "evidence_refs": [], "post_ids": [], "resolution": None},
        ]
    }), encoding="utf-8")
    ledger = PositionLedger(root)
    rows = ledger.open_positions(now=NOW)
    assert [r["position_id"] for r in rows] == ["pos-ok"]
    ledger.register(topic="t2", proposition="p2", falsifier="f2", evidence_refs=(),
                    draft_id="d", now=NOW)  # 原子写不因坏行失败
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_position_ledger.py -v
```

预期：FAIL / ERROR（`ModuleNotFoundError: position_ledger`）。

- [ ] **Step 3: 实现**

```python
"""Committed falsifiable positions: continuity and accountability for published claims."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

LEDGER_SCHEMA = 1
ABANDON_AFTER = timedelta(days=5)
RESOLUTION_WINDOW = timedelta(days=7)
_TERMINAL = {"CONFIRMED", "REFUTED", "ABANDONED"}


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class PositionLedger:
    """OPEN -> CONFIRMED | REFUTED | ABANDONED; terminal states are final."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "positions.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = row.get("positions") if isinstance(row, dict) else None
        if not isinstance(rows, list):
            return []
        clean = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("position_id"), str) or not isinstance(item.get("status"), str):
                continue
            if item["status"] not in {"OPEN"} | _TERMINAL:
                continue
            clean.append(item)
        return clean

    def _save(self, rows: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"schema": LEDGER_SCHEMA, "positions": rows},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _valid(self, row: Mapping[str, Any]) -> bool:
        for key in ("position_id", "topic", "proposition", "falsifier", "status"):
            if not str(row.get(key) or "").strip() and key != "falsifier":
                return False
        if row.get("status") == "OPEN" and not str(row.get("falsifier") or "").strip():
            return False
        return _parse_ts(row.get("created_at")) is not None

    def get(self, position_id: str) -> dict[str, Any] | None:
        for row in self._load():
            if row.get("position_id") == position_id:
                return row
        return None

    def register(self, *, topic: str, proposition: str, falsifier: str,
                 evidence_refs: tuple[str, ...] | list[str], draft_id: str,
                 now: datetime) -> dict[str, Any]:
        topic, proposition, falsifier = topic.strip(), proposition.strip(), falsifier.strip()
        if not topic or not proposition or not falsifier:
            raise ValueError("position requires topic, proposition and falsifier")
        rows = [row for row in self._load() if self._valid(row)]
        digest = re.sub(r"[^a-z0-9]", "", f"{topic}:{proposition}".lower())[:24]
        row = {
            "position_id": "pos-" + digest + "-" + now.strftime("%Y%m%d%H%M%S"),
            "topic": topic, "proposition": proposition, "falsifier": falsifier,
            "status": "OPEN", "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "evidence_refs": [str(ref) for ref in evidence_refs],
            "post_ids": [str(draft_id)], "resolution": None,
        }
        rows.append(row)
        self._save(rows)
        return dict(row)

    def transition(self, position_id: str, *, verdict: str, resolution: str,
                   evidence_refs: tuple[str, ...] | list[str], draft_id: str,
                   now: datetime) -> dict[str, Any]:
        if verdict not in {"CONFIRMED", "REFUTED", "ABANDONED"}:
            raise ValueError("verdict must be CONFIRMED, REFUTED or ABANDONED")
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
            row["evidence_refs"] = list(dict.fromkeys(
                [*row.get("evidence_refs", []), *(str(ref) for ref in evidence_refs)]))
            if str(draft_id):
                row["post_ids"] = list(dict.fromkeys([*row.get("post_ids", []), str(draft_id)]))
            self._save(rows)
            return dict(row)
        raise ValueError(f"position {position_id} not found")

    def _sweep(self, rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
        changed = False
        for row in rows:
            if row.get("status") != "OPEN":
                continue
            created = _parse_ts(row.get("created_at"))
            if created and now - created > ABANDON_AFTER:
                row["status"] = "ABANDONED"
                row["resolution"] = "no verification within 5 days"
                row["updated_at"] = now.isoformat()
                changed = True
        if changed:
            self._save(rows)
        return rows

    def open_positions(self, *, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
        rows = [row for row in self._sweep(self._load(), now) if row.get("status") == "OPEN"]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return [dict(row) for row in rows[:limit]]

    def recent_resolutions(self, *, now: datetime, limit: int = 5) -> list[dict[str, Any]]:
        rows = []
        for row in self._load():
            if row.get("status") not in {"CONFIRMED", "REFUTED"}:
                continue
            updated = _parse_ts(row.get("updated_at"))
            if updated and now - updated <= RESOLUTION_WINDOW:
                rows.append((str(row.get("updated_at")), dict(row)))
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in rows[:limit]]


import json  # noqa: E402  (kept at bottom: used only by load/save above)
```

注意：把 `import json` 移到文件顶部常规位置（`import re` 旁），上面底部 import 只是占位提醒——实现时放顶部。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_position_ledger.py -v
```

预期：6 passed。

- [ ] **Step 5: 提交（只加本任务文件）**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/position_ledger.py tests/test_position_ledger.py && git commit -m "feat: position ledger for committed falsifiable positions"
```

---

### Task 2: ContentMind 集成账本 + salience 衰减视图

**Files:**
- Modify: `src/wechat_traffic_agent/content_mind.py`（`__init__` 约 31-59 行、`public_projection` 约 1032-1143 行）
- Test: `tests/test_content_mind_projection.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.content_mind import ContentMind

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _mind(tmp_path: Path) -> ContentMind:
    return ContentMind(root=tmp_path / "state", subject_id="content-agent")


def test_projection_includes_positions_block(tmp_path: Path):
    mind = _mind(tmp_path)
    mind.positions.register(topic="sol-staking", proposition="p", falsifier="f",
                            evidence_refs=(), draft_id="d1", now=NOW)
    projection = mind.public_projection()
    assert projection["positions"]["open"][0]["topic"] == "sol-staking"
    assert projection["positions"]["recent_resolutions"] == []


def test_projection_rows_carry_decayed_salience(tmp_path: Path):
    mind = _mind(tmp_path)
    mind.observe_learning_interest(cycle="c1", topic="fresh",
                                   reward={"utility": 1.0, "kind": "research"},
                                   evidence_refs=("r",))
    projection = mind.public_projection(now=NOW)
    rows = {row["name"]: row for row in projection["interests"]}
    assert "decayed_salience" in rows["fresh"]
    assert 0.0 <= rows["fresh"]["decayed_salience"] <= rows["fresh"]["salience"]


def test_decay_math_half_life():
    # 纯函数直接测，不依赖 VirtualBrain interests history 的 observed_at
    from wechat_traffic_agent.content_mind import _decayed_salience
    fresh = NOW - timedelta(minutes=10)
    assert _decayed_salience(1.0, fresh.isoformat(), NOW) >= 0.99
    assert _decayed_salience(1.0, (NOW - timedelta(hours=48)).isoformat(), NOW) <= 0.51
    assert _decayed_salience(1.0, (NOW - timedelta(hours=96)).isoformat(), NOW) <= 0.26
    assert _decayed_salience(0.8, None, NOW) == 0.8          # 无历史不衰减
    assert _decayed_salience(0.8, "not-a-date", NOW) == 0.8  # 坏时间戳不衰减
    assert _decayed_salience(1.0, (NOW + timedelta(hours=5)).isoformat(), NOW) == 1.0  # 未来时间戳钳制


def test_missing_history_no_decay(tmp_path: Path):
    mind = _mind(tmp_path)
    mind.observe_learning_interest(cycle="c1", topic="t1",
                                   reward={"utility": 0.3, "kind": "research"},
                                   evidence_refs=("r",))
    projection = mind.public_projection(now=NOW)
    for row in projection["interests"]:
        if row.get("last_observed_at") is None:
            assert row["decayed_salience"] == row["salience"]
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_content_mind_projection.py -v
```

预期：FAIL（`public_projection() got an unexpected keyword argument 'now'` / 无 `positions` key）。

- [ ] **Step 3: 实现**

3a. 顶部 import 区加入（与现有 import 风格一致）：

```python
from .position_ledger import PositionLedger
```

3b. `__init__` 中 `self._inquiry_topics: dict[str, str] = {}`（约 52 行）之后加：

```python
        self.positions = PositionLedger(self.root)
```

3c. `public_projection` 签名改为带 `now`（默认 None，向后兼容现有全部调用点）：

```python
    def public_projection(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
```

3d. interests 循环（1043-1048 行）改为在排序前加衰减字段并按衰减值排序：

```python
        def _decayed(row: dict[str, Any]) -> float:
            return _decayed_salience(row["salience"], row.get("last_observed_at"), now)

        for row in interests:
            row["decayed_salience"] = round(_decayed(row), 6)
        interests.sort(key=lambda row: (-row["decayed_salience"], -row["preference"], row["name"]))
```

3e. 模块级辅助函数（放在 `public_projection` 上方类外）：

```python
def _parse_optional_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

def _decayed_salience(raw: float, last_observed_at: Any, now: datetime) -> float:
    observed = _parse_optional_iso(last_observed_at)
    if observed is None:
        return float(raw)
    hours = (now - observed).total_seconds() / 3600.0
    if hours <= 0.0:
        return float(raw)
    return float(raw) * (0.5 ** (hours / 48.0))
```

3f. return dict（1114-1143 行）中 `"latest_plan": ...` 之后插入一个 key：

```python
            "positions": {
                "open": [
                    {"position_id": row["position_id"], "topic": row["topic"],
                     "proposition": row["proposition"], "falsifier": row["falsifier"],
                     "age_days": round((now - ( _parse_optional_iso(row["created_at"]) or now)).total_seconds() / 86400.0, 2)}
                    for row in self.positions.open_positions(now=now)
                ],
                "recent_resolutions": [
                    {"position_id": row["position_id"], "topic": row["topic"],
                     "proposition": row["proposition"], "status": row["status"],
                     "resolution": row.get("resolution")}
                    for row in self.positions.recent_resolutions(now=now)
                ],
            },
```

- [ ] **Step 4: 运行新测试 + 既有 content_mind 测试**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_content_mind_projection.py tests/test_content_mind.py -v
```

预期：全 PASS（`public_projection()` 既有调用不带 now 仍工作）。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/content_mind.py tests/test_content_mind_projection.py && git commit -m "feat: decayed interest salience view and positions in mind projection"
```

---

### Task 3: square_observation 注入 positions/兴趣地形 + 自传体指令

**Files:**
- Modify: `src/wechat_traffic_agent/square_observation.py`（`_mind_context` 48-61 行、`mind_instruction` 139-143 行、payload 170-172 行）
- Test: `tests/test_square_observation.py`（追加）

- [ ] **Step 1: 写失败测试（追加到 tests/test_square_observation.py 末尾）**

```python
def test_mind_context_carries_positions_and_interest_terrain():
    projection = {
        "beliefs": [{"proposition": "B", "stance": 0.4, "confidence": 0.6}],
        "interests": [
            {"name": "sol-staking", "salience": 1.0, "decayed_salience": 0.9, "preference": 0.7},
            {"name": "eth-btc", "salience": 1.0, "decayed_salience": 0.2, "preference": 0.5},
        ],
        "open_inquiries": [{"question": "q?", "origin": "self_question"}],
        "positions": {
            "open": [{"position_id": "pos-1", "topic": "sol-staking",
                      "proposition": "质押流入持续", "falsifier": "流入回落", "age_days": 1.5}],
            "recent_resolutions": [{"position_id": "pos-0", "topic": "t", "proposition": "p",
                                    "status": "REFUTED", "resolution": "错在 X"}],
        },
    }
    ctx = _mind_context(_FakeMind(projection))
    assert ctx["positions_open"][0]["position_id"] == "pos-1"
    assert ctx["recent_resolutions"][0]["status"] == "REFUTED"
    assert ctx["interest_terrain"][0]["name"] == "sol-staking"
    assert ctx["interest_terrain"][0]["decayed_salience"] == 0.9


def test_mind_context_without_positions_key_is_empty():
    ctx = _mind_context(_FakeMind({"beliefs": [], "interests": [], "open_inquiries": []}))
    assert ctx["positions_open"] == []
    assert ctx["recent_resolutions"] == []
    assert ctx["interest_terrain"] == []
```

文件顶部 import 行改为：

```python
from wechat_traffic_agent.square_observation import (
    prepare_observation, ObservationExpression, ObservationReview, _structure_fingerprint,
    _mind_context,
)
```

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_square_observation.py -v -k "positions or terrain"
```

预期：ImportError / FAIL。

- [ ] **Step 3: 实现**

3a. `_mind_context`（48-61 行）整体替换为：

```python
def _mind_context(mind):
    if mind is None:
        return {}
    try:
        projection = mind.public_projection()
    except Exception:
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
    positions_open = [dict(row) for row in (positions.get("open") or ())[:6]]
    resolutions = [dict(row) for row in (positions.get("recent_resolutions") or ())[:4]]
    return {"beliefs": beliefs, "interests": interests, "open_questions": questions,
            "interest_terrain": terrain, "positions_open": positions_open,
            "recent_resolutions": resolutions}
```

3b. `mind_instruction`（139-143 行）替换为（在原句后追加立场/连续性指令）：

```python
    mind_instruction = (
        "mind_context is this agent's actual Virtual Brain state: its interests, open questions and held worldview beliefs. "
        "Prefer supplied observations that touch these interests, help answer an open question, or support or weaken a held belief; "
        "when one does, make that connection the agent's own first-person view in the body and say which belief or question it bears on. "
        "positions_open are this agent's own published, falsifiable commitments. interest_terrain ranks what it keeps returning to "
        "(decayed_salience falls when a topic is left alone). recent_resolutions are how its past commitments ended. "
        "If a supplied observation tests an open position - its falsifier condition is now checkable with the supplied evidence - "
        "verifying that position outranks new topics: select the testing evidence, make the verdict explicit in the body, "
        "and declare position_update with position_id and verdict CONFIRMED or REFUTED plus a one-sentence resolution (required when REFUTED: what was wrong). "
        "Confirming or refuting your own published claim is learning progress, not failure; silently dropping it is the only wrong outcome. "
        "Write the body as the same continuous person: it may reference its earlier position by viewpoint, not by id. "
        if mind_context else "")
```

3c. payload 不变（`mind_context` 已随 dict 注入，新增 key 自动进入）。无需改 170-172 行。

- [ ] **Step 4: 运行测试**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_square_observation.py -v
```

预期：全 PASS（既有测试的 FakeMind projection 无新 key → 新字段为空，不破坏原行为）。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/square_observation.py tests/test_square_observation.py && git commit -m "feat: inject positions and interest terrain into square observation mind context"
```

---

### Task 4: position_update 声明 + 登记/转移/奖励 + 同话题连续闸

**Files:**
- Modify: `src/wechat_traffic_agent/square_observation.py`（`ObservationExpression` 16-31 行、`_attempt_observation` 136-229 行）
- Test: `tests/test_square_observation.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
class _ResolvingMind:
    """FakeMind that records learning-interest calls, backed by a real ledger."""

    def __init__(self, projection, ledger):
        self._projection = projection
        self.positions = ledger
        self.learning_calls = []

    def public_projection(self):
        return self._projection

    def observe_learning_interest(self, *, cycle, topic, reward, evidence_refs):
        self.learning_calls.append({"cycle": cycle, "topic": topic,
                                    "utility": reward.get("utility")})
        return {"status": "OBSERVED", "preference_updated": True}


def _projection_with_open_position():
    return {"beliefs": [], "interests": [], "open_inquiries": [],
            "positions": {"open": [{"position_id": "pos-x", "topic": "sol-staking",
                                    "proposition": "质押流入持续", "falsifier": "流入回落",
                                    "age_days": 2.0}],
                          "recent_resolutions": []}}


def test_position_update_resolves_open_position_and_rewards_topic(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    row = ledger.register(topic="sol-staking", proposition="质押流入持续",
                          falsifier="流入回落", evidence_refs=(), draft_id="seed", now=now)
    mind = _ResolvingMind(_projection_with_open_position(), ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
              "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            assert "positions_open" in prompt and "pos-x" in prompt
            return model(action="SHARE", rationale="Testing my own commitment.",
                body="SOL 质押过去24小时流入 120,000 SOL，没有回落，我此前判断的证伪条件没有触发，判断维持。",
                symbols=["SOL"], source_ids=["sol-data"],
                thesis="质押流入持续", invalidation="流入回落", value_focus="accountability",
                position_update=model.PositionUpdate(position_id=row["position_id"],
                                                     verdict="CONFIRMED", resolution="流入未回落"))
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="Direct test of the committed position.")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="pos-resolve", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "CONFIRMED"
    assert mind.learning_calls == [{"cycle": "pos-resolve", "topic": "sol-staking", "utility": 1.0}]


def test_position_update_ignored_for_unknown_or_non_open(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    mind = _ResolvingMind(_projection_with_open_position(), ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
              "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="SOL 质押过去24小时流入 120,000 SOL，规模维持。",
                symbols=["SOL"], source_ids=["sol-data"], thesis="t", invalidation="f",
                value_focus="v",
                position_update=model.PositionUpdate(position_id="pos-gone",
                                                     verdict="REFUTED", resolution="x"))
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="pos-gone-cycle", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"  # 未知 position 不拦帖
    assert ledger.get("pos-gone") is None
    assert mind.learning_calls == []


def test_new_invalidation_registers_position(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    mind = _ResolvingMind({"beliefs": [], "interests": [], "open_inquiries": [],
                           "positions": {"open": [], "recent_resolutions": []}}, ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
              "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="SOL 质押过去24小时流入 120,000 SOL，判断：流入将维持。",
                symbols=["SOL"], source_ids=["sol-data"], thesis="流入将维持",
                invalidation="流入回落至 50,000 SOL 以下", value_focus="v")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="pos-reg-cycle", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    open_rows = ledger.open_positions(now=now + timedelta(minutes=1))
    assert len(open_rows) == 1
    assert open_rows[0]["topic"] == "sol"
    assert open_rows[0]["falsifier"] == "流入回落至 50,000 SOL 以下"


def test_same_asset_three_posts_in_a_row_rejected(tmp_path):
    now = datetime.now(timezone.utc)
    store = SquareDraftStore(tmp_path)
    for i in range(3):
        draft = store.create(
            body=f"XYZ 第{i}条观察，走势与资金数据持续。",
            evidence={"kind": "SOURCE_OBSERVATIONS", "observed_at": now.isoformat(), "sources": []},
            symbols=["XYZ"], reader_question="", thesis="t", invalidation="",
            observation_provenance={"format": "OBSERVATION", "cycle": f"old-{i}"})
        draft.update(status="SUCCESS", published_at=now.isoformat())
        store.save(draft)
    mind = _ResolvingMind({"beliefs": [], "interests": [], "open_inquiries": [],
                           "positions": {"open": [], "recent_resolutions": []}},
                          PositionLedger(tmp_path / "cm"))
    source = {"evidence_id": "xyz-new", "source_kind": "public-api",
              "summary": "XYZ rolling 24h +2%, turnover 3000000 USDT", "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="XYZ 过去24小时上涨 2%，成交额 3,000,000 USDT。",
                symbols=["XYZ"], source_ids=["xyz-new"], thesis="t", invalidation="",
                value_focus="v")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="streak-cycle", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "COGNITION_ONLY"
    assert "streak" in result["rationale"]
```

文件顶部 import 增加：

```python
from datetime import timedelta
from wechat_traffic_agent.position_ledger import PositionLedger
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
```

（`SquareDraftStore` 若文件内已 import 过则去重。）

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_square_observation.py -v -k "position_update or registers_position or three_posts"
```

预期：FAIL（`PositionUpdate` 不存在 / 无 streak 拦截）。

- [ ] **Step 3: 实现**

3a. `ObservationExpression` 之前加模型、之后加字段（16-31 行区域）：

```python
class PositionUpdate(BaseModel):
    position_id: str
    verdict: Literal["CONFIRMED", "REFUTED"]
    resolution: str = ""
```

`ObservationExpression` 字段区（`value_focus` 之后）追加：

```python
    position_update: PositionUpdate | None = None
```

3b. `_attempt_observation` 中、结构查重（200-206 行）之后、review 调用之前，插入同话题连续闸：

```python
    if _repeat_topic_streak(context["recent_published"], choice.symbols):
        return {**result, "rationale": "Same asset streak: last three publications already cover it; choose another subject."}
```

模块级新函数（放 `_recent_formats` 后）：

```python
def _repeat_topic_streak(recent_published, symbols, *, streak=3):
    current = {str(s).upper() for s in (symbols or ())}
    if not current or len(recent_published) < streak:
        return False
    for post in recent_published[:streak]:
        if not current & {str(s).upper() for s in (post.get("symbols") or ())}:
            return False
    return True
```

3c. `_attempt_observation` 签名（136 行）加 `mind=None`；`prepare_observation` 的调用处（127-129 行）传 `mind=mind`。draft 创建段（223-229 行）替换为：

```python
    store = SquareDraftStore(state_root)
    draft = store.create(body=choice.body, evidence=evidence, symbols=choice.symbols,
        reader_question=choice.reader_question, thesis=choice.thesis, invalidation=choice.invalidation,
        observation_provenance=provenance)
    _record_position_outcome(mind, choice, draft, selected, cycle,
                             datetime.now(timezone.utc))
    draft = store.review(draft["draft_id"], reviewer="ferro-observation-cognition", evidence_check=review.rationale)
    return {**result, "status": "SQUARE_EXPRESSION_READY", "draft_id": draft["draft_id"],
            "draft_status": draft["status"], "publication_confirmed": False}
```

3d. 模块级新函数（放 `_repeat_topic_streak` 后）：

```python
def _position_topic(choice):
    symbols = [str(s).lower() for s in (choice.symbols or [])]
    if symbols:
        return "+".join(symbols)
    return re.sub(r"[^a-z0-9]+", "-", str(choice.value_focus or "").lower()).strip("-")[:60]

def _record_position_outcome(mind, choice, draft, selected, cycle, now):
    if mind is None or not hasattr(mind, "positions"):
        return
    ledger = mind.positions
    try:
        if choice.position_update is not None:
            target = ledger.get(choice.position_update.position_id)
            if target and target.get("status") == "OPEN":
                ledger.transition(choice.position_update.position_id,
                    verdict=choice.position_update.verdict,
                    resolution=choice.position_update.resolution,
                    evidence_refs=[row["evidence_id"] for row in selected],
                    draft_id=draft["draft_id"], now=now)
                try:
                    mind.observe_learning_interest(cycle=cycle,
                        topic=str(target.get("topic")),
                        reward={"kind": "position-resolution", "utility": 1.0},
                        evidence_refs=[row["evidence_id"] for row in selected])
                except (ValueError, RuntimeError):
                    pass
        if choice.invalidation.strip():
            ledger.register(topic=_position_topic(choice), proposition=choice.thesis.strip(),
                falsifier=choice.invalidation.strip(),
                evidence_refs=[row["evidence_id"] for row in selected],
                draft_id=draft["draft_id"], now=now)
    except (ValueError, OSError):
        pass  # 账本故障不拦发帖
```

3e. `prepare_observation` 的 `_attempt_observation(...)` 调用（127-129 行）加实参 `mind=mind`；`_attempt_observation` 签名（136 行）加 `mind=None`。

- [ ] **Step 4: 运行测试**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_square_observation.py tests/test_position_ledger.py -v
```

预期：全 PASS。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/square_observation.py tests/test_square_observation.py && git commit -m "feat: position registration, resolution with interest reward, and same-asset streak guard"
```

---

### Task 5: owner 决策上下文接入 positions + 核验优先指令

**Files:**
- Modify: `src/wechat_traffic_agent/owner_agent_episode.py`（`_decision_mind_context` 209-259 行、决策 prompt 约 608 行附近）
- Test: `tests/test_owner_decision_positions.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
from wechat_traffic_agent.owner_agent_episode import OwnerAgentEpisode

def test_decision_mind_context_includes_open_positions():
    current_mind = {
        "mind_tick": 7,
        "interests": [{"name": "sol", "salience": 1.0, "preference": 0.6, "familiarity": 0.5}],
        "open_inquiries": [], "beliefs": [],
        "positions": {"open": [{"position_id": "pos-1", "topic": "sol",
                                "proposition": "流入持续", "falsifier": "流入回落",
                                "age_days": 2.0}],
                      "recent_resolutions": []},
    }
    ctx = OwnerAgentEpisode._decision_mind_context(current_mind)
    assert ctx["positions"]["open"][0]["position_id"] == "pos-1"
    assert ctx["positions"]["open"][0]["age_days"] == 2.0


def test_decision_mind_context_without_positions_is_empty():
    ctx = OwnerAgentEpisode._decision_mind_context({"interests": [], "open_inquiries": [], "beliefs": []})
    assert ctx["positions"] == {"open": [], "recent_resolutions": []}
```

（若类名不是 `OwnerAgentEpisode`，以 `rg -n "^class " src/wechat_traffic_agent/owner_agent_episode.py` 实际名为准替换。）

- [ ] **Step 2: 运行确认失败**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_owner_decision_positions.py -v
```

预期：FAIL（KeyError/断言失败，无 positions key）。

- [ ] **Step 3: 实现**

3a. `_decision_mind_context` return dict 中 `"strategy_learning":` 之前插入：

```python
            "positions": {
                "open": [
                    {key: row.get(key)
                     for key in ("position_id", "topic", "proposition", "falsifier", "age_days")}
                    for row in list((current_mind.get("positions") or {}).get("open") or ())[:5]
                    if isinstance(row, Mapping)
                ],
                "recent_resolutions": [
                    {key: row.get(key)
                     for key in ("position_id", "topic", "status", "resolution")}
                    for row in list((current_mind.get("positions") or {}).get("recent_resolutions") or ())[:3]
                    if isinstance(row, Mapping)
                ],
            },
```

3b. 决策 prompt：在 608 行 `"Use research_trajectory ..."` 之前插入一句：

```python
            "When an open_position's falsifier is testable with current public data, verifying that position outranks starting a new topic: choose RUN_CONTENT_PIPELINE now and let the expression resolve it. "
```

- [ ] **Step 4: 运行测试 + 确认 prompt 修改在位**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/test_owner_decision_positions.py -v && rg -c "open_position" src/wechat_traffic_agent/owner_agent_episode.py
```

预期：2 passed；rg 输出 ≥1。

- [ ] **Step 5: 提交**

```bash
cd D:/ferro/wechat-traffic-agent && git add src/wechat_traffic_agent/owner_agent_episode.py tests/test_owner_decision_positions.py && git commit -m "feat: expose open positions to owner decision with verification-first instruction"
```

---

### Task 6: 全量回归 + 部署 + daily minimum 调整

**Files:** 无代码改动；服务器操作。

- [ ] **Step 1: 全量测试**

```bash
cd D:/ferro/wechat-traffic-agent && PYTHONPATH="src;D:/ferro/agent-runtime/src" python -m pytest tests/ -x -q 2>&1 | tail -15
```

预期：除已知既有失败（growth-loop native 3 个，见本地测试环境记忆）外全绿；新文件全部通过。若出现本次改动引入的失败，回到对应任务修复。

- [ ] **Step 2: 部署到服务器 current 并重启**

```bash
cd D:/ferro/wechat-traffic-agent && scp src/wechat_traffic_agent/position_ledger.py src/wechat_traffic_agent/content_mind.py src/wechat_traffic_agent/square_observation.py src/wechat_traffic_agent/owner_agent_episode.py ubuntu@43.133.181.149:/home/ubuntu/content-agent/current/src/wechat_traffic_agent/ && ssh ubuntu@43.133.181.149 "sudo systemctl restart content-agent.service && sleep 20 && systemctl is-active content-agent.service"
```

预期：`active`。不碰 agent-runtime（避开 venv editable 双写陷阱）。**不建备份目录**（用户反馈）。

- [ ] **Step 3: daily minimum 100 → 20**

```bash
ssh ubuntu@43.133.181.149 "python3 -c \"
import json
p='/home/ubuntu/content-agent-state/content/x/posting_contract.json'
c=json.load(open(p))
print('before:', c.get('minimum_original_posts'))
c['minimum_original_posts']=20
json.dump(c, open(p,'w'), ensure_ascii=False, indent=1)
print('after:', json.load(open(p))['minimum_original_posts'])
\""
```

- [ ] **Step 4: 上线验证（重启后 30-60 分钟）**

```bash
ssh ubuntu@43.133.181.149 "grep -c positions_open /home/ubuntu/content-agent-state/application/llm_audit/requests.jsonl | tail -1; test -f /home/ubuntu/content-agent-state/application/content_mind/positions.json && echo LEDGER_OK || echo LEDGER_EMPTY"
```

预期：`positions_open` 出现次数 >0（payload 注入成功）；`LEDGER_OK` 或 `LEDGER_EMPTY`（空合法——首条可证伪帖发布后才登记）。同时抽查一篇新帖是否带立场/自我连续表述。回滚：`git` 保留每任务独立 commit；服务器回滚 = 用 releases/ 历史版本目录替换 current 符号链接并重启。

---

## Self-Review 记录

- Spec 覆盖：①衰减视图→Task 2；②立场账本→Task 1/4；③自传体/地形注入→Task 3；选择策略核验优先→Task 3(payload 指令)+Task 5(决策层)；K=3 切换→Task 4 streak 闸；daily minimum→Task 6。二期项（信号扩容/风格蒸馏/核心 v2）不在本计划，符合 spec。
- 占位符：无 TBD/TODO；Task 1 实现里的底部 import 提醒已注明"实现时放顶部"。
- 类型一致性：`PositionUpdate.verdict` Literal 与 `PositionLedger.transition` verdict 校验一致（CONFIRMED/REFUTED 由 LLM 声明，ABANDONED 仅内部 sweep 使用）；`mind.observe_learning_interest(cycle, topic, reward, evidence_refs)` 与 content_mind.py:938 签名一致；`_decision_mind_context` 为 staticmethod、测试直接调用，与 owner_agent_episode.py:208 一致。
- 已知取舍：`observe_learning_interest` 的 event_id 按 cycle 幂等，同 cycle 双奖励会 ALREADY_OBSERVED——v1 接受（转移本身即连续性信号），已在 Task 4 以 try/except 包裹。
