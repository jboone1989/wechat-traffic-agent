from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_system import CanonicalMemoryStore


RECALL_TIERS = ("HOT", "WARM", "COLD", "ARCHIVED")


def _parse_time(value: str) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recall_tier(memory: dict[str, Any], *, now: datetime | None = None) -> str:
    if str(memory.get("status") or "ACTIVE") == "ARCHIVED":
        return "ARCHIVED"
    importance = float(memory.get("importance") or 0.0)
    if importance >= 0.95:
        return "HOT"
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_days = max(0.0, (current - _parse_time(str(memory.get("created_at") or ""))).total_seconds() / 86400.0)
    if age_days <= 7:
        return "HOT"
    if age_days <= 30:
        return "WARM"
    if age_days <= 180:
        return "COLD"
    return "ARCHIVED"


def recall_weight(memory: dict[str, Any], *, now: datetime | None = None) -> float:
    return {"HOT": 1.0, "WARM": 0.72, "COLD": 0.38, "ARCHIVED": 0.12}[recall_tier(memory, now=now)]


class MemoryConsolidationService:
    """M8 bounded consolidation that derives new memories only from traceable memory evidence."""

    def __init__(self, state_root, *, store: CanonicalMemoryStore | None = None) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.formation = MemoryFormationPipeline(state_root, store=self.store)

    def _active_claim(self, claim_key: str) -> dict[str, Any] | None:
        for row in reversed(self.store.list_memories(limit=5000)):
            if str(row.get("status") or "") != "ACTIVE":
                continue
            if str((row.get("payload") or {}).get("claim_key") or "") == claim_key:
                return row
        return None

    def consolidate_daily_episodes(
        self,
        episode_ids: Iterable[str],
        *,
        source_ref: str,
        topic: str = "daily-reflection",
    ) -> dict[str, Any] | None:
        episodes = [self.store.get_memory(str(item)) for item in episode_ids]
        episodes = [row for row in episodes if row and row.get("memory_type") == "EPISODIC"]
        lessons = []
        for row in episodes:
            lesson = str((row.get("payload") or {}).get("lesson") or "").strip()
            if lesson and lesson not in lessons:
                lessons.append(lesson)
        if not lessons:
            return None
        evidence = [str(row["memory_id"]) for row in episodes]
        text = "Daily consolidation: " + " | ".join(lessons[:8])
        claim_key = "daily-consolidation:" + str(topic)
        prior = self._active_claim(claim_key)
        result = self.formation.form_many(
            [{
                "memory_type": "SEMANTIC",
                "kind": "CONSOLIDATED_LESSON",
                "text": text,
                "claim_key": claim_key,
                "importance": min(0.95, 0.65 + 0.05 * len(lessons)),
                "confidence": 1.0,
                "evidence_refs": evidence,
                "topics": [topic],
                "payload": {"derived_from": evidence, "lessons": lessons},
                "supersedes": str(prior.get("memory_id") or "") if prior else "",
            }],
            context=FormationContext(
                source_type="MEMORY_CONSOLIDATION",
                source_ref=str(source_ref),
                access_scope="PRIVATE_OWNER",
            ),
        )
        return dict(result["saved"][0]) if result["saved"] else None

    def consolidate_long_term_pattern(
        self,
        *,
        topic: str,
        source_ref: str,
        minimum_episodes: int = 3,
    ) -> dict[str, Any] | None:
        episodes = self.store.query_memories(
            memory_types=["EPISODIC"], statuses=["ACTIVE"], topic=topic, limit=500,
        )
        if len(episodes) < max(2, int(minimum_episodes)):
            return None
        lessons = []
        for row in episodes:
            lesson = str((row.get("payload") or {}).get("lesson") or "").strip()
            if lesson and lesson not in lessons:
                lessons.append(lesson)
        if len(lessons) < 2:
            return None
        evidence = [str(row["memory_id"]) for row in episodes]
        text = f"Long-term pattern for {topic}: " + " | ".join(lessons[:12])
        claim_key = "long-term-pattern:" + str(topic)
        prior = self._active_claim(claim_key)
        result = self.formation.form_many(
            [{
                "memory_type": "SEMANTIC",
                "kind": "LONG_TERM_PATTERN",
                "text": text,
                "claim_key": claim_key,
                "importance": 0.9,
                "confidence": min(1.0, 0.55 + 0.08 * len(episodes)),
                "evidence_refs": evidence,
                "topics": [topic],
                "payload": {"derived_from": evidence, "lessons": lessons, "episode_count": len(episodes)},
                "supersedes": str(prior.get("memory_id") or "") if prior else "",
            }],
            context=FormationContext(
                source_type="MEMORY_CONSOLIDATION",
                source_ref=str(source_ref),
                access_scope="PRIVATE_OWNER",
            ),
        )
        return dict(result["saved"][0]) if result["saved"] else None


class TrajectoryColdArchive:
    """Lossless gzip archive for raw append-only trajectory files."""

    def __init__(self, state_root) -> None:
        self.root = Path(state_root) / "application" / "memory_system" / "cold_trajectory"
        self.root.mkdir(parents=True, exist_ok=True)

    def archive_jsonl(self, source: Path, *, archive_name: str | None = None) -> Path:
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(source)
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        name = archive_name or (source.name + ".gz")
        if not name.endswith(".gz"):
            name += ".gz"
        target = self.root / name
        with gzip.open(target, "wb", compresslevel=9) as handle:
            handle.write(raw)
        manifest = {
            "source_name": source.name,
            "archive_name": target.name,
            "sha256": digest,
            "bytes": len(raw),
        }
        (target.with_suffix(target.suffix + ".json")).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    @staticmethod
    def read_archive(path: Path) -> bytes:
        with gzip.open(Path(path), "rb") as handle:
            return handle.read()


__all__ = [
    "MemoryConsolidationService", "RECALL_TIERS", "TrajectoryColdArchive",
    "recall_tier", "recall_weight",
]
