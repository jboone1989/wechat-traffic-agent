"""Content Agent orchestration for Runtime-owned autonomous resource discovery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.resource_discovery import ResourceNeed, ResourceScout


class ContentResourceScout:
    def __init__(self, *, state_root: Path, capability_runtime: Any) -> None:
        self.state_root = Path(state_root)
        self.capability_runtime = capability_runtime
        self.scout = ResourceScout(self.state_root / "application", self._search)

    def maybe_discover(self, *, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("resource scout time requires timezone")
        runs = self._runs()
        if runs:
            last = datetime.fromisoformat(str(runs[-1]["observed_at"]).replace("Z", "+00:00"))
            if now.astimezone(timezone.utc) - last < timedelta(hours=24):
                return {
                    "status": "NOT_DUE",
                    "last_observed_at": last.isoformat(),
                    "opportunity_count": len(self.scout.opportunities()),
                }
        need = ResourceNeed(
            resource_kind="llm_inference",
            objective="reduce financial burn while preserving useful task quality",
            reason="paid-provider concentration exists and no low-cost alternative has trial evidence",
            constraints=(
                "public evidence only",
                "never send secrets during discovery",
                "prefer zero-cost or recurring free quota",
                "no paid signup or privilege expansion without operator approval",
            ),
        )
        created = self.scout.discover(need, now=now, max_queries=3, results_per_query=6)
        opportunities = self.scout.opportunities()
        return {
            "status": "DISCOVERED" if created else "NO_NEW_OPPORTUNITIES",
            "new_count": len(created),
            "opportunity_count": len(opportunities),
            "claimed_free_count": sum(1 for x in opportunities if x.claimed_free),
            "credential_hint_count": sum(1 for x in opportunities if x.credential_hint),
            "new_opportunities": [
                {
                    "opportunity_id": x.opportunity_id,
                    "title": x.title,
                    "url": x.url,
                    "claimed_free": x.claimed_free,
                    "credential_hint": x.credential_hint,
                    "status": x.status,
                }
                for x in created[:12]
            ],
        }

    def _search(self, query: str, limit: int, source_class: str = "general"):
        # agent-runtime owns source-class planning/provenance; the underlying
        # web.search capability remains provider-neutral and does not need the
        # classification as a transport parameter.
        _ = source_class
        return self.capability_runtime.invoke(
            "web.search", {"query": query, "limit": int(limit), "safesearch": 1}
        )

    def _runs(self) -> list[dict[str, Any]]:
        folder = self.state_root / "application" / "resource_discovery" / "runs"
        rows=[]
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                value=json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value,dict) and value.get("observed_at"): rows.append(value)
            except (OSError,json.JSONDecodeError):
                continue
        return sorted(rows,key=lambda x:str(x["observed_at"]))


__all__=["ContentResourceScout"]
