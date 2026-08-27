"""Offline stand-ins shared across test modules."""
from __future__ import annotations


class StubRuntime:
    """Minimal fake of Agent Runtime's advisory backend for unit tests."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.tasks: list[str] = []
        self.max_tokens_seen: list[int] = []

    async def cognitive_probe(self, task: str, max_tokens: int) -> str:
        self.tasks.append(task)
        self.max_tokens_seen.append(max_tokens)
        if not self._responses:
            raise AssertionError("unexpected extra cognitive_probe call")
        return self._responses.pop(0)
