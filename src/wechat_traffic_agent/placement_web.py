"""Public web research adapter that offloads safely and falls back to the core."""
from __future__ import annotations

import asyncio
from datetime import datetime
from hashlib import sha256
import os
from typing import Any, Mapping

from .habitat_placement import (
    AUTH_NONE,
    CONF_PUBLIC,
    COST_FREE,
    HabitatPlacementController,
    PlacementContract,
)
from .network_capabilities import CAP_CONTENT_RESEARCH


class PlacementAwareWebSearch:
    def __init__(self, *, local_search: Any, placement: HabitatPlacementController) -> None:
        self.local_search = local_search
        self.placement = placement

    @staticmethod
    def _task_id(query: str, now: datetime) -> str:
        seed = (
            now.isoformat() + "\0" + query + "\0" + os.urandom(8).hex()
        ).encode()
        return "public-research:" + sha256(seed).hexdigest()[:24]

    async def search(self, *, query: str, now: datetime) -> list[dict[str, Any]]:
        normalized = " ".join(str(query).split())
        if not 2 <= len(normalized) <= 180:
            raise ValueError("web search query length is outside bounds")

        # Discovery itself is optional. Failure never removes the local public path.
        try:
            await asyncio.to_thread(
                lambda: self.placement.sync_network(
                    capability_ids=(CAP_CONTENT_RESEARCH,),
                    now=now,
                    lease_seconds=120,
                )
            )
        except Exception:
            return await self.local_search.search(query=normalized, now=now)

        contract = PlacementContract(
            task_id=self._task_id(normalized, now),
            capability_id=CAP_CONTENT_RESEARCH,
            confidentiality=CONF_PUBLIC,
            authority=AUTH_NONE,
            stateful=False,
            idempotent=True,
            replayable=True,
            preferred_cost=COST_FREE,
            required_reliability=0.5,
            created_at=now.isoformat(),
        )
        decision = self.placement.choose(contract, now=now, resource_pressure="NORMAL")
        if decision.mode != "REMOTE_WORKER":
            return await self.local_search.search(query=normalized, now=now)

        payload_ref = "public-query:sha256:" + sha256(normalized.encode()).hexdigest()
        self.placement.queue.enqueue(contract, payload_ref=payload_ref)
        try:
            dispatched = await asyncio.to_thread(
                lambda: self.placement.dispatch_task(
                    contract.task_id,
                    now=now,
                    resource_pressure="NORMAL",
                    payload_loader=lambda _task: {
                        "query": normalized,
                        "max_sources": 4,
                    },
                )
            )
        except Exception:
            return await self.local_search.search(query=normalized, now=now)

        if dispatched.get("status") == "COMPLETED":
            result = dispatched.get("result")
            if isinstance(result, Mapping):
                evidence = result.get("evidence")
                if isinstance(evidence, list):
                    rows = [dict(row) for row in evidence if isinstance(row, Mapping)]
                    if rows:
                        return rows

        # Public research is read-only and replayable, so local fallback is safe even
        # if the remote result was lost. Non-idempotent work never uses this adapter.
        return await self.local_search.search(query=normalized, now=now)


__all__ = ["PlacementAwareWebSearch"]
