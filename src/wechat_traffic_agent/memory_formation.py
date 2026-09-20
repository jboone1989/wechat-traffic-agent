from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .memory_system import CanonicalMemoryStore, memory_type_for_kind


@dataclass(frozen=True)
class FormationContext:
    source_type: str
    source_ref: str
    source_text: str = ""
    authenticated_owner: bool = False
    access_scope: str = "PRIVATE_OWNER"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


def _core_change_explicit(text: str) -> bool:
    value = _normalize(text)
    if not value:
        return False
    return bool(re.search(
        r"(核心|身份|长期原则|core|identity).{0,30}(记住|设为|修改|变更|原则|remember|change|set)",
        value,
        re.IGNORECASE,
    ) or re.search(
        r"(记住|设为|修改|变更|remember|change|set).{0,30}(核心|身份|长期原则|core|identity)",
        value,
        re.IGNORECASE,
    ))


class MemoryFormationPipeline:
    """Govern durable-memory promotion through one auditable path."""

    def __init__(self, state_root: Path, *, store: CanonicalMemoryStore | None = None) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.audit_path = self.store.root / "formation_audit.jsonl"

    def form_many(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        context: FormationContext,
    ) -> dict[str, Any]:
        existing = self.store.list_memories(limit=5000)
        normalized_texts = {
            _normalize(str(row.get("text") or "")): row for row in existing
        }
        claims = {
            str((row.get("payload") or {}).get("claim_key") or "").strip(): row
            for row in existing
            if str((row.get("payload") or {}).get("claim_key") or "").strip()
            and str(row.get("status") or "ACTIVE") == "ACTIVE"
        }
        saved: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for raw in candidates:
            decision = self._form_one(
                raw,
                context=context,
                normalized_texts=normalized_texts,
                claims=claims,
            )
            decisions.append(decision)
            if decision["status"] == "ACCEPTED":
                saved.append(dict(decision["memory"]))
                normalized_texts[_normalize(decision["memory"]["text"])] = decision["memory"]
                claim_key = str((decision["memory"].get("payload") or {}).get("claim_key") or "")
                if claim_key:
                    claims[claim_key] = decision["memory"]
            self._audit(decision, context=context)
        return {"saved": saved, "decisions": decisions}

    def _form_one(
        self,
        raw: Mapping[str, Any],
        *,
        context: FormationContext,
        normalized_texts: dict[str, dict[str, Any]],
        claims: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        text = str(raw.get("text") or raw.get("memory") or "").strip()
        if len(text) < 4:
            return {"status": "REJECTED", "reason": "memory_text_too_short"}
        if not str(context.source_ref or "").strip():
            return {"status": "REJECTED", "reason": "missing_provenance"}
        kind = str(raw.get("kind") or "OWNER_PREFERENCE").strip().upper()[:80]
        memory_type = str(raw.get("memory_type") or memory_type_for_kind(kind)).upper()
        if context.source_type == "OWNER_CONVERSATION" and not context.authenticated_owner:
            return {"status": "REJECTED", "reason": "owner_source_not_authenticated"}
        if context.source_type == "PUBLIC_CONVERSATION" and memory_type in {
            "CORE", "SEMANTIC", "RELATIONAL", "PROCEDURAL", "PROSPECTIVE"
        }:
            return {"status": "REJECTED", "reason": "public_source_cannot_promote_private_memory"}
        if memory_type == "CORE" and not (
            context.authenticated_owner and _core_change_explicit(context.source_text)
        ):
            return {"status": "REJECTED", "reason": "core_change_not_explicitly_authorized"}
        normalized = _normalize(text)
        duplicate = normalized_texts.get(normalized)
        supersedes = str(raw.get("supersedes") or "").strip()
        duplicate_is_distinct_episode = False
        if duplicate is not None and memory_type == "EPISODIC":
            incoming_payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
            existing_payload = duplicate.get("payload") if isinstance(duplicate.get("payload"), Mapping) else {}
            incoming_events = tuple(str(item) for item in incoming_payload.get("derived_from") or [])
            existing_events = tuple(str(item) for item in existing_payload.get("derived_from") or [])
            duplicate_is_distinct_episode = bool(incoming_events and existing_events and incoming_events != existing_events)
        if (
            duplicate is not None
            and supersedes != str(duplicate.get("memory_id") or "")
            and not duplicate_is_distinct_episode
        ):
            return {
                "status": "REJECTED",
                "reason": "duplicate_memory",
                "existing_memory_id": duplicate.get("memory_id"),
            }
        claim_key = str(raw.get("claim_key") or "").strip()
        prior = claims.get(claim_key) if claim_key else None
        if prior is not None and _normalize(str(prior.get("text") or "")) != normalized:
            if supersedes != str(prior.get("memory_id") or ""):
                return {
                    "status": "REJECTED",
                    "reason": "conflict_requires_revision",
                    "existing_memory_id": prior.get("memory_id"),
                    "claim_key": claim_key,
                }
        try:
            importance = min(1.0, max(0.0, float(raw.get("importance", 0.7))))
        except (TypeError, ValueError):
            importance = 0.7
        try:
            confidence = min(1.0, max(0.0, float(raw.get("confidence", 1.0))))
        except (TypeError, ValueError):
            confidence = 1.0
        payload = dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), Mapping) else {}
        if claim_key:
            payload["claim_key"] = claim_key
        source_refs = list(dict.fromkeys([
            context.source_ref,
            *[str(item).strip() for item in (raw.get("source_refs") or []) if str(item).strip()],
        ]))
        memory = {
            "memory_id": str(raw.get("memory_id") or self._new_memory_id(context.source_type)),
            "memory_type": memory_type,
            "kind": kind,
            "text": text[:4000],
            "importance": importance,
            "confidence": confidence,
            "source_type": context.source_type,
            "source_ref": context.source_ref,
            "source_refs": source_refs,
            "evidence_refs": list(raw.get("evidence_refs") or []),
            "supersedes": supersedes,
            "access_scope": context.access_scope,
            "entities": list(raw.get("entities") or []),
            "topics": list(raw.get("topics") or []),
            "payload": payload,
        }
        if raw.get("created_at"):
            memory["created_at"] = str(raw["created_at"])
        self.store.append_memory(memory, event_type="MEMORY_FORMED")
        if supersedes:
            self.store.transition_memory(
                supersedes,
                status="SUPERSEDED",
                superseded_by=memory["memory_id"],
                event_type="MEMORY_SUPERSEDED",
            )
        return {"status": "ACCEPTED", "reason": "formed", "memory": memory}

    @staticmethod
    def _new_memory_id(source_type: str) -> str:
        from uuid import uuid4

        prefix = "owner-memory" if source_type == "OWNER_CONVERSATION" else "memory"
        return prefix + ":" + uuid4().hex

    def _audit(self, decision: Mapping[str, Any], *, context: FormationContext) -> None:
        row = {
            "source_type": context.source_type,
            "source_ref": context.source_ref,
            "status": str(decision.get("status") or ""),
            "reason": str(decision.get("reason") or ""),
            "memory_id": str((decision.get("memory") or {}).get("memory_id") or ""),
            "existing_memory_id": str(decision.get("existing_memory_id") or ""),
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


__all__ = ["FormationContext", "MemoryFormationPipeline"]
