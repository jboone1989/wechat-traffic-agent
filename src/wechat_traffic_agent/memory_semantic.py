from __future__ import annotations

from typing import Any, Iterable, Mapping

from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_system import CanonicalMemoryStore


class SemanticRelationalMemoryService:
    """M5 semantic/relational memories with explicit revision and provenance."""

    def __init__(
        self,
        state_root,
        *,
        store: CanonicalMemoryStore | None = None,
        formation: MemoryFormationPipeline | None = None,
    ) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.formation = formation or MemoryFormationPipeline(state_root, store=self.store)

    def remember_semantic(
        self,
        *,
        text: str,
        claim_key: str,
        source_type: str,
        source_ref: str,
        source_text: str = "",
        authenticated_owner: bool = False,
        importance: float = 0.8,
        confidence: float = 1.0,
        evidence_refs: Iterable[str] = (),
        entities: Iterable[str] = (),
        topics: Iterable[str] = (),
        supersedes: str = "",
        kind: str = "FACT",
    ) -> dict[str, Any]:
        result = self.formation.form_many(
            [{
                "memory_type": "SEMANTIC",
                "kind": kind,
                "text": text,
                "claim_key": claim_key,
                "importance": importance,
                "confidence": confidence,
                "evidence_refs": list(evidence_refs),
                "entities": list(entities),
                "topics": list(topics),
                "supersedes": supersedes,
            }],
            context=FormationContext(
                source_type=source_type,
                source_ref=source_ref,
                source_text=source_text,
                authenticated_owner=authenticated_owner,
                access_scope="PRIVATE_OWNER",
            ),
        )
        if not result["saved"]:
            raise ValueError(str(result["decisions"][0].get("reason") or "semantic_memory_rejected"))
        return dict(result["saved"][0])

    def remember_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_: str,
        source_type: str,
        source_ref: str,
        source_text: str = "",
        authenticated_owner: bool = False,
        confidence: float = 1.0,
        evidence_refs: Iterable[str] = (),
        supersedes: str = "",
    ) -> dict[str, Any]:
        subject, predicate, object_ = (str(value).strip() for value in (subject, predicate, object_))
        if not subject or not predicate or not object_:
            raise ValueError("relationship requires subject, predicate, and object")
        claim_key = f"relation:{subject.casefold()}:{predicate.casefold()}"
        result = self.formation.form_many(
            [{
                "memory_type": "RELATIONAL",
                "kind": "RELATIONSHIP",
                "text": f"{subject} {predicate} {object_}",
                "claim_key": claim_key,
                "confidence": confidence,
                "evidence_refs": list(evidence_refs),
                "entities": [subject, object_],
                "payload": {
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_,
                },
                "supersedes": supersedes,
            }],
            context=FormationContext(
                source_type=source_type,
                source_ref=source_ref,
                source_text=source_text,
                authenticated_owner=authenticated_owner,
                access_scope="PRIVATE_OWNER",
            ),
        )
        if not result["saved"]:
            raise ValueError(str(result["decisions"][0].get("reason") or "relationship_rejected"))
        return dict(result["saved"][0])

    def current_claim(self, claim_key: str) -> dict[str, Any] | None:
        for row in reversed(self.store.list_memories(limit=5000)):
            if row.get("memory_type") not in {"SEMANTIC", "RELATIONAL"}:
                continue
            if str(row.get("status") or "") != "ACTIVE":
                continue
            if str((row.get("payload") or {}).get("claim_key") or "") == str(claim_key):
                return row
        return None

    @staticmethod
    def worldview_projection(mind: Any) -> Mapping[str, Any]:
        """Read VirtualBrain worldview through its authoritative projection without copying claims."""
        projection = getattr(mind, "public_projection", None)
        if not callable(projection):
            return {"beliefs": [], "recent_belief_changes": [], "authority": "virtualbrain"}
        row = projection()
        return {
            "beliefs": list(row.get("beliefs") or []),
            "recent_belief_changes": list(row.get("recent_belief_changes") or []),
            "authority": "virtualbrain",
        }


__all__ = ["SemanticRelationalMemoryService"]
