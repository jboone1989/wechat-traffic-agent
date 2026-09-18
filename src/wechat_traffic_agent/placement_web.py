"""Public web research adapter with VirtualBrain placement learning and safe local fallback."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import resource
import shutil
import time
from typing import Any, Mapping

from .habitat_learning import ACTION_KEEP_LOCAL, ACTION_OFFLOAD
from .habitat_placement import (
    AUTH_NONE,
    CONF_PUBLIC,
    COST_FREE,
    HabitatPlacementController,
    PlacementContract,
)
from .network_capabilities import CAP_CONTENT_RESEARCH


def _rss_mib() -> float:
    # ru_maxrss is KiB on Linux; this deployment and CI both run on Linux.
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


class _Measure:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.wall = time.perf_counter()
        self.cpu = time.process_time()
        self.rss = _rss_mib()
        self.disk_free = float(shutil.disk_usage(self.root).free)

    def finish(self) -> dict[str, float]:
        return {
            "latency_ms": max(0.0, (time.perf_counter() - self.wall) * 1000.0),
            "process_cpu_ms": max(0.0, (time.process_time() - self.cpu) * 1000.0),
            "rss_delta_mib": _rss_mib() - self.rss,
            "disk_free_delta_mib": (
                float(shutil.disk_usage(self.root).free) - self.disk_free
            ) / (1024.0 * 1024.0),
        }


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

    @staticmethod
    def _worker_age_seconds(placement: HabitatPlacementController, worker_id: str | None, now: datetime) -> float | None:
        if not worker_id:
            return None
        worker = placement.registry.by_id(worker_id)
        if worker is None or worker.first_seen_at is None:
            return None
        first = datetime.fromisoformat(worker.first_seen_at.replace("Z", "+00:00"))
        return max(0.0, (now.astimezone(timezone.utc) - first.astimezone(timezone.utc)).total_seconds())

    async def _local(
        self, *, query: str, now: datetime, task_id: str | None = None,
        learning_decision: Mapping[str, Any] | None = None,
        fallback_used: bool = False, prior_metrics: Mapping[str, float] | None = None,
        prior_started_at: datetime | None = None, prior_error: str | None = None,
    ) -> list[dict[str, Any]]:
        measure = _Measure(self.placement.state_root)
        started_at = prior_started_at or datetime.now(timezone.utc)
        rows = await self.local_search.search(query=query, now=now)
        metrics = measure.finish()
        if prior_metrics:
            metrics = {
                "latency_ms": float(prior_metrics.get("latency_ms", 0.0)) + metrics["latency_ms"],
                "process_cpu_ms": float(prior_metrics.get("process_cpu_ms", 0.0)) + metrics["process_cpu_ms"],
                "rss_delta_mib": float(prior_metrics.get("rss_delta_mib", 0.0)) + metrics["rss_delta_mib"],
                "disk_free_delta_mib": float(prior_metrics.get("disk_free_delta_mib", 0.0)) + metrics["disk_free_delta_mib"],
            }
        learning = getattr(self.placement, "learning", None)
        if learning is not None and task_id and learning_decision:
            selected = learning_decision.get("selected") or {}
            action = str(selected.get("action") or ACTION_KEEP_LOCAL)
            learning.record_execution(
                task_id=task_id,
                episode_id=str(learning_decision["episode_id"]),
                capability_id=CAP_CONTENT_RESEARCH,
                action=action,
                worker_id=selected.get("worker_id"),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                success=(action == ACTION_KEEP_LOCAL and bool(rows)) or (
                    action == ACTION_OFFLOAD and not fallback_used
                ),
                fallback_used=fallback_used,
                latency_ms=metrics["latency_ms"],
                process_cpu_ms=metrics["process_cpu_ms"],
                rss_delta_mib=metrics["rss_delta_mib"],
                disk_free_delta_mib=metrics["disk_free_delta_mib"],
                worker_age_seconds=self._worker_age_seconds(
                    self.placement, selected.get("worker_id"), now
                ),
                error_type=prior_error,
                evidence_refs=("public-web-research",),
            )
        return rows

    async def search(self, *, query: str, now: datetime) -> list[dict[str, Any]]:
        normalized = " ".join(str(query).split())
        if not 2 <= len(normalized) <= 180:
            raise ValueError("web search query length is outside bounds")

        try:
            await asyncio.to_thread(
                lambda: self.placement.sync_network(
                    capability_ids=(CAP_CONTENT_RESEARCH,),
                    now=now,
                    lease_seconds=120,
                )
            )
        except Exception:
            return await self._local(query=normalized, now=now)

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
        learning = getattr(self.placement, "learning", None)
        if learning is None:
            hard = self.placement.choose(contract, now=now, resource_pressure="NORMAL")
            if hard.mode != "REMOTE_WORKER":
                return await self._local(query=normalized, now=now)
            decision = {
                "episode_id": "",
                "selected": {"action": ACTION_OFFLOAD, "worker_id": hard.worker_id},
            }
        else:
            decision = learning.decide(
                contract=contract,
                now=now,
                resource_pressure=learning.current_pressure(),
            )
            if str((decision.get("selected") or {}).get("action")) != ACTION_OFFLOAD:
                return await self._local(
                    query=normalized,
                    now=now,
                    task_id=contract.task_id,
                    learning_decision=decision,
                )

        payload_ref = "public-query:sha256:" + sha256(normalized.encode()).hexdigest()
        self.placement.queue.enqueue(contract, payload_ref=payload_ref)
        measure = _Measure(self.placement.state_root)
        started_at = datetime.now(timezone.utc)
        try:
            dispatched = await asyncio.to_thread(
                lambda: self.placement.dispatch_task(
                    contract.task_id,
                    now=now,
                    resource_pressure=(
                        learning.current_pressure() if learning is not None else "NORMAL"
                    ),
                    payload_loader=lambda _task: {
                        "query": normalized,
                        "max_sources": 4,
                    },
                )
            )
        except Exception as exc:
            metrics = measure.finish()
            return await self._local(
                query=normalized,
                now=now,
                task_id=contract.task_id if learning is not None else None,
                learning_decision=decision if learning is not None else None,
                fallback_used=True,
                prior_metrics=metrics,
                prior_started_at=started_at,
                prior_error=type(exc).__name__,
            )

        metrics = measure.finish()
        if dispatched.get("status") == "COMPLETED":
            result = dispatched.get("result")
            if isinstance(result, Mapping):
                evidence = result.get("evidence")
                if isinstance(evidence, list):
                    rows = [dict(row) for row in evidence if isinstance(row, Mapping)]
                    if rows:
                        if learning is not None:
                            selected = decision["selected"]
                            learning.record_execution(
                                task_id=contract.task_id,
                                episode_id=str(decision["episode_id"]),
                                capability_id=CAP_CONTENT_RESEARCH,
                                action=ACTION_OFFLOAD,
                                worker_id=selected.get("worker_id"),
                                started_at=started_at,
                                completed_at=datetime.now(timezone.utc),
                                success=True,
                                fallback_used=False,
                                latency_ms=metrics["latency_ms"],
                                process_cpu_ms=metrics["process_cpu_ms"],
                                rss_delta_mib=metrics["rss_delta_mib"],
                                disk_free_delta_mib=metrics["disk_free_delta_mib"],
                                worker_age_seconds=self._worker_age_seconds(
                                    self.placement, selected.get("worker_id"), now
                                ),
                                error_type=None,
                                evidence_refs=("public-web-research",),
                            )
                        return rows

        # Public research is read-only and replayable; local fallback is safe.
        return await self._local(
            query=normalized,
            now=now,
            task_id=contract.task_id if learning is not None else None,
            learning_decision=decision if learning is not None else None,
            fallback_used=True,
            prior_metrics=metrics,
            prior_started_at=started_at,
            prior_error=str(dispatched.get("error_type") or dispatched.get("status") or "REMOTE_UNCONFIRMED"),
        )


__all__ = ["PlacementAwareWebSearch"]
