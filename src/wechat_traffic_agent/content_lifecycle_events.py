"""Typed, synchronous content lifecycle signals.

Immediate decisions remain direct calls.  Completed phases are observed by
independently injected subscribers; website publication already owns a separate
durable PublicationEvent and distribution outbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar

EventT = TypeVar("EventT")


@dataclass(frozen=True)
class DraftChecked:
    cycle: str
    topic: str
    passed: bool
    evidence_refs: tuple[str, ...]
    draft_hash: str
    model_calls: int
    folder: Path


@dataclass(frozen=True)
class ContentCycleSettled:
    cycle: str
    topic: str
    passed: bool
    status: str
    evidence_refs: tuple[str, ...]
    process_steps: tuple[str, ...]
    failure_mode: str
    model_calls: int
    observed_at: datetime
    publication_enabled: bool


class ContentEventRouter:
    """An in-process event router, no new JSON queue or duplicated state."""

    def __init__(self):
        self._subscribers: dict[type, list[tuple[str, Callable]]] = {}

    def subscribe(self, event_type: type[EventT], name: str,
                  handler: Callable[[EventT], object]) -> None:
        subscribers = self._subscribers.setdefault(event_type, [])
        if any(other_name == name for other_name, _ in subscribers):
            raise ValueError("duplicate content event subscriber: " + name)
        subscribers.append((name, handler))

    def emit(self, event: EventT) -> dict[str, object]:
        return {
            name: handler(event)
            for name, handler in self._subscribers.get(type(event), ())
        }
