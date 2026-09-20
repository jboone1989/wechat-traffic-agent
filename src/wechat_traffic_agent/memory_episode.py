from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_system import CanonicalMemoryStore


@dataclass(frozen=True)
class EpisodeResult:
    memory_id: str
    event_ids: tuple[str, ...]
    actors: tuple[str, ...]


class EpisodeMemoryService:
    """Consolidate bounded trajectory slices into inspectable episodic memories."""

    def __init__(
        self,
        state_root,
        *,
        store: CanonicalMemoryStore | None = None,
        formation: MemoryFormationPipeline | None = None,
    ) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.formation = formation or MemoryFormationPipeline(state_root, store=self.store)

    def consolidate(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        goal: str = "",
        decision: str = "",
        actions: Iterable[str] = (),
        outcome: str = "",
        lesson: str = "",
        prediction: str = "",
        prediction_error: str = "",
        related_episode_ids: Iterable[str] = (),
        memory_refs: Iterable[str] = (),
        tool_outcomes: Iterable[Mapping[str, Any]] = (),
        evidence_refs: Iterable[str] = (),
        attributes: Mapping[str, Any] | None = None,
        topics: Iterable[str] = (),
        importance: float = 0.8,
    ) -> EpisodeResult:
        normalized = [self._normalize_event(row) for row in events]
        normalized = [row for row in normalized if row is not None]
        if not normalized:
            raise ValueError("episode requires at least one trajectory event")
        event_ids = tuple(str(row["event_id"]) for row in normalized)
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("episode trajectory contains duplicate event ids")
        actors = tuple(dict.fromkeys(str(row["actor"]) for row in normalized if row["actor"]))
        started_at = str(normalized[0].get("created_at") or "")
        ended_at = str(normalized[-1].get("created_at") or started_at)
        summary = self._summary(normalized, goal=goal, outcome=outcome, lesson=lesson)
        extra_evidence = [str(item).strip() for item in evidence_refs if str(item).strip()]
        durable_refs = [str(item).strip() for item in memory_refs if str(item).strip()]
        normalized_tools = [
            {
                "capability_id": str(row.get("capability_id") or ""),
                "receipt_id": str(row.get("receipt_id") or ""),
                "status": str(row.get("status") or ""),
                "ok": bool(row.get("ok")),
                "evidence_ref": str(row.get("evidence_ref") or row.get("receipt_id") or ""),
            }
            for row in tool_outcomes
            if isinstance(row, Mapping)
        ]
        payload = {
            "episode_schema": 2,
            "started_at": started_at,
            "ended_at": ended_at,
            "actors": list(actors),
            "goal": str(goal or ""),
            "observations": [
                {"event_id": row["event_id"], "actor": row["actor"], "text": row["text"]}
                for row in normalized
            ],
            "decision": str(decision or ""),
            "actions": [str(item) for item in actions if str(item).strip()],
            "outcome": str(outcome or ""),
            "prediction": str(prediction or ""),
            "prediction_error": str(prediction_error or ""),
            "lesson": str(lesson or ""),
            "related_episode_ids": [
                str(item) for item in related_episode_ids if str(item).strip()
            ],
            "memory_refs": list(dict.fromkeys(durable_refs)),
            "tool_outcomes": normalized_tools,
            "attributes": dict(attributes or {}),
            "derived_from": list(event_ids),
        }
        result = self.formation.form_many(
            [{
                "memory_type": "EPISODIC",
                "kind": "EPISODE",
                "text": summary,
                "importance": importance,
                "confidence": 1.0,
                "source_refs": list(event_ids),
                "evidence_refs": list(dict.fromkeys([*event_ids, *extra_evidence])),
                "entities": list(actors),
                "topics": [str(item) for item in topics if str(item).strip()],
                "payload": payload,
                "created_at": ended_at or started_at or None,
            }],
            context=FormationContext(
                source_type="TRAJECTORY_CONSOLIDATION",
                source_ref=event_ids[0],
                access_scope="PRIVATE_OWNER",
            ),
        )
        if not result["saved"]:
            reason = str(result["decisions"][0].get("reason") or "episode_not_formed")
            if reason == "duplicate_memory":
                existing_id = str(result["decisions"][0].get("existing_memory_id") or "")
                existing = self.store.get_memory(existing_id)
                if existing is not None:
                    return EpisodeResult(existing_id, event_ids, actors)
            raise ValueError(reason)
        memory_id = str(result["saved"][0]["memory_id"])
        return EpisodeResult(memory_id, event_ids, actors)

    def related(self, episode_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        source = self.store.get_memory(episode_id)
        if source is None or source.get("memory_type") != "EPISODIC":
            return []
        payload = source.get("payload") or {}
        ids = [str(item) for item in payload.get("related_episode_ids") or []]
        rows = [self.store.get_memory(item) for item in ids[: max(1, int(limit))]]
        return [row for row in rows if row is not None]

    @staticmethod
    def _normalize_event(raw: Mapping[str, Any]) -> dict[str, str] | None:
        event_id = str(raw.get("event_id") or raw.get("message_id") or raw.get("receipt_id") or "").strip()
        text = str(raw.get("text") or raw.get("summary") or raw.get("detail") or "").strip()
        if not event_id or not text:
            return None
        actor = str(raw.get("actor") or raw.get("role") or raw.get("source") or "unknown").strip()
        return {
            "event_id": event_id,
            "actor": actor,
            "text": text[:4000],
            "created_at": str(raw.get("created_at") or raw.get("at") or ""),
        }

    @staticmethod
    def _summary(
        events: Sequence[Mapping[str, str]],
        *,
        goal: str,
        outcome: str,
        lesson: str,
    ) -> str:
        first = str(events[0].get("text") or "")[:280]
        parts = []
        if goal:
            parts.append("Goal: " + str(goal)[:500])
        else:
            parts.append("Situation: " + first)
        if outcome:
            parts.append("Outcome: " + str(outcome)[:500])
        if lesson:
            parts.append("Lesson: " + str(lesson)[:500])
        return " | ".join(parts)


__all__ = ["EpisodeMemoryService", "EpisodeResult"]
