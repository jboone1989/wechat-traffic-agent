"""Temporary adapter from Ferro's legacy MemoryService to Runtime capability v1.

This file is intentionally the only new Ferro-side memory integration point.
The target is to move the implementation out of Ferro while keeping this
provider-neutral contract unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.capabilities import (
    MEMORY_CAPABILITY_ID,
    CapabilityCall,
    CapabilityProtocolError,
    CapabilityReply,
)

from .memory_service import MemoryService

PROVIDER_ID = "ferro.memory.legacy"


class LegacyFerroMemoryCapabilityProvider:
    """Expose the current MemoryService behind the standard agent.memory port."""

    def __init__(self, service: MemoryService, *, subject_id: str) -> None:
        self.service = service
        self.subject_id = _string(subject_id, "subject_id")

    def manifest(self) -> Mapping[str, Any]:
        return {
            "provider": PROVIDER_ID,
            "capabilities": [
                {
                    "capability_id": MEMORY_CAPABILITY_ID,
                    "description": "Legacy Ferro memory implementation behind Runtime memory v1",
                    "side_effect_level": "LOCAL_STATE",
                    "required_permissions": [],
                }
            ],
        }

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if capability_id != MEMORY_CAPABILITY_ID:
            raise ValueError("provider is bound only to agent.memory")
        call = CapabilityCall.from_mapping(
            payload or {}, expected_capability_id=MEMORY_CAPABILITY_ID
        )
        if call.subject_id != self.subject_id:
            return CapabilityReply.failure(
                call,
                provider=PROVIDER_ID,
                error_code="SUBJECT_MISMATCH",
                error_message="memory provider is bound to another subject",
            ).to_mapping()

        operation = call.operation
        if operation == "remember":
            record = _mapping(call.payload.get("record"), "record")
            created = self.service.store.append_memory(record)
            self.service.sync()
            return CapabilityReply.success(
                call,
                provider=PROVIDER_ID,
                payload={
                    "created": bool(created),
                    "memory_id": _string(record.get("memory_id"), "memory_id"),
                },
            ).to_mapping()

        if operation == "recall":
            query = _string(call.payload.get("query"), "query", allow_empty=True)
            limit = _bounded_int(call.payload.get("limit"), "limit", minimum=1, maximum=100)
            topic = _string(call.payload.get("topic"), "topic", allow_empty=True)
            node_types = _string_list(call.payload.get("node_types"), "node_types")
            source_ids = _string_list(call.payload.get("source_ids"), "source_ids")
            items = self.service.search(
                query,
                topic=topic,
                limit=limit,
                node_types=node_types,
                source_ids=source_ids,
            )
            return CapabilityReply.success(
                call,
                provider=PROVIDER_ID,
                payload={"items": items},
            ).to_mapping()

        if operation == "read":
            memory_id = _string(call.payload.get("memory_id"), "memory_id")
            offset = _bounded_int(call.payload.get("offset"), "offset", minimum=0)
            return CapabilityReply.success(
                call,
                provider=PROVIDER_ID,
                payload=self.service.read(memory_id, offset=offset),
            ).to_mapping()

        if operation == "changes":
            since = _bounded_int(call.payload.get("since"), "since", minimum=0)
            limit = _bounded_int(call.payload.get("limit"), "limit", minimum=1, maximum=100)
            return CapabilityReply.success(
                call,
                provider=PROVIDER_ID,
                payload={"items": self.service.changes(since, limit=limit)},
            ).to_mapping()

        if operation == "health":
            if call.payload:
                raise CapabilityProtocolError("health payload must be empty")
            self.service.sync()
            return CapabilityReply.success(
                call,
                provider=PROVIDER_ID,
                payload={"status": "ok"},
            ).to_mapping()

        return CapabilityReply.failure(
            call,
            provider=PROVIDER_ID,
            error_code="UNKNOWN_OPERATION",
            error_message=operation,
        ).to_mapping()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityProtocolError(f"{field} must be a mapping")
    return dict(value)


def _string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CapabilityProtocolError(f"{field} must be a string")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CapabilityProtocolError(f"{field} must be a string array")
    return list(value)


def _bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise CapabilityProtocolError(f"{field} is out of range")
    return value


async def memory_decision(**kwargs):
    """Bind Ferro's decision formatting to Runtime's retrieval controller."""
    from agent_runtime.memory.session import memory_decision as run_retrieval
    from .content_loop import _structured, _structured_prompt
    return await run_retrieval(
        **kwargs, decision_runner=_structured, prompt_builder=_structured_prompt,
        memory_context_builder=lambda payload: payload,
    )


__all__ = ["LegacyFerroMemoryCapabilityProvider", "PROVIDER_ID", "memory_decision"]
