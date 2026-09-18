"""Continuous challenger discovery for replaceable Content Agent capabilities."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
            "site:github.com browser automation agent library Playwright alternative",
            "site:pypi.org browser automation Playwright alternative Python",
        ),
    },
    {
        "capability_id": "remote_browser_service",
        "current_binding_id": "tinyfish:current",
        "current_provider": "tinyfish",
        "search_terms": (
            "cloud browser automation API for AI agents developer",
            "site:github.com remote browser automation agent service Playwright",
        ),
    },
    {
        "capability_id": "http_transport",
        "current_binding_id": "httpx:current",
        "current_provider": "httpx",
        "search_terms": (
            "site:github.com Python async HTTP client httpx alternative maintained",
            "site:pypi.org async HTTP client Python httpx alternative",
        ),
    },
    {
        "capability_id": "web_content_extraction",
        "current_binding_id": "runtime-web-fetch:current",
        "current_provider": "agent-runtime",
        "search_terms": (
            "site:github.com web content extraction crawl4ai trafilatura readability alternative",
            "site:pypi.org web content extraction crawler markdown Python",
        ),
    },
)

_CODE_HOSTS = frozenset({
    "github.com", "www.github.com", "gitlab.com", "www.gitlab.com",
    "pypi.org", "www.pypi.org",
})
_NOISE_HOSTS = frozenset({
    "openai.com", "www.openai.com", "python.org", "www.python.org",
    "wikipedia.org", "en.wikipedia.org", "ja.wikipedia.org",
    "baike.baidu.com",
})
_CAPABILITY_TOKENS: dict[str, tuple[str, ...]] = {
    "browser_automation": (
        "browser", "playwright", "chromium", "automation", "browser-use",
        "selenium", "puppeteer", "agent-browser",
    ),
    "http_transport": (
        "http", "client", "async", "requests", "httpx", "aiohttp",
    ),
    "web_content_extraction": (
        "crawl", "crawler", "extract", "reader", "readability", "trafilatura",
        "markdown", "scrape", "scraper", "crawl4ai",
    ),
    "remote_browser_service": (
        "browser", "playwright", "chromium", "automation", "browserbase",
        "browserless", "tinyfish", "steel", "browser-use",
    ),
}


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
        summary = " ".join(
            str(
                result.get("summary")
                or result.get("snippet")
                or result.get("content")
                or ""
            ).split()
        )[:800]
        if not url or not title:
            return None
        parsed = urlparse(url)
        host = str(parsed.hostname or "").lower()
        if not host or host in _NOISE_HOSTS:
            return None

        text = f"{title} {summary} {url}".lower()
        tokens = _CAPABILITY_TOKENS.get(capability_id, ())
        if tokens and not any(token in text for token in tokens):
            return None

        technical_source = host in _CODE_HOSTS
        if capability_id == "remote_browser_service":
            service_signal = any(
                token in text
                for token in (
                    "api", "developer", "docs", "sdk", "cloud browser",
                    "browser automation", "browserbase", "browserless",
                    "tinyfish", "steel.dev",
                )
            )
            technical_source = technical_source or service_signal
        if not technical_source:
            return None

        digest = sha256(f"{capability_id}\0{url}".encode()).hexdigest()[:24]
        return CapabilityChallenger(
            capability_id=capability_id,
            candidate_id=f"challenger:{capability_id}:{digest}",
            provider=host,
            source=url,
            description=title,
            discovered_cycle=int(cycle),
            evidence_refs=(
                evidence_ref,
                "technical-source-qualified:v1",
                "url-sha256:" + sha256(url.encode()).hexdigest(),
            ),
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
