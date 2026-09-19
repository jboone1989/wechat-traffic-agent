from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_system import CanonicalMemoryStore


@dataclass(frozen=True)
class SelfMemoryPacket:
    current_intent: tuple[dict[str, Any], ...]
    current_resources: Mapping[str, Any]
    current_capabilities: tuple[str, ...]
    current_predictions: tuple[dict[str, Any], ...]
    self_model: Mapping[str, Any]


class AuthoritativeStateAdapters:
    def __init__(self) -> None:
        self._adapters: dict[str, Callable[[str], Mapping[str, Any] | None]] = {}

    def register(
        self,
        name: str,
        resolver: Callable[[str], Mapping[str, Any] | None],
    ) -> None:
        key = str(name).strip().lower()
        if not key:
            raise ValueError("authority adapter name is required")
        self._adapters[key] = resolver

    def resolve(self, name: str, object_id: str) -> Mapping[str, Any] | None:
        resolver = self._adapters.get(str(name).strip().lower())
        if resolver is None:
            return None
        return resolver(str(object_id))


class ProspectiveSelfMemoryService:
    """M9 references current intentions/self-state without copying their mutable authority."""

    def __init__(
        self,
        state_root,
        *,
        store: CanonicalMemoryStore | None = None,
        adapters: AuthoritativeStateAdapters | None = None,
    ) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.formation = MemoryFormationPipeline(state_root, store=self.store)
        self.adapters = adapters or AuthoritativeStateAdapters()

    def remember_reference(
        self,
        *,
        reference_kind: str,
        object_id: str,
        description: str,
        authority: str,
        source_ref: str,
        deadline: str = "",
    ) -> dict[str, Any]:
        if not all(str(value).strip() for value in (reference_kind, object_id, authority, source_ref)):
            raise ValueError("prospective reference requires kind, object id, authority and source_ref")
        result = self.formation.form_many(
            [{
                "memory_type": "PROSPECTIVE",
                "kind": "PROSPECTIVE_REFERENCE",
                "text": str(description or f"{reference_kind} {object_id}"),
                "importance": 0.9,
                "confidence": 1.0,
                "topics": ["prospective", str(reference_kind)],
                "payload": {
                    "authority": str(authority),
                    "reference_kind": str(reference_kind),
                    "object_id": str(object_id),
                    "deadline": str(deadline or ""),
                },
            }],
            context=FormationContext(
                source_type="AUTHORITATIVE_RUNTIME_REFERENCE",
                source_ref=str(source_ref),
                access_scope="PRIVATE_OWNER",
            ),
        )
        if not result["saved"]:
            raise ValueError(str(result["decisions"][0].get("reason") or "prospective_reference_rejected"))
        return dict(result["saved"][0])

    def resolve_reference(self, memory_id: str) -> Mapping[str, Any] | None:
        row = self.store.get_memory(memory_id)
        if row is None or row.get("memory_type") != "PROSPECTIVE":
            return None
        payload = row.get("payload") or {}
        return self.adapters.resolve(
            str(payload.get("authority") or ""),
            str(payload.get("object_id") or ""),
        )

    @staticmethod
    def snapshot_from_runtime(application: Any, mind: Any | None = None) -> SelfMemoryPacket:
        runtime = getattr(application, "domain_runtime", None)
        goals: list[dict[str, Any]] = []
        missions: list[dict[str, Any]] = []
        for goal in list(getattr(runtime, "active_goals", []) or []):
            goals.append({
                "kind": "GOAL",
                "goal_id": str(getattr(goal, "goal_id", "") or ""),
                "description": str(getattr(goal, "description", "") or ""),
                "status": str(getattr(getattr(goal, "status", ""), "value", getattr(goal, "status", "")) or ""),
                "priority": getattr(goal, "priority", None),
            })
        for mission in list(getattr(runtime, "external_missions", []) or []):
            deadline = getattr(mission, "deadline", None)
            missions.append({
                "kind": "MISSION",
                "mission_id": str(getattr(mission, "mission_id", "") or ""),
                "goal_id": str(getattr(mission, "goal_id", "") or ""),
                "description": str(getattr(mission, "description", "") or ""),
                "metric": str(getattr(mission, "metric", "") or ""),
                "target": getattr(mission, "target", None),
                "status": str(getattr(getattr(mission, "status", ""), "value", getattr(mission, "status", "")) or ""),
                "deadline": deadline.isoformat() if hasattr(deadline, "isoformat") else str(deadline or ""),
            })
        mind_projection: dict[str, Any] = {}
        project = getattr(mind, "public_projection", None)
        if callable(project):
            raw = project()
            if isinstance(raw, dict):
                mind_projection = raw
        runtime_capabilities = getattr(getattr(application, "runtime", None), "capabilities", None)
        capabilities: list[str] = []
        if isinstance(runtime_capabilities, Mapping):
            capabilities = [str(key) for key in runtime_capabilities]
        elif isinstance(runtime_capabilities, (list, tuple, set)):
            capabilities = [str(item) for item in runtime_capabilities]
        predictions = list(mind_projection.get("predictions") or [])
        self_model = {
            "mind_schema": mind_projection.get("mind_schema"),
            "mind_tick": mind_projection.get("mind_tick"),
            "interests": list(mind_projection.get("interests") or []),
            "open_inquiries": list(mind_projection.get("open_inquiries") or []),
            "beliefs": list(mind_projection.get("beliefs") or []),
            "latest_plan": mind_projection.get("latest_plan"),
        }
        return SelfMemoryPacket(
            current_intent=tuple(goals + missions),
            current_resources=dict(mind_projection.get("resource_awareness") or {}),
            current_capabilities=tuple(capabilities),
            current_predictions=tuple(predictions),
            self_model=self_model,
        )


__all__ = [
    "AuthoritativeStateAdapters", "ProspectiveSelfMemoryService", "SelfMemoryPacket",
]
