from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .memory_consolidation import MemoryConsolidationService
from .memory_continuity import MemoryContinuityService
from .memory_episode import EpisodeMemoryService
from .memory_evolution import MemoryEvolutionEngine
from .memory_procedural import ProceduralMemoryService
from .memory_semantic import SemanticRelationalMemoryService
from .memory_system import CanonicalMemoryStore


class MemoryLifeRuntime:
    """Long-running experience -> knowledge and action -> procedure bridge.

    This service is deliberately domain-agnostic: callers provide verified process
    evidence; Memory System decides durable episode/procedure/semantic projections.
    """

    def __init__(self, state_root: Path, *, store: CanonicalMemoryStore | None = None) -> None:
        self.state_root = Path(state_root)
        self.store = store or CanonicalMemoryStore(self.state_root)
        self.episodes = EpisodeMemoryService(self.state_root, store=self.store)
        self.procedures = ProceduralMemoryService(self.state_root, store=self.store)
        self.semantic = SemanticRelationalMemoryService(self.state_root, store=self.store)
        self.consolidation = MemoryConsolidationService(self.state_root, store=self.store)
        self.evolution = MemoryEvolutionEngine(self.state_root, store=self.store)
        self.continuity = MemoryContinuityService(self.state_root, store=self.store)

    def observe_process_outcome(
        self,
        *,
        process_id: str,
        topic: str,
        action: str,
        success: bool,
        outcome: str,
        evidence_refs: Iterable[str],
        capabilities: Iterable[str] = (),
        steps: Iterable[str] = (),
        verification: Iterable[str] = (),
        failure_mode: str = "",
        recovery_step: str = "",
        expected_cost: Mapping[str, Any] | None = None,
        observed_at: str = "",
    ) -> dict[str, Any]:
        process_id = str(process_id).strip()
        topic = str(topic).strip() or "general-process"
        action = str(action).strip()
        refs = list(dict.fromkeys(str(item).strip() for item in evidence_refs if str(item).strip()))
        if not process_id or not action or not refs:
            raise ValueError("process outcome requires process_id, action, and evidence_refs")
        at = str(observed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        lesson = (
            f"Verified success for {action}: {outcome}"
            if success else f"Verified failure for {action}: {failure_mode or outcome}"
        )
        episode = self.episodes.consolidate(
            [
                {"event_id": process_id + ":action", "actor": "ferro", "text": action, "created_at": at},
                {"event_id": process_id + ":outcome", "actor": "world", "text": outcome, "created_at": at},
            ],
            goal=action,
            actions=[action],
            outcome=outcome,
            lesson=lesson,
            topics=[topic, "process-experience"],
            importance=0.82 if success else 0.9,
        )
        procedure = self._update_procedure(
            action=action,
            success=success,
            evidence_ref=refs[0],
            capabilities=capabilities,
            steps=steps,
            verification=verification,
            failure_mode=failure_mode,
            recovery_step=recovery_step,
            expected_cost=expected_cost,
        )
        relation = self._record_capability_relation(action, capabilities, refs[0]) if success else None
        knowledge = self._consolidate_topic(topic=topic, source_ref=episode.memory_id)
        checkpoint = self._checkpoint_process_memory()
        return {
            "episode_id": episode.memory_id,
            "procedure_id": str((procedure or {}).get("memory_id") or ""),
            "relation_id": str((relation or {}).get("memory_id") or ""),
            "daily_knowledge_id": str((knowledge.get("daily") or {}).get("memory_id") or ""),
            "long_term_knowledge_id": str((knowledge.get("long_term") or {}).get("memory_id") or ""),
            "continuity_checkpoint": checkpoint,
        }
    def _update_procedure(
        self,
        *,
        action: str,
        success: bool,
        evidence_ref: str,
        capabilities: Iterable[str],
        steps: Iterable[str],
        verification: Iterable[str],
        failure_mode: str,
        recovery_step: str,
        expected_cost: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        name = "process:" + action
        active = self.store.query_memories(memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=1000)
        existing = next(
            (row for row in reversed(active) if str((row.get("payload") or {}).get("name") or "") == name),
            None,
        )
        if existing is not None:
            return self.procedures.record_outcome(
                str(existing["memory_id"]),
                success=success,
                evidence_ref=evidence_ref,
                failure_mode="" if success else (failure_mode or "verified_process_failure"),
                recovery_step="" if success else recovery_step,
            )
        step_rows = [str(item).strip() for item in steps if str(item).strip()] or [action]
        verify_rows = [str(item).strip() for item in verification if str(item).strip()] or ["require verified outcome evidence"]
        return self.procedures.learn_procedure(
            name=name,
            description=f"Execute {action} and learn only from verified world outcomes.",
            preconditions=["required capabilities and resources are available"],
            capabilities=list(capabilities),
            steps=step_rows,
            verification=verify_rows,
            failure_modes=[] if success else [failure_mode or "verified_process_failure"],
            recovery=[] if success or not recovery_step else [recovery_step],
            expected_cost=expected_cost or {},
            source_type="VERIFIED_EXECUTION_OUTCOME",
            source_ref=evidence_ref,
            evidence_refs=[evidence_ref],
            success_count=1 if success else 0,
            failure_count=0 if success else 1,
            last_outcome="SUCCESS" if success else "FAILURE",
        )

    def _record_capability_relation(
        self, action: str, capabilities: Iterable[str], source_ref: str
    ) -> dict[str, Any] | None:
        values = [str(item).strip() for item in capabilities if str(item).strip()]
        if not values:
            return None
        object_ = values[0]
        predicate = "verified-capability:" + object_
        claim_key = f"relation:ferro:{predicate.casefold()}"
        current = self.semantic.current_claim(claim_key)
        if current is not None and str((current.get("payload") or {}).get("object") or "") == object_:
            return current
        return self.semantic.remember_relation(
            subject="Ferro",
            predicate=predicate,
            object_=object_,
            source_type="VERIFIED_EXECUTION_OUTCOME",
            source_ref=source_ref,
            evidence_refs=[source_ref],
            supersedes=str(current.get("memory_id") or "") if current else "",
        )

    def _consolidate_topic(self, *, topic: str, source_ref: str) -> dict[str, Any]:
        episodes = self.store.query_memories(
            memory_types=["EPISODIC"], statuses=["ACTIVE"], topic=topic, limit=500,
        )
        ordered = [str(row["memory_id"]) for row in reversed(episodes)]
        daily = None
        long_term = None
        if len(ordered) >= 3:
            daily = self.consolidation.consolidate_daily_episodes(
                ordered[-24:], source_ref=source_ref, topic=topic
            )
        if len(ordered) >= 6:
            long_term = self.consolidation.consolidate_long_term_pattern(
                topic=topic, source_ref=source_ref, minimum_episodes=6
            )
        return {"daily": daily, "long_term": long_term}

    def _checkpoint_process_memory(self) -> dict[str, Any] | None:
        episodes = self.store.query_memories(
            memory_types=["EPISODIC"], statuses=["ACTIVE"], topic="process-experience", limit=5000,
        )
        if not episodes or len(episodes) % 10:
            return None
        row = self.continuity.append_checkpoint(
            reason="process-memory-runtime",
            critical_refs=[str(episodes[0]["memory_id"])],
        )
        return {
            "sequence": int(row["sequence"]),
            "record_hash": str(row["record_hash"]),
            "state_hash": str(row["state_hash"]),
        }


__all__ = ["MemoryLifeRuntime"]
