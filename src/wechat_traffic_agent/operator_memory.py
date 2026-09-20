from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable
from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_observation import MemoryLifeObservatory, MemoryRecallTimer
from .memory_retrieval import MemoryRouter
from .memory_system import CanonicalMemoryStore


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


def memory_promotion_allowed_for_turn(owner_text: str) -> bool:
    value = _normalize(owner_text)
    explicit = re.search(
        r"请记住|记下来|保存.{0,8}长期记忆|存入.{0,8}记忆|remember\s+(?:this|that)|save.{0,20}memory",
        value,
        re.IGNORECASE,
    )
    if explicit:
        return True
    recall_only = re.search(
        r"回忆|历史对话|原始历史|聊天记录|刚才.{0,24}(?:说|回答|调用)|之前.{0,24}(?:说|问|回答|聊)|"
        r"what.{0,20}(?:did|have).{0,20}(?:say|ask)|earlier conversation|chat history",
        value,
        re.IGNORECASE,
    )
    return recall_only is None


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
        self.store = CanonicalMemoryStore(state_root)
        self.formation = MemoryFormationPipeline(state_root, store=self.store)
        self.store.import_legacy_jsonl(self.path)
        self.router = MemoryRouter(state_root, store=self.store)
        self.observatory = MemoryLifeObservatory(state_root)
        self._repair_legacy_mirror()

    def _repair_legacy_mirror(self) -> None:
        existing_ids: set[str] = set()
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except (ValueError, json.JSONDecodeError):
                            continue
                        if isinstance(row, dict) and str(row.get("memory_id") or "").strip():
                            existing_ids.add(str(row["memory_id"]))
            except OSError:
                existing_ids = set()
        missing = [
            row for row in self.store.list_memories(limit=5000)
            if row.get("source_type") == "OWNER_CONVERSATION"
            and str(row.get("memory_id") or "") not in existing_ids
        ]
        if not missing:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            for row in missing:
                mirror = {
                    "memory_id": row["memory_id"],
                    "kind": row["kind"],
                    "text": row["text"],
                    "importance": row["importance"],
                    "source_message_id": row.get("source_message_id") or row.get("source_ref") or "",
                    "created_at": row["created_at"],
                }
                handle.write(json.dumps(mirror, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    def all(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        learned = self.store.list_memories(limit=max(1, min(int(limit), 5000)))
        private_rows = [
            row for row in learned
            if str(row.get("access_scope") or "").upper() == "PRIVATE_OWNER"
        ]
        return [dict(row) for row in CORE_MEMORIES] + private_rows

    def remember_many(
        self,
        memories: Iterable[dict[str, Any]],
        *,
        source_message_id: str = "",
        source_text: str = "",
    ) -> list[dict[str, Any]]:
        result = self.formation.form_many(
            memories,
            context=FormationContext(
                source_type="OWNER_CONVERSATION",
                source_ref=str(source_message_id or ""),
                source_text=str(source_text or ""),
                authenticated_owner=True,
                access_scope="PRIVATE_OWNER",
            ),
        )
        self._repair_legacy_mirror()
        return list(result["saved"])

    def relevant(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        core = [dict(row) for row in CORE_MEMORIES]
        remaining = max(0, int(limit) - len(core))
        with MemoryRecallTimer() as timer:
            learned = self.router.recall(
                query,
                limit=max(1, remaining),
                access_scope="PRIVATE_OWNER",
            ) if remaining else []
        self.observatory.record_retrieval(
            query=query,
            retrieved_memory_ids=[str(row.get("memory_id") or "") for row in learned],
            recall_latency_ms=float(getattr(timer, "elapsed_ms", 0.0)),
            source="operator-long-term-memory",
        )
        return core + learned


__all__ = ["CORE_MEMORIES", "OperatorLongTermMemory", "memory_promotion_allowed_for_turn"]
