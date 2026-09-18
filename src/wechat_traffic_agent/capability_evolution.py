"""Continuous challenger discovery for replaceable Content Agent capabilities."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from agent_runtime.capabilities import (
    CapabilityChallenger,
    CapabilityReplacementEngine,
    ReplaceableCapability,
)


_SLOT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "capability_id": "subject_continuity",
        "current_binding_id": "livingruntime-subject:v1",
        "current_provider": "livingruntime-core",
        "core_protected": True,
        "search_terms": (),
    },
    {
        "capability_id": "browser_automation",
        "current_binding_id": "playwright:current",
        "current_provider": "playwright",
        "search_terms": (
            "open source browser automation agent library Playwright alternative GitHub",
            "ultrafast browser agent automation library GitHub",
        ),
    },
    {
        "capability_id": "remote_browser_service",
        "current_binding_id": "tinyfish:current",
        "current_provider": "tinyfish",
        "search_terms": (
            "AI agent cloud browser service alternative TinyFish browser automation",
            "open source remote browser automation agent service GitHub",
        ),
    },
    {
        "capability_id": "http_transport",
        "current_binding_id": "httpx:current",
        "current_provider": "httpx",
        "search_terms": (
            "Python async HTTP client library httpx alternative maintained",
        ),
    },
    {
        "capability_id": "web_content_extraction",
        "current_binding_id": "runtime-web-fetch:current",
        "current_provider": "agent-runtime",
        "search_terms": (
            "open source web content extraction library crawl4ai trafilatura readability GitHub",
            "agent web crawling extraction library maintained GitHub",
        ),
    },
)


class ContentCapabilityEvolution:
    """Discover better implementations while preserving capability identity."""

    def __init__(self, *, state_root: Path, capability_runtime: Any) -> None:
        self.state_root = Path(state_root)
        self.capability_runtime = capability_runtime
        self.engine = CapabilityReplacementEngine(
            state_path=(
                self.state_root
                / "application"
                / "capability_evolution"
                / "replacement.json"
            )
        )
        self._seed_slots()

    def _seed_slots(self) -> None:
        for raw in _SLOT_SPECS:
            if raw["capability_id"] in self.engine.slots:
                continue
            self.engine.register(
                ReplaceableCapability(
                    **raw,
                    search_interval_cycles=24,
                    minimum_trials=3,
                    improvement_threshold=0.05,
                    max_regression=0.05,
                    evidence_refs=("content-capability-slot:v1",),
                )
            )
    def review(
        self,
        *,
        now: datetime,
        cycle: int,
        pressure: str = "NORMAL",
        opportunity: bool = False,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for capability_id, spec in sorted(self.engine.slots.items()):
            if spec.core_protected:
                rows.append({
                    "capability_id": capability_id,
                    "status": "CORE_PROTECTED",
                    "core_protected": True,
                })
                continue
            due = self.engine.search_due(
                capability_id,
                cycle=cycle,
                health=1.0,
                opportunity=opportunity,
            )
            if not due:
                rows.append({
                    "capability_id": capability_id,
                    "status": "NOT_DUE",
                    "core_protected": False,
                })
                continue
            discovered: list[str] = []
            evidence_refs: list[str] = []
            for query in spec.search_terms[:2]:
                receipt = self.capability_runtime.invoke(
                    "web.search",
                    {"query": query, "limit": 6, "safesearch": 1},
                )
                evidence_refs.append(str(receipt.receipt_id))
                payload = dict(receipt.payload)
                if not payload.get("ok"):
                    continue
                for result in payload.get("results") or ():
                    if not isinstance(result, dict):
                        continue
                    challenger = self._challenger(
                        capability_id, result, cycle=cycle, evidence_ref=str(receipt.receipt_id)
                    )
                    if challenger is None:
                        continue
                    self.engine.discover(challenger)
                    discovered.append(challenger.candidate_id)
            persist = getattr(self.capability_runtime, "persist", None)
            if callable(persist):
                persist()
            self.engine.record_search(
                capability_id,
                cycle=cycle,
                evidence_refs=evidence_refs,
            )
            rows.append({
                "capability_id": capability_id,
                "status": "CHALLENGERS_DISCOVERED" if discovered else "SEARCHED_NO_CHALLENGER",
                "challenger_count": len(set(discovered)),
                "candidate_ids": sorted(set(discovered)),
                "core_protected": False,
            })
        return {
            "status": "OK",
            "observed_at": now.isoformat(),
            "cycle": int(cycle),
            "replaceable_count": sum(
                1 for item in self.engine.slots.values() if not item.core_protected
            ),
            "core_protected_count": sum(
                1 for item in self.engine.slots.values() if item.core_protected
            ),
            "slots": rows,
        }

    @staticmethod
    def _challenger(
        capability_id: str,
        result: dict[str, Any],
        *,
        cycle: int,
        evidence_ref: str,
    ) -> CapabilityChallenger | None:
        url = str(result.get("url") or "").strip()
        title = " ".join(str(result.get("title") or "").split())[:300]
        if not url or not title:
            return None
        digest = sha256(f"{capability_id}\0{url}".encode()).hexdigest()[:24]
        host = url.split("://", 1)[-1].split("/", 1)[0].lower()
        return CapabilityChallenger(
            capability_id=capability_id,
            candidate_id=f"challenger:{capability_id}:{digest}",
            provider=host or "web-discovery",
            source=url,
            description=title,
            discovered_cycle=int(cycle),
            evidence_refs=(evidence_ref, "url-sha256:" + sha256(url.encode()).hexdigest()),
        )

    def status(self) -> dict[str, Any]:
        return {
            "slots": [asdict(item) for item in self.engine.slots.values()],
            "challengers": [asdict(item) for item in self.engine.challengers.values()],
            "decisions": [
                {**asdict(item), "status": item.status.value}
                for item in self.engine.decisions[-50:]
            ],
        }


__all__ = ["ContentCapabilityEvolution"]
