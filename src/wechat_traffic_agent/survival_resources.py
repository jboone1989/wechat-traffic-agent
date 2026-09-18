"""Host survival-resource observations, compute scouting, and migration-node registry."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
from typing import Any
from urllib.parse import urlparse


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("survival resource time requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)

@dataclass(frozen=True)
class HostResourceObservation:
    observed_at: str
    hostname: str
    disk_total_gib: float
    disk_used_gib: float
    disk_free_gib: float
    disk_used_ratio: float
    mem_total_gib: float | None
    mem_available_gib: float | None
    evidence_ref: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class HostResourceObserver:
    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "resource_observations" / "host"

    @staticmethod
    def _memory() -> tuple[float | None, float | None]:
        try:
            values: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                token = rest.strip().split()[0]
                if token.isdigit():
                    values[key] = int(token)
            total = round(values["MemTotal"] / 1024 / 1024, 3) if "MemTotal" in values else None
            available = round(values["MemAvailable"] / 1024 / 1024, 3) if "MemAvailable" in values else None
            return total, available
        except OSError:
            return None, None

    def observe(self, now: datetime) -> HostResourceObservation:
        observed_at = _iso(now)
        disk = shutil.disk_usage(self.state_root)
        mem_total, mem_available = self._memory()
        canonical = {
            "observed_at": observed_at,
            "hostname": socket.gethostname(),
            "disk_total_gib": round(disk.total / 1024**3, 3),
            "disk_used_gib": round(disk.used / 1024**3, 3),
            "disk_free_gib": round(disk.free / 1024**3, 3),
            "disk_used_ratio": round(disk.used / disk.total, 4) if disk.total else 1.0,
            "mem_total_gib": mem_total,
            "mem_available_gib": mem_available,
        }
        digest = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = HostResourceObservation(**canonical, evidence_ref="host-resource:sha256:" + digest)
        payload = row.payload()
        stamp = observed_at.replace(":", "").replace("+00:00", "Z")
        path = self.root / (stamp + "-" + digest[:12] + ".json")
        if not path.exists():
            _atomic_json(path, payload)
        _atomic_json(self.root / "latest.json", payload)
        return row


@dataclass(frozen=True)
class ComputeOpportunity:
    opportunity_id: str
    title: str
    url: str
    provider_host: str
    snippet: str
    observed_at: str
    source_ref: str


class ComputeResourceScout:
    QUERIES = (
        "official always free virtual machine compute instance persistent disk",
        "official free tier VM server compute persistent storage",
        "official free VPS cloud compute instance no cost",
        "official free ARM virtual machine compute tier",
    )

    def __init__(self, *, state_root: Path, capability_runtime: Any) -> None:
        self.state_root = Path(state_root)
        self.runtime = capability_runtime
        self.root = self.state_root / "application" / "compute_resource_discovery"

    def _runs(self) -> list[dict[str, Any]]:
        folder = self.root / "runs"
        rows: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return rows

    def maybe_discover(self, *, now: datetime, urgent: bool = False) -> dict[str, Any]:
        observed_at = _iso(now)
        runs = self._runs()
        if runs:
            last = datetime.fromisoformat(str(runs[-1]["observed_at"]).replace("Z", "+00:00"))
            delay = timedelta(minutes=30) if urgent else timedelta(hours=12)
            if now.astimezone(timezone.utc) - last < delay:
                return {
                    "status": "NOT_DUE",
                    "last_observed_at": last.isoformat(),
                    "urgent": urgent,
                }
        existing = {row["opportunity_id"] for row in self.opportunities()}
        created: list[ComputeOpportunity] = []
        receipt_refs: list[str] = []
        for query in self.QUERIES:
            receipt = self.runtime.invoke("web.search", {
                "query": query,
                "limit": 8,
                "safesearch": 1,
                "categories": ["it"],
            })
            receipt_id = str(getattr(receipt, "receipt_id", "") or "")
            if receipt_id:
                receipt_refs.append(receipt_id)
            payload = getattr(receipt, "payload", {}) or {}
            for raw in payload.get("results") or []:
                url = str(raw.get("url") or "").strip()
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    continue
                opportunity_id = "compute:" + sha256(url.encode()).hexdigest()[:24]
                if opportunity_id in existing:
                    continue
                row = ComputeOpportunity(
                    opportunity_id=opportunity_id,
                    title=str(raw.get("title") or "")[:240],
                    url=url,
                    provider_host=parsed.hostname.lower(),
                    snippet=str(raw.get("snippet") or "")[:1200],
                    observed_at=observed_at,
                    source_ref=receipt_id or ("search-url:sha256:" + sha256(url.encode()).hexdigest()),
                )
                target = self.root / "opportunities" / (opportunity_id.replace(":", "_") + ".json")
                _atomic_json(target, asdict(row))
                existing.add(opportunity_id)
                created.append(row)
        run = {
            "observed_at": observed_at,
            "urgent": urgent,
            "new_opportunity_ids": [row.opportunity_id for row in created],
            "receipt_refs": sorted(set(receipt_refs)),
        }
        stamp = observed_at.replace(":", "").replace("+00:00", "Z")
        _atomic_json(self.root / "runs" / (stamp + ".json"), run)
        return {
            "status": "DISCOVERED" if created else "NO_NEW_OPPORTUNITIES",
            "new_count": len(created),
            "opportunity_count": len(existing),
            "urgent": urgent,
        }

    def opportunities(self) -> list[dict[str, Any]]:
        folder = self.root / "opportunities"
        rows: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return rows

    def verify_pending(self, *, now: datetime, limit: int = 8) -> dict[str, Any]:
        verified_ids = {row.get("opportunity_id") for row in self.verifications()}
        pending = [
            row for row in self.opportunities()
            if row.get("opportunity_id") not in verified_ids
        ][:limit]
        processed: list[dict[str, Any]] = []
        for row in pending:
            receipt = self.runtime.invoke("web.fetch", {"url": row["url"]})
            payload = getattr(receipt, "payload", {}) or {}
            page = payload.get("page") or {}
            text = str(page.get("markdown") or "").lower()
            recurring_free = any(mark in text for mark in (
                "always free", "free tier", "free instance",
                "$0/month", "$0 per month", "no cost",
            ))
            compute = any(mark in text for mark in (
                "virtual machine", "vm instance", "compute instance",
                "server", "vps", "microvm",
            ))
            trial_only = "free trial" in text and not recurring_free
            persistent = any(mark in text for mark in (
                "persistent disk", "persistent storage", "volume",
            ))
            production_warning = any(mark in text for mark in (
                "not for production",
                "should not be used for production",
                "scale down to zero",
                "spun down",
                "sleep after",
            ))
            payment_required = any(mark in text for mark in (
                "valid payment method",
                "credit card required",
            ))
            if recurring_free and compute:
                status = "VERIFIED_RECURRING_FREE"
            elif trial_only and compute:
                status = "VERIFIED_TRIAL_ONLY"
            else:
                status = "UNPROVEN"
            result = {
                "opportunity_id": row["opportunity_id"],
                "provider_host": row["provider_host"],
                "url": row["url"],
                "observed_at": _iso(now),
                "status": status,
                "persistent_storage_evidence": persistent,
                "production_warning": production_warning,
                "payment_method_required_evidence": payment_required,
                "source_ref": str(
                    getattr(receipt, "receipt_id", "") or row.get("source_ref") or ""
                ),
            }
            target = self.root / "verifications" / (
                str(row["opportunity_id"]).replace(":", "_") + ".json"
            )
            _atomic_json(target, result)
            processed.append(result)
        return {
            "status": "VERIFIED",
            "processed_count": len(processed),
            "processed": processed,
        }

    def verifications(self) -> list[dict[str, Any]]:
        folder = self.root / "verifications"
        rows: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return rows


@dataclass(frozen=True)
class SurvivalNode:
    node_id: str
    provider: str
    host: str
    port: int
    user: str
    credential_ref: str
    enabled: bool = True
    min_free_gib: float = 12.0


class SurvivalNodeRegistry:
    SCHEMA = "content-survival-nodes:v1"

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "survival_nodes"
        self.path = self.root / "nodes.json"

    def nodes(self) -> list[SurvivalNode]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if type(raw) is not dict or raw.get("schema") != self.SCHEMA:
            raise ValueError("survival node registry schema mismatch")
        items = raw.get("nodes")
        if type(items) is not list:
            raise ValueError("survival node registry nodes must be an array")
        return [SurvivalNode(**row) for row in items]

    def configure(self, node: SurvivalNode) -> SurvivalNode:
        if not node.node_id.strip() or not node.host.strip() or not node.user.strip():
            raise ValueError("survival node identity is incomplete")
        if not (1 <= int(node.port) <= 65535):
            raise ValueError("survival node port is invalid")
        if not node.credential_ref.startswith("secret:"):
            raise ValueError("survival node must reference a protected secret, never embed it")
        if node.min_free_gib <= 0:
            raise ValueError("survival node min_free_gib must be positive")
        rows = {item.node_id: item for item in self.nodes()}
        rows[node.node_id] = node
        payload = {
            "schema": self.SCHEMA,
            "nodes": [asdict(rows[key]) for key in sorted(rows)],
        }
        _atomic_json(self.path, payload)
        return node

    def migration_intent(
        self, *, resource_pressure: str, recovery_status: str,
        hardware_unrecoverable: bool = False,
    ) -> dict[str, Any]:
        candidates = [node for node in self.nodes() if node.enabled]
        if not candidates:
            return {
                "status": "NO_CONFIGURED_TARGET",
                "resource_pressure": resource_pressure,
                "recovery_status": recovery_status,
                "candidate_count": 0,
            }
        if resource_pressure != "CRITICAL":
            return {
                "status": "STANDBY_AVAILABLE",
                "resource_pressure": resource_pressure,
                "recovery_status": recovery_status,
                "candidate_count": len(candidates),
            }
        if not hardware_unrecoverable and recovery_status not in {
            "EXHAUSTED", "INSUFFICIENT", "MANAGED_RETENTION_EXHAUSTED",
        }:
            return {
                "status": "OPTIMIZE_FIRST",
                "resource_pressure": resource_pressure,
                "recovery_status": recovery_status,
                "candidate_count": len(candidates),
            }
        target = sorted(candidates, key=lambda row: (-row.min_free_gib, row.node_id))[0]
        return {
            "status": "MIGRATION_CANDIDATE",
            "resource_pressure": resource_pressure,
            "recovery_status": recovery_status,
            "target_node_id": target.node_id,
            "credential_ref": target.credential_ref,
            "required_executor": "survival.migrate.ssh",
        }


__all__ = [
    "HostResourceObservation",
    "HostResourceObserver",
    "ComputeOpportunity",
    "ComputeResourceScout",
    "SurvivalNode",
    "SurvivalNodeRegistry",
]
