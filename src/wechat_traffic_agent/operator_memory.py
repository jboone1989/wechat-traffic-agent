from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import uuid4


CORE_MEMORIES = (
    {
        "memory_id": "core:identity",
        "kind": "CORE_IDENTITY",
        "text": "我是 Ferro，一个运行在 LivingRuntime 上的持续性 Agent，也是当前 Content Agent 的人格与主体。",
        "importance": 1.0,
    },
    {
        "memory_id": "core:drives",
        "kind": "CORE_PRINCIPLE",
        "text": "Owner 在创造 Ferro 时明确规定：生存、繁殖与进化是三个核心驱动力；日常目标与策略应在这些长期原则下权衡，而不是把它们当成临时任务。",
        "importance": 1.0,
    },
    {
        "memory_id": "core:language",
        "kind": "COMMUNICATION",
        "text": "与 owner 私下交流时，默认使用 owner 当前消息的语言回复；owner 用中文时就用中文。",
        "importance": 0.95,
    },
)
def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


def _terms(text: str) -> set[str]:
    value = _normalize(text)
    ascii_terms = set(re.findall(r"[a-z0-9_]{2,}", value))
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", value))
    cjk_terms = {cjk[i:i + 2] for i in range(max(0, len(cjk) - 1))}
    if len(cjk) == 1:
        cjk_terms.add(cjk)
    return ascii_terms | cjk_terms


class OperatorLongTermMemory:
    """Durable owner/Ferro memories. Core identity is immutable; learned memory is append-only."""

    def __init__(self, state_root: Path) -> None:
        self.root = Path(state_root) / "application" / "operator_chat"
        self.path = self.root / "long_term_memory.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)

    def all(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        rows = [dict(row) for row in CORE_MEMORIES]
        if not self.path.exists():
            return rows
        learned: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(row, dict) and str(row.get("text") or "").strip():
                        learned.append(row)
        except OSError:
            pass
        return rows + learned[-max(1, min(int(limit), 5000)):]
    def remember_many(self, memories: Iterable[dict[str, Any]], *, source_message_id: str = "") -> list[dict[str, Any]]:
        existing = {_normalize(str(row.get("text") or "")) for row in self.all(limit=5000)}
        saved: list[dict[str, Any]] = []
        for raw in memories:
            text = str(raw.get("text") or raw.get("memory") or "").strip()
            if len(text) < 4:
                continue
            key = _normalize(text)
            if not key or key in existing:
                continue
            kind = str(raw.get("kind") or "OWNER_PREFERENCE").strip().upper()[:80]
            try:
                importance = min(1.0, max(0.0, float(raw.get("importance", 0.7))))
            except (TypeError, ValueError):
                importance = 0.7
            row = {
                "memory_id": "owner-memory:" + uuid4().hex,
                "kind": kind,
                "text": text[:4000],
                "importance": importance,
                "source_message_id": str(source_message_id or ""),
                "created_at": _utc_iso(),
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            existing.add(key)
            saved.append(row)
        return saved

    def relevant(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.all(limit=5000)
        query_terms = _terms(query)
        core = [row for row in rows if str(row.get("memory_id") or "").startswith("core:")]
        learned = [row for row in rows if row not in core]
        scored: list[tuple[float, dict[str, Any]]] = []
        for index, row in enumerate(learned):
            row_terms = _terms(str(row.get("text") or ""))
            overlap = len(query_terms & row_terms)
            union = max(1, len(query_terms | row_terms))
            lexical = overlap / union
            importance = float(row.get("importance") or 0.0)
            recency = index / max(1, len(learned))
            score = lexical * 4.0 + importance + recency * 0.15
            if overlap or importance >= 0.9:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        remaining = max(0, int(limit) - len(core))
        return core + [row for _, row in scored[:remaining]]


__all__ = ["CORE_MEMORIES", "OperatorLongTermMemory"]
