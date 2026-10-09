"""Collect decision evidence from live candidates and the canonical memory index.

Retrieval has no access to model prompts or publication state. The returned
candidate bundle can be ranked and selected independently using in-memory data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class CandidateRetrievalPorts:
    router_factory: Callable
    canonicalize: Callable
    timestamp: Callable
    declares_supersession: Callable
    dependency_tokens: Callable


@dataclass(frozen=True)
class CandidateBatch:
    candidates: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]]
    recall_ranks: dict[tuple[str, str], int]
    memory_errors: list[str]
    recall_count: int


class DecisionCandidateRetriever:
    def __init__(self, ports: CandidateRetrievalPorts):
        self.ports = ports

    def _recall(self, by_id, needs, state_root, now):
        ranks: dict[tuple[str, str], int] = {}
        errors: list[str] = []
        count = 0
        if state_root is None:
            return ranks, errors, count
        try:
            router = self.ports.router_factory(state_root)
            for need in needs:
                try:
                    rows = router.recall(need.question, limit=14, now=now)
                except Exception as exc:
                    errors.append(f"{need.need_id}:{type(exc).__name__}")
                    continue
                for rank, memory in enumerate(rows, start=1):
                    row = self.ports.canonicalize(memory)
                    cid = str(row["context_id"])
                    by_id[cid] = row
                    ranks[(need.need_id, cid)] = rank
                    count += 1
        except Exception as exc:
            errors.append(f"router:{type(exc).__name__}")
        return ranks, errors, count

    def _mark_superseded(self, candidates, owner_goal_ids, owner_mission_ids):
        generic = owner_goal_ids | owner_mission_ids
        superseders = [
            row for row in candidates
            if self.ports.declares_supersession(row)
            and self.ports.timestamp(row) is not None
        ]
        for newer in superseders:
            newer_at = self.ports.timestamp(newer)
            if newer_at is None:
                continue
            newer_tokens = self.ports.dependency_tokens(newer)
            for older in candidates:
                if older is newer or bool(older.get("current")):
                    continue
                older_at = self.ports.timestamp(older)
                if older_at is None or older_at >= newer_at:
                    continue
                specific_shared = (
                    newer_tokens & self.ports.dependency_tokens(older)
                ) - generic
                if specific_shared:
                    older["_temporal_shadowed"] = True

    def collect(self, *, local_candidates, needs, state_root, now,
                owner_goal_ids, owner_mission_ids) -> CandidateBatch:
        by_id = {}
        for raw in local_candidates:
            row = dict(raw)
            cid = str(row.get("context_id") or "")
            if cid:
                by_id[cid] = row
        ranks, errors, count = self._recall(by_id, needs, state_root, now)
        candidates = list(by_id.values())
        self._mark_superseded(candidates, owner_goal_ids, owner_mission_ids)
        return CandidateBatch(candidates, by_id, ranks, errors, count)
