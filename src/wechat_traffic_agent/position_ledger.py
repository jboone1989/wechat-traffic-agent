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

LEDGER_SCHEMA = 2
ABANDON_AFTER = timedelta(days=5)
RESOLUTION_WINDOW = timedelta(days=7)
EVIDENCE_CAP = 10
_TERMINAL = {"CONFIRMED", "REFUTED", "ABANDONED"}

# 不变量：公共写方法（register/transition/attach_post/append_evidence）禁止互相调用。
# _locked 里 flock 绑定在每次 os.open 的新 fd 上，同线程重入拿到的第二把 flock 与第一把
# 互斥（Linux 直接死锁，Windows 本地 fcntl=None 测不出来）。用 Lock 而非 RLock 让重入
# 在开发机上立刻以 "RuntimeError: cannot acquire an already held lock" 暴露，而非留到产线死锁。
_WRITE_LOCK = threading.Lock()


def parse_iso_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
    tokens = [t for t in re.split(r"[^0-9a-z一-鿿]+", str(topic).casefold()) if t]
    if not tokens:
        return False
    hay = str(text).casefold()
    return any(token in hay for token in tokens)


class PositionLedger:
    """OPEN -> CONFIRMED | REFUTED | ABANDONED; terminal states are final."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "positions.json"

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
            raw = item.get("post_ids")
            item["post_ids"] = [e for e in (_post_entry(v) for v in (raw if isinstance(raw, list) else [])) if e]
            clean.append(item)
        return clean

    def _save(self, rows: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"schema": LEDGER_SCHEMA, "positions": rows},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _valid(self, row: Mapping[str, Any]) -> bool:
        for key in ("position_id", "topic", "proposition", "status"):
            if not str(row.get(key) or "").strip():
                return False
        if row.get("status") == "OPEN" and not str(row.get("falsifier") or "").strip():
            return False
        return parse_iso_ts(row.get("created_at")) is not None

    def get(self, position_id: str) -> dict[str, Any] | None:
        for row in self._load():
            if row.get("position_id") == position_id:
                return row
        return None

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
                "post_ids": [e for e in (_post_entry(draft_id),) if e], "resolution": None,
                "source": source,
            }
            rows.append(row)
            self._save(rows)
            return dict(row)

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

    def _sweep(self, now: datetime) -> None:
        """Expire stale OPEN positions. Write-path only; reads must stay pure."""
        now = _aware(now)
        rows = self._load()
        changed = False
        for row in rows:
            if row.get("status") != "OPEN":
                continue
            created = parse_iso_ts(row.get("created_at"))
            if created and now - created > ABANDON_AFTER:
                row["status"] = "ABANDONED"
                row["resolution"] = "no verification within 5 days"
                row["updated_at"] = now.isoformat()
                changed = True
        if changed:
            self._save(rows)

    def open_positions(self, *, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
        now = _aware(now)
        rows = [row for row in self._load() if row.get("status") == "OPEN"]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return [dict(row) for row in rows[:limit]]

    def recent_resolutions(self, *, now: datetime, limit: int = 5) -> list[dict[str, Any]]:
        now = _aware(now)
        rows = []
        for row in self._load():
            if row.get("status") not in {"CONFIRMED", "REFUTED", "ABANDONED"}:
                continue
            updated = parse_iso_ts(row.get("updated_at"))
            if updated and now - updated <= RESOLUTION_WINDOW:
                rows.append((str(row.get("updated_at")), dict(row)))
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in rows[:limit]]
