from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .memory_consolidation import MemoryConsolidationService
from .memory_continuity import MemoryContinuityService
from .memory_episode import EpisodeMemoryService
from .memory_evolution import MemoryEvolutionEngine
from .memory_knowledge import ExperienceKnowledgeConsolidator
from .memory_procedural import ProceduralMemoryService
from .memory_salience import MemorySaliencePolicy
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
        self.knowledge = ExperienceKnowledgeConsolidator(self.state_root, store=self.store)
        self.evolution = MemoryEvolutionEngine(self.state_root, store=self.store)
        self.salience = MemorySaliencePolicy(self.state_root)
        self.continuity = MemoryContinuityService(self.state_root, store=self.store)
        self._life_event_episode_ids: dict[str, str] = {}
        for row in self.store.query_memories(
            memory_types=["EPISODIC"], access_scope="PRIVATE_OWNER", limit=5000,
        ):
            for event_id in (row.get("payload") or {}).get("derived_from") or []:
                if str(event_id).strip():
                    self._life_event_episode_ids[str(event_id)] = str(row["memory_id"])

    def observe_life_event(
        self,
        event: Mapping[str, Any],
        *,
        topic: str = "",
    ) -> dict[str, Any]:
        """Project one authoritative VirtualBrain autobiographical event into episodic memory."""
        event_id = str(event.get("event_id") or "").strip()
        summary = str(event.get("summary") or "").strip()
        if not event_id or not summary:
            return {"status": "SKIPPED", "reason": "missing_event_identity"}
        existing_id = self._life_event_episode_ids.get(event_id)
        if existing_id:
            return {"status": "ALREADY_RECORDED", "episode_id": existing_id, "knowledge_memory_ids": []}
        refs = list(dict.fromkeys(
            str(item).strip() for item in event.get("evidence_refs") or () if str(item).strip()
        )) or [event_id]
        raw_source = str(event.get("source") or "virtualbrain").strip()
        source = raw_source
        domain = str(event.get("domain") or "unknown").strip()
        if event_id.startswith("world-attention:"):
            source, domain = "world-feed-attention", "crypto-finance-world"
        elif event_id.startswith("prediction-outcome:"):
            source, domain = "prospective-world-outcome", "crypto-finance-world"
        elif event_id.startswith("content-action-outcome:"):
            source, domain = "content-action-prediction-outcome", "content-prediction"
        elif event_id.startswith("content-strategy-outcome:"):
            source, domain = "website-audience-outcome", "content-audience"
        elif event_id.startswith("content-process:"):
            source, domain = "content-process", "content-process"
        context_topic = str(topic or event.get("topic") or "").strip()
        memory_topic = "virtualbrain-life:" + domain + ((":" + context_topic) if context_topic else "")
        lesson = ""
        if source == "prospective-world-outcome":
            lesson = "Dated world predictions should be calibrated against observed evidence and explicit prediction error."
        elif source == "content-action-prediction-outcome":
            lesson = "Content forecasts should be calibrated against mature observed outcomes; prediction error is descriptive, not causal proof."
        elif source == "website-audience-outcome":
            lesson = "Mature audience response is descriptive evidence for strategy learning, not causal proof."
        provenance = event.get("provenance") if isinstance(event.get("provenance"), Mapping) else {}
        at = str(
            event.get("observed_at")
            or event.get("created_at")
            or provenance.get("ingested_at")
            or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        significance = min(1.0, max(0.0, float(event.get("significance") or 0.5)))
        salience = self.salience.score(event)
        episode = self.episodes.consolidate(
            [{"event_id": event_id, "actor": "ferro", "text": summary[:1600], "created_at": at}],
            goal=f"preserve lived experience in {domain}",
            outcome=str(event.get("outcome") if event.get("outcome") is not None else "OBSERVED"),
            lesson=lesson,
            prediction_error=("" if event.get("prediction_error") is None else str(event.get("prediction_error"))),
            evidence_refs=refs,
            attributes={
                "experience_kind": "VIRTUALBRAIN_AUTOBIOGRAPHICAL_EVENT",
                "source": source,
                "raw_authority_source": raw_source,
                "authority_id": provenance.get("authority_id"),
                "provenance_kind": event.get("provenance_kind") or provenance.get("source_kind"),
                "domain": domain,
                "topic": context_topic,
                "tick": event.get("tick"),
                "confidence": event.get("confidence"),
                "significance": event.get("significance"),
                "outcome": event.get("outcome"),
                "prediction_error": event.get("prediction_error"),
                "identity_relevance": event.get("identity_relevance"),
                "emotional_intensity": event.get("emotional_intensity"),
                "memory_salience": salience["salience"],
                "consolidation_priority": salience["consolidation_priority"],
                "decay_rate": salience["decay_rate"],
                "recall_boost": salience["recall_boost"],
                "neural_activation": salience["neural_activation"],
                "causal_claim": False,
            },
            topics=[memory_topic, "virtualbrain-life"],
            importance=min(0.98, max(0.5, significance, 0.45 + 0.5 * salience["consolidation_priority"])),
        )
        self._life_event_episode_ids[event_id] = episode.memory_id
        knowledge = self.knowledge.consolidate(
            topic=memory_topic,
            source_ref=episode.memory_id,
            minimum_support=2,
        ) if lesson else []
        return {
            "status": "RECORDED",
            "episode_id": episode.memory_id,
            "knowledge_memory_ids": [str(row.get("memory_id") or "") for row in knowledge],
            "causal_claim": False,
        }

    def backfill_life_history(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        limit: int = 256,
    ) -> dict[str, Any]:
        rows = [dict(row) for row in events if isinstance(row, Mapping)][-max(1, int(limit)):]
        recorded = already = skipped = 0
        errors: list[dict[str, str]] = []
        for row in rows:
            try:
                result = self.observe_life_event(row)
            except Exception as exc:
                errors.append({"event_id": str(row.get("event_id") or ""), "error_type": type(exc).__name__})
                continue
            status = str(result.get("status") or "")
            if status == "RECORDED":
                recorded += 1
            elif status == "ALREADY_RECORDED":
                already += 1
            else:
                skipped += 1
        return {
            "status": "OK" if not errors else "PARTIAL",
            "scanned": len(rows),
            "recorded": recorded,
            "already_recorded": already,
            "skipped": skipped,
            "errors": errors[:20],
        }

    def select_process_variant(
        self,
        *,
        action: str,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        name = "process:" + str(action).strip()
        existing = self._active_procedure(name)
        if existing is None:
            return None
        selected = self.procedures.select_variant(
            str(existing["memory_id"]),
            context=context or {
                "satisfied_preconditions": ["required capabilities and resources are available"],
            },
        )
        if selected is None:
            return None
        return {
            "procedure_id": str(existing["memory_id"]),
            **selected,
        }

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
        existing_episode = self._existing_process_episode(process_id)
        if existing_episode is not None:
            active_procedure = self._active_procedure("process:" + action)
            return {
                "status": "ALREADY_RECORDED",
                "episode_id": str(existing_episode["memory_id"]),
                "procedure_id": str((active_procedure or {}).get("memory_id") or ""),
                "relation_id": "",
                "daily_knowledge_id": "",
                "long_term_knowledge_id": "",
                "knowledge_memory_ids": [],
                "memory_evolution": {"status": "ALREADY_RECORDED"},
                "continuity_checkpoint": None,
            }
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
            evidence_refs=refs,
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
        evolution = {"status": "NO_NEW_SIGNALS"}
        if not success:
            signal = self.evolution.observe_signal(
                "PROCEDURE_FAILURE",
                evidence_ref=refs[0],
                detail=f"{action} failed: {failure_mode or outcome}"[:1000],
            )
            evolution = {
                "status": "ROUTED_TO_RUNTIME_EVOLUTION",
                "signal_id": str(signal.get("signal_id") or ""),
            }
        knowledge = self._consolidate_topic(topic=topic, source_ref=episode.memory_id)
        checkpoint = self._checkpoint_process_memory()
        return {
            "episode_id": episode.memory_id,
            "procedure_id": str((procedure or {}).get("memory_id") or ""),
            "relation_id": str((relation or {}).get("memory_id") or ""),
            "daily_knowledge_id": str((knowledge.get("daily") or {}).get("memory_id") or ""),
            "long_term_knowledge_id": str((knowledge.get("long_term") or {}).get("memory_id") or ""),
            "knowledge_memory_ids": [str(row.get("memory_id") or "") for row in knowledge.get("knowledge") or []],
            "memory_evolution": evolution,
            "continuity_checkpoint": checkpoint,
        }
    def _existing_process_episode(self, process_id: str) -> dict[str, Any] | None:
        marker = process_id + ":action"
        rows = self.store.query_memories(memory_types=["EPISODIC"], limit=5000)
        for row in reversed(rows):
            derived = list((row.get("payload") or {}).get("derived_from") or [])
            if marker in derived:
                return row
        return None

    def _active_procedure(self, name: str) -> dict[str, Any] | None:
        active = self.store.query_memories(
            memory_types=["PROCEDURAL"], statuses=["ACTIVE"], limit=1000,
        )
        return next(
            (row for row in reversed(active) if str((row.get("payload") or {}).get("name") or "") == name),
            None,
        )

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
        existing = self._active_procedure(name)
        if existing is not None:
            return self.procedures.record_outcome(
                str(existing["memory_id"]),
                success=success,
                evidence_ref=evidence_ref,
                failure_mode="" if success else (failure_mode or "verified_process_failure"),
                recovery_step="" if success else recovery_step,
                variant_steps=list(steps),
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
        knowledge = self.knowledge.consolidate(
            topic=topic,
            source_ref=source_ref,
            minimum_support=2,
        ) if len(ordered) >= 2 else []
        return {"daily": daily, "long_term": long_term, "knowledge": knowledge}

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
