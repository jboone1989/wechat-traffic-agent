from __future__ import annotations

from typing import Any, Iterable, Mapping

from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_retrieval import MemoryRouter
from .memory_system import CanonicalMemoryStore


class ProceduralMemoryService:
    """M6 reusable procedures revised only from explicit verified evidence."""

    def __init__(
        self,
        state_root,
        *,
        store: CanonicalMemoryStore | None = None,
        formation: MemoryFormationPipeline | None = None,
    ) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.formation = formation or MemoryFormationPipeline(state_root, store=self.store)
        self.router = MemoryRouter(state_root, store=self.store)

    def learn_procedure(
        self,
        *,
        name: str,
        description: str,
        preconditions: Iterable[str],
        capabilities: Iterable[str],
        steps: Iterable[str],
        verification: Iterable[str],
        failure_modes: Iterable[str] = (),
        recovery: Iterable[str] = (),
        expected_cost: Mapping[str, Any] | None = None,
        source_type: str,
        source_ref: str,
        evidence_refs: Iterable[str],
        supersedes: str = "",
        success_count: int = 0,
        failure_count: int = 0,
        last_outcome: str = "",
    ) -> dict[str, Any]:
        name = str(name).strip()
        if not name:
            raise ValueError("procedure requires a name")
        evidence = [str(item).strip() for item in evidence_refs if str(item).strip()]
        if not evidence:
            raise ValueError("procedure requires verified evidence")
        payload = {
            "procedure_schema": 1,
            "name": name,
            "preconditions": [str(item) for item in preconditions if str(item).strip()],
            "capabilities": [str(item) for item in capabilities if str(item).strip()],
            "steps": [str(item) for item in steps if str(item).strip()],
            "verification": [str(item) for item in verification if str(item).strip()],
            "failure_modes": [str(item) for item in failure_modes if str(item).strip()],
            "recovery": [str(item) for item in recovery if str(item).strip()],
            "expected_cost": dict(expected_cost or {}),
            "success_count": int(success_count),
            "failure_count": int(failure_count),
            "execution_count": int(success_count) + int(failure_count),
            "last_outcome": str(last_outcome or ""),
            "derived_from": evidence,
        }
        result = self.formation.form_many(
            [{
                "memory_type": "PROCEDURAL",
                "kind": "PROCEDURE",
                "text": f"Procedure {name}: {description}",
                "claim_key": "procedure:" + name.casefold(),
                "importance": 0.9,
                "confidence": 1.0,
                "evidence_refs": evidence,
                "topics": ["procedure", name],
                "payload": payload,
                "supersedes": supersedes,
            }],
            context=FormationContext(
                source_type=source_type,
                source_ref=source_ref,
                access_scope="PRIVATE_OWNER",
            ),
        )
        if not result["saved"]:
            raise ValueError(str(result["decisions"][0].get("reason") or "procedure_rejected"))
        return dict(result["saved"][0])

    def record_outcome(
        self,
        procedure_id: str,
        *,
        success: bool,
        evidence_ref: str,
        failure_mode: str = "",
        recovery_step: str = "",
    ) -> dict[str, Any]:
        current = self.store.get_memory(procedure_id)
        if current is None or current.get("memory_type") != "PROCEDURAL":
            raise KeyError(procedure_id)
        evidence_ref = str(evidence_ref).strip()
        if not evidence_ref:
            raise ValueError("procedure outcome requires evidence_ref")
        payload = dict(current.get("payload") or {})
        failures = list(payload.get("failure_modes") or [])
        recovery = list(payload.get("recovery") or [])
        if failure_mode and failure_mode not in failures:
            failures.append(failure_mode)
        if recovery_step and recovery_step not in recovery:
            recovery.append(recovery_step)
        success_count = int(payload.get("success_count") or 0) + (1 if success else 0)
        failure_count = int(payload.get("failure_count") or 0) + (0 if success else 1)
        refs = list(dict.fromkeys([*(current.get("evidence_refs") or []), evidence_ref]))
        name = str(payload.get("name") or current.get("text") or "procedure")
        description = str(current.get("text") or "")
        if description.startswith(f"Procedure {name}: "):
            description = description[len(f"Procedure {name}: "):]
        revised = self.learn_procedure(
            name=name,
            description=description,
            preconditions=payload.get("preconditions") or [],
            capabilities=payload.get("capabilities") or [],
            steps=payload.get("steps") or [],
            verification=payload.get("verification") or [],
            failure_modes=failures,
            recovery=recovery,
            expected_cost=payload.get("expected_cost") or {},
            source_type="VERIFIED_EXECUTION_OUTCOME",
            source_ref=evidence_ref,
            evidence_refs=refs,
            supersedes=procedure_id,
            success_count=success_count,
            failure_count=failure_count,
            last_outcome="SUCCESS" if success else "FAILURE",
        )
        stored = self.store.get_memory(revised["memory_id"])
        if stored is None:
            raise RuntimeError("procedure revision missing")
        return stored

    def retrieve(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return self.router.recall(query, limit=limit, memory_types=["PROCEDURAL"])


__all__ = ["ProceduralMemoryService"]
