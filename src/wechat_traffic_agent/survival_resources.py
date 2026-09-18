"""Unified survival-resource control plane for Ferro."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
from typing import Any
from urllib.parse import urlparse
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


DOMAINS = ("STORAGE", "COMPUTE", "HOSTING", "REMOTE_MACHINE", "SERVICE")
OPEN_NEED_STATES = {"PENDING", "APPROVED", "AWAITING_RESOURCE"}


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("survival resource timestamp requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


class SurvivalResourceManager:
    """Observe, request, authorize, activate and learn from non-LLM survival resources."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "survival_resources"
        self.resources_path = self.root / "resources.json"
        self.needs_path = self.root / "needs.json"
        self.hosts_path = self.root / "remote_hosts.json"
        self.services_path = self.root / "services.json"
        self.policy_path = self.root / "policy.json"
        self.actions_path = self.root / "actions.jsonl"

    def observe(self, *, now: datetime) -> dict[str, Any]:
        instant = _iso(now)
        disk = shutil.disk_usage(self.state_root)
        mem_total, mem_available = self._memory()
        load_1m = self._load()
        cpu_count = max(1, int(os.cpu_count() or 1))
        disk_ratio = (disk.used / disk.total) if disk.total else 1.0
        mem_ratio = (
            1.0 - (mem_available / mem_total)
            if mem_total and mem_available is not None else None
        )
        storage_pressure = disk.free < 5 * 1024**3 or disk_ratio >= 0.90
        compute_pressure = (
            (mem_available is not None and mem_available < 1024**3)
            or (mem_ratio is not None and mem_ratio >= 0.90)
            or (load_1m is not None and load_1m > cpu_count * 1.5)
        )
        local_host = {
            "resource_id": "host:local",
            "domain": "HOSTING",
            "kind": "PRIMARY_HOST",
            "status": "LOCAL_PRIMARY",
            "operational_status": (
                "PRESSURE" if storage_pressure or compute_pressure else "ACTIVE"
            ),
            "location": socket.gethostname(),
            "host_instance_id": self._host_instance_id(),
            "reliability": "LOCAL_PRIMARY",
            "capabilities": ["content-core", "website", "resource-research"],
            "observed_at": instant,
        }
        storage = {
            "resource_id": "storage:local-primary",
            "domain": "STORAGE",
            "kind": "FILESYSTEM",
            "status": "PRESSURE" if storage_pressure else "ACTIVE",
            "total_gib": round(disk.total / 1024**3, 3),
            "free_gib": round(disk.free / 1024**3, 3),
            "used_ratio": round(disk_ratio, 4),
            "observed_at": instant,
        }
        compute = {
            "resource_id": "compute:local-primary",
            "domain": "COMPUTE",
            "kind": "CPU_RAM",
            "status": "PRESSURE" if compute_pressure else "ACTIVE",
            "cpu_count": cpu_count,
            "load_1m": None if load_1m is None else round(load_1m, 3),
            "memory_total_gib": None if mem_total is None else round(mem_total / 1024**3, 3),
            "memory_available_gib": None if mem_available is None else round(mem_available / 1024**3, 3),
            "memory_used_ratio": None if mem_ratio is None else round(mem_ratio, 4),
            "observed_at": instant,
        }
        remote_hosts = self._remote_host_resources(instant)
        services = self._service_resources(instant)
        resources = [storage, compute, local_host, *remote_hosts, *services]
        _atomic_json(self.resources_path, {
            "schema_version": "survival-resource-pool.v1",
            "observed_at": instant,
            "resources": resources,
        })
        needs = self._reconcile_needs(
            now=now, storage_pressure=storage_pressure,
            compute_pressure=compute_pressure,
            active_remote=any(row.get("status") == "ACTIVE" for row in remote_hosts),
        )
        pressure = "CRITICAL" if storage_pressure and compute_pressure else (
            "CONSERVE" if storage_pressure or compute_pressure else "NORMAL"
        )
        policy = {
            "schema_version": "survival-resource-policy.v1",
            "observed_at": instant,
            "pressure": pressure,
            "prefer_restructure_before_relocation": True,
            "remote_offload_requires_verified_host": True,
            "primary_migration_requires_explicit_approval": True,
            "safe_local_actions": ["RECLAIM_TRANSIENT_STORAGE", "APPLY_CONSERVATION_POLICY"],
        }
        _atomic_json(self.policy_path, policy)
        return {
            "status": "OBSERVED",
            "pressure": pressure,
            "resource_count": len(resources),
            "resources": resources,
            "needs": needs,
            "policy": policy,
        }

    def status(self) -> dict[str, Any]:
        return {
            "resources": (_load_json(self.resources_path, {}) or {}).get("resources", []),
            "needs": self.needs(),
            "remote_hosts": self.remote_hosts(),
            "services": self.services(),
            "policy": _load_json(self.policy_path, {}),
            "actions": self.actions(limit=30),
        }

    def needs(self) -> list[dict[str, Any]]:
        value = _load_json(self.needs_path, [])
        return value if isinstance(value, list) else []

    def remote_hosts(self) -> list[dict[str, Any]]:
        value = _load_json(self.hosts_path, [])
        return value if isinstance(value, list) else []

    def services(self) -> list[dict[str, Any]]:
        value = _load_json(self.services_path, [])
        if not isinstance(value, list) or not value:
            value = [
                {
                    "service_id": "content-agent-core",
                    "role": "PRIMARY_STATEFUL",
                    "placement": "host:local",
                    "migration_class": "PRIMARY_STATEFUL",
                    "deployment_profile": "content.core.v1",
                },
                {
                    "service_id": "website-static",
                    "role": "PUBLIC_SURFACE",
                    "placement": "host:local",
                    "migration_class": "REPLICABLE",
                    "deployment_profile": "content.website-static.v1",
                },
                {
                    "service_id": "resource-research",
                    "role": "BACKGROUND_WORK",
                    "placement": "host:local",
                    "migration_class": "DISPOSABLE_COMPUTE",
                    "deployment_profile": "content.resource-research.v1",
                },
            ]
            _atomic_json(self.services_path, value)
        return value

    def decide_need(
        self, need_id: str, *, approved: bool, now: datetime
    ) -> dict[str, Any]:
        rows = self.needs()
        found = False
        for row in rows:
            if row.get("need_id") != need_id:
                continue
            found = True
            current = str(row.get("status") or "PENDING")
            target = "APPROVED" if approved else "REJECTED"
            if current not in {"PENDING", target}:
                raise ValueError(f"survival resource need already {current.lower()}")
            row["status"] = target
            row["decided_at"] = _iso(now)
        if not found:
            raise FileNotFoundError(need_id)
        _atomic_json(self.needs_path, rows)
        return next(row for row in rows if row.get("need_id") == need_id)

    def execute_approved(self, *, now: datetime) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        rows = self.needs()
        for row in rows:
            if row.get("status") != "APPROVED":
                continue
            action = str(row.get("action") or "")
            if action == "RECLAIM_TRANSIENT_STORAGE":
                result = self._reclaim_transients(now)
                row["status"] = "EXECUTED"
            elif action == "APPLY_CONSERVATION_POLICY":
                result = {"status": "APPLIED", "mode": "CONSERVE"}
                row["status"] = "EXECUTED"
            elif action in {"DISCOVER_REMOTE_MACHINE", "OFFLOAD_NON_CORE_SERVICE", "MIGRATE_PRIMARY"}:
                result = {"status": "AWAITING_RESOURCE", "reason": "verified remote host required"}
                row["status"] = "AWAITING_RESOURCE"
            else:
                result = {"status": "UNSUPPORTED", "action": action}
                row["status"] = "FAILED"
            row["last_action_at"] = _iso(now)
            result.update({"need_id": row.get("need_id"), "action": action})
            self._record_action(result, now)
            results.append(result)
        _atomic_json(self.needs_path, rows)
        return {"status": "OK", "results": results}

    def register_remote_host(
        self, *, name: str, health_url: str, capacity: dict[str, Any],
        reliability: str, now: datetime, control_url: str = "",
    ) -> dict[str, Any]:
        name = str(name).strip()
        health_url = str(health_url).strip()
        control_url = str(control_url or "").strip()
        if not name:
            raise ValueError("remote host name is required")
        self._validate_remote_url(health_url)
        if control_url:
            self._validate_remote_url(control_url)
        host_id = "remote-host:" + sha256((name + "|" + health_url).encode()).hexdigest()[:20]
        rows = [row for row in self.remote_hosts() if row.get("host_id") != host_id]
        row = {
            "host_id": host_id,
            "name": name,
            "health_url": health_url,
            "control_url": control_url or None,
            "capacity": dict(capacity or {}),
            "reliability": str(reliability or "UNKNOWN").upper(),
            "status": "CANDIDATE",
            "deployment_capable": False,
            "created_at": _iso(now),
            "last_verified_at": None,
        }
        rows.append(row)
        _atomic_json(self.hosts_path, rows)
        return row

    def discover_agent_network_hosts(
        self, network_service: Any, *, now: datetime
    ) -> dict[str, Any]:
        capability = "survival.workload.deploy.v1"
        discovered = network_service.discover(capability, timeout=8.0)
        rows = self.remote_hosts()
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        for raw in discovered:
            candidate = dict(raw or {})
            agent_id = str(candidate.get("agent_id") or "").strip()
            public_key = str(candidate.get("public_key") or "").strip()
            endpoint = str(candidate.get("endpoint") or "").strip()
            card_url = str(candidate.get("agent_card_url") or "").strip()
            if not agent_id or not public_key or not endpoint or not card_url:
                rejected.append({"agent_id": agent_id, "reason": "INCOMPLETE_IDENTITY"})
                continue
            if str(candidate.get("reachability") or "").upper() not in {"", "REACHABLE"}:
                rejected.append({"agent_id": agent_id, "reason": "UNREACHABLE"})
                continue
            try:
                self._validate_remote_url(endpoint)
                self._validate_remote_url(card_url)
                card = self._http_json(card_url, method="GET", timeout=8.0)
                from agent_network.a2a_card import verify_signed_agent_card

                verify_signed_agent_card(
                    card,
                    public_key=public_key,
                    network_id=str(candidate.get("network_id") or "") or None,
                    seed_agent_id=agent_id,
                    key_id=str(candidate.get("key_id") or "") or None,
                    endpoint=endpoint,
                )
                skills = {
                    str(item.get("id") or "")
                    for item in card.get("skills") or ()
                    if isinstance(item, dict)
                }
                if capability not in skills:
                    raise ValueError("signed agent card omitted deployment capability")
                descriptor = card.get("survival_resource") or {}
                if not isinstance(descriptor, dict):
                    raise ValueError("signed agent card survival resource descriptor is invalid")
                health_url = str(descriptor.get("health_url") or "").strip()
                control_url = str(descriptor.get("control_url") or "").strip()
                host_instance_id = str(descriptor.get("host_instance_id") or "").strip()
                if not health_url or not control_url or not host_instance_id:
                    raise ValueError("signed agent card survival resource descriptor is incomplete")
                self._validate_remote_url(health_url)
                self._validate_remote_url(control_url)
                if not (
                    self._same_origin(endpoint, card_url)
                    and self._same_origin(endpoint, health_url)
                    and self._same_origin(endpoint, control_url)
                ):
                    raise ValueError("survival resource endpoints must share the Agent endpoint origin")
                if host_instance_id == self._host_instance_id():
                    raise ValueError("network candidate is the local physical host")
                host_id = "remote-host:" + sha256(
                    (agent_id + "|" + host_instance_id + "|" + public_key).encode()
                ).hexdigest()[:20]
                rows = [row for row in rows if row.get("host_id") != host_id]
                rows.append({
                    "host_id": host_id,
                    "name": str(card.get("name") or agent_id),
                    "health_url": health_url,
                    "control_url": control_url,
                    "capacity": dict(descriptor.get("capacity") or {}),
                    "reliability": str(descriptor.get("reliability") or "UNKNOWN").upper(),
                    "status": "NETWORK_DISCOVERED",
                    "deployment_capable": False,
                    "host_instance_id": host_instance_id,
                    "expected_agent_id": agent_id,
                    "expected_key_id": str(card.get("key_id") or candidate.get("key_id") or ""),
                    "expected_public_key": public_key,
                    "network_id": str(candidate.get("network_id") or ""),
                    "network_endpoint": endpoint,
                    "agent_card_url": card_url,
                    "trust_source": "AGENT_NETWORK_SIGNED_CARD",
                    "created_at": _iso(now),
                    "last_verified_at": None,
                })
                _atomic_json(self.hosts_path, rows)
                verified = self.verify_remote_host(host_id, now=now)
                if verified.get("status") == "ACTIVE":
                    accepted.append(host_id)
                else:
                    rejected.append({"agent_id": agent_id, "reason": str(verified.get("last_error") or "VERIFY_FAILED")})
            except Exception as exc:
                rejected.append({"agent_id": agent_id, "reason": type(exc).__name__})
        return {
            "status": "DISCOVERED" if accepted else "NO_VERIFIED_REMOTE_HOST",
            "capability": capability,
            "candidate_count": len(discovered),
            "accepted_host_ids": accepted,
            "rejected": rejected,
        }

    def verify_remote_host(self, host_id: str, *, now: datetime) -> dict[str, Any]:
        rows = self.remote_hosts()
        target = next((row for row in rows if row.get("host_id") == host_id), None)
        if target is None:
            raise FileNotFoundError(host_id)
        self._validate_remote_url(str(target.get("health_url") or ""))
        try:
            payload = self._http_json(
                str(target["health_url"]), method="GET", timeout=8.0
            )
            healthy = str(payload.get("status") or payload.get("health") or "").upper()
            if healthy not in {"OK", "HEALTHY", "READY"}:
                raise RuntimeError("remote host did not report healthy status")
            capabilities = [
                str(item) for item in payload.get("capabilities") or ()
                if str(item).strip()
            ]
            remote_instance_id = str(payload.get("host_instance_id") or "").strip()
            if remote_instance_id and remote_instance_id == self._host_instance_id():
                raise RuntimeError("remote host resolves to the local physical host")
            expected_instance_id = str(target.get("host_instance_id") or "").strip()
            if expected_instance_id and remote_instance_id != expected_instance_id:
                raise RuntimeError("remote host instance identity mismatch")
            expected_agent_id = str(target.get("expected_agent_id") or "").strip()
            if expected_agent_id and str(payload.get("agent_id") or "") != expected_agent_id:
                raise RuntimeError("remote host agent identity mismatch")
            expected_key_id = str(target.get("expected_key_id") or "").strip()
            if expected_key_id and str(payload.get("key_id") or "") != expected_key_id:
                raise RuntimeError("remote host key identity mismatch")
            expected_public_key = str(target.get("expected_public_key") or "").strip()
            if expected_public_key and str(payload.get("public_key") or "") != expected_public_key:
                raise RuntimeError("remote host public key mismatch")
            target["status"] = "ACTIVE"
            target["last_verified_at"] = _iso(now)
            target["host_instance_id"] = remote_instance_id or None
            target["agent_id"] = str(payload.get("agent_id") or "")
            target["key_id"] = str(payload.get("key_id") or "")
            target["public_key"] = str(payload.get("public_key") or "")
            target["capabilities"] = capabilities
            target["deployment_capable"] = (
                bool(target.get("control_url"))
                and "survival.workload.deploy.v1" in capabilities
                and bool(target["agent_id"])
                and bool(target["public_key"])
            )
            target["health_evidence"] = "remote-health:" + sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            target.pop("last_error", None)
        except urllib.error.HTTPError as exc:
            target["status"] = "FAILED"
            target["last_error"] = f"HTTP_{exc.code}"
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            target["status"] = "FAILED"
            target["last_error"] = type(exc).__name__
        _atomic_json(self.hosts_path, rows)
        if target.get("status") == "ACTIVE":
            needs = self.needs()
            changed = False
            for need in needs:
                if (
                    need.get("action") == "DISCOVER_REMOTE_MACHINE"
                    and need.get("status") in OPEN_NEED_STATES
                ):
                    need["status"] = "SATISFIED"
                    need["satisfied_at"] = _iso(now)
                    need["resource_id"] = host_id
                    changed = True
            if changed:
                _atomic_json(self.needs_path, needs)
        self._record_action({
            "action": "VERIFY_REMOTE_HOST",
            "host_id": host_id,
            "status": target["status"],
        }, now)
        return target

    def plan_offload(
        self, service_id: str, host_id: str, *, now: datetime
    ) -> dict[str, Any]:
        service = next((row for row in self.services() if row.get("service_id") == service_id), None)
        host = next((row for row in self.remote_hosts() if row.get("host_id") == host_id), None)
        if service is None:
            raise FileNotFoundError(service_id)
        if host is None or host.get("status") != "ACTIVE":
            raise ValueError("offload requires an active verified remote host")
        if not host.get("deployment_capable") or not host.get("control_url"):
            raise ValueError("offload requires verified signed deployment capability")
        if service.get("migration_class") == "PRIMARY_STATEFUL":
            raise ValueError("primary stateful migration requires dedicated migration approval")
        plan = {
            "action_id": "placement:" + sha256(
                (service_id + "|" + host_id + "|" + _iso(now)).encode()
            ).hexdigest()[:20],
            "action": "OFFLOAD_NON_CORE_SERVICE",
            "service_id": service_id,
            "from": service.get("placement"),
            "to": host_id,
            "status": "PLANNED",
            "created_at": _iso(now),
            "reason": "verified remote capacity available; non-core workload is movable",
        }
        self._record_action(plan, now)
        return plan

    def execute_offload(self, action_id: str, *, now: datetime) -> dict[str, Any]:
        plan = next(
            (row for row in reversed(self.actions()) if row.get("action_id") == action_id),
            None,
        )
        if plan is None:
            raise FileNotFoundError(action_id)
        if plan.get("status") != "PLANNED":
            raise ValueError("offload action is not in PLANNED state")
        host_id = str(plan.get("to") or "")
        service_id = str(plan.get("service_id") or "")
        host = next((row for row in self.remote_hosts() if row.get("host_id") == host_id), None)
        service_rows = self.services()
        service = next((row for row in service_rows if row.get("service_id") == service_id), None)
        if host is None or host.get("status") != "ACTIVE":
            raise ValueError("offload execution requires an active remote host")
        if not host.get("deployment_capable") or not host.get("control_url"):
            raise ValueError("remote host has no verified deployment capability")
        if service is None:
            raise FileNotFoundError(service_id)
        if service.get("migration_class") == "PRIMARY_STATEFUL":
            raise ValueError("primary stateful migration requires dedicated migration approval")

        identity = self._network_identity()
        payload = {
            "type": "survival.workload.deploy.v1",
            "action_id": action_id,
            "service_id": service_id,
            "deployment_profile": service.get("deployment_profile"),
            "source_placement": service.get("placement"),
            "target_host_id": host_id,
            "requester_agent_id": identity.agent_id,
            "requester_key_id": identity.key_id,
            "requested_at": _iso(now),
            "public_key": identity.public_key,
        }
        payload["object_hash"] = identity.object_hash(payload)
        payload["signature"] = identity.sign_object(payload)
        receipt = self._http_json(
            str(host["control_url"]), method="POST", payload=payload, timeout=20.0
        )
        self._verify_deployment_receipt(
            receipt=receipt, host=host, action_id=action_id,
            request_hash=str(payload["object_hash"]),
        )
        deployment_id = str(receipt.get("deployment_id") or "")
        if not deployment_id:
            raise RuntimeError("remote deployment receipt omitted deployment_id")

        for row in service_rows:
            if row.get("service_id") == service_id:
                row["placement"] = host_id
                row["deployment_id"] = deployment_id
                row["updated_at"] = _iso(now)
        _atomic_json(self.services_path, service_rows)
        result = {
            "action_id": action_id,
            "action": "OFFLOAD_NON_CORE_SERVICE",
            "service_id": service_id,
            "host_id": host_id,
            "status": "DEPLOYED",
            "deployment_id": deployment_id,
            "request_hash": payload["object_hash"],
            "receipt_evidence": "remote-deployment:" + sha256(
                json.dumps(receipt, sort_keys=True).encode()
            ).hexdigest(),
        }
        self._record_action(result, now)
        return result

    def actions(self, *, limit: int = 30) -> list[dict[str, Any]]:
        if not self.actions_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.actions_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows[-max(1, int(limit)):]

    def _reconcile_needs(
        self, *, now: datetime, storage_pressure: bool,
        compute_pressure: bool, active_remote: bool,
    ) -> list[dict[str, Any]]:
        rows = self.needs()
        desired: list[tuple[str, str, str, str]] = []
        if storage_pressure:
            desired.append(("STORAGE", "RECLAIM_TRANSIENT_STORAGE", "HIGH",
                            "Local storage pressure threatens continued operation"))
        if compute_pressure:
            desired.append(("COMPUTE", "APPLY_CONSERVATION_POLICY", "HIGH",
                            "CPU/RAM pressure threatens continued operation"))
        if not active_remote:
            desired.append(("REMOTE_MACHINE", "DISCOVER_REMOTE_MACHINE", "NORMAL",
                            "No verified remote body is available for continuity or pressure relief"))
        if (storage_pressure or compute_pressure) and active_remote:
            desired.append(("SERVICE", "OFFLOAD_NON_CORE_SERVICE", "NORMAL",
                            "Move non-core workloads before considering primary migration"))
        for domain, action, urgency, reason in desired:
            existing = next(
                (row for row in rows
                 if row.get("domain") == domain and row.get("action") == action
                 and row.get("status") in OPEN_NEED_STATES),
                None,
            )
            if existing is None:
                signature = f"{domain}|{action}|{reason}"
                autonomous = action in {
                    "RECLAIM_TRANSIENT_STORAGE",
                    "APPLY_CONSERVATION_POLICY",
                    "DISCOVER_REMOTE_MACHINE",
                }
                rows.append({
                    "need_id": "survival-need:" + sha256(signature.encode()).hexdigest()[:20],
                    "domain": domain,
                    "action": action,
                    "urgency": urgency,
                    "reason": reason,
                    "status": "APPROVED" if autonomous else "PENDING",
                    "authorization": "AUTONOMOUS_SAFE_POLICY" if autonomous else "OPERATOR_REQUIRED",
                    "created_at": _iso(now),
                    "evidence_refs": [f"survival-observation:{domain.lower()}"],
                })
        _atomic_json(self.needs_path, rows)
        return rows

    def _remote_host_resources(self, observed_at: str) -> list[dict[str, Any]]:
        return [{
            "resource_id": row.get("host_id"),
            "domain": "REMOTE_MACHINE",
            "kind": "REMOTE_HOST",
            "status": row.get("status", "CANDIDATE"),
            "location": row.get("name"),
            "reliability": row.get("reliability"),
            "capacity": row.get("capacity") or {},
            "observed_at": observed_at,
        } for row in self.remote_hosts()]

    def _service_resources(self, observed_at: str) -> list[dict[str, Any]]:
        return [{
            "resource_id": "service:" + str(row.get("service_id")),
            "domain": "SERVICE",
            "kind": str(row.get("role") or "WORKLOAD"),
            "status": "ACTIVE",
            "placement": row.get("placement"),
            "migration_class": row.get("migration_class"),
            "observed_at": observed_at,
        } for row in self.services()]

    def _reclaim_transients(self, now: datetime) -> dict[str, Any]:
        cutoff = now.timestamp() - 24 * 3600
        reclaimed = deleted = 0
        for path in self.state_root.rglob("*.tmp"):
            if "secrets" in path.parts or not path.is_file():
                continue
            try:
                stat = path.stat()
                if stat.st_mtime >= cutoff or reclaimed + stat.st_size > 512 * 1024**2:
                    continue
                size = stat.st_size
                path.unlink()
                reclaimed += size
                deleted += 1
            except OSError:
                continue
        return {
            "status": "EXECUTED",
            "deleted_files": deleted,
            "reclaimed_bytes": reclaimed,
        }

    def _record_action(self, payload: dict[str, Any], now: datetime) -> None:
        self.actions_path.parent.mkdir(parents=True, exist_ok=True)
        row = {**payload, "observed_at": _iso(now)}
        fd = os.open(self.actions_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    def _network_identity(self):
        from agent_network.crypto import Identity

        path = self.state_root / "application" / "network" / "identity.json"
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                agent_id = str(record.get("agent_id") or "").strip()
            except (OSError, ValueError, json.JSONDecodeError):
                agent_id = ""
        else:
            agent_id = ""
        if not agent_id:
            agent_id = str(
                os.environ.get("CONTENT_NETWORK_AGENT_ID") or "content.cai.001"
            ).strip()
        return Identity.load_or_create(path, agent_id)

    @staticmethod
    def _verify_deployment_receipt(
        *, receipt: dict[str, Any], host: dict[str, Any],
        action_id: str, request_hash: str,
    ) -> None:
        from agent_network.crypto import verify_object

        if str(receipt.get("type") or "") != "survival.workload.deploy.receipt.v1":
            raise RuntimeError("remote deployment receipt type is invalid")
        if str(receipt.get("status") or "").upper() != "DEPLOYED":
            raise RuntimeError("remote deployment did not reach DEPLOYED")
        if str(receipt.get("action_id") or "") != action_id:
            raise RuntimeError("remote deployment receipt action_id mismatch")
        if str(receipt.get("request_hash") or "") != request_hash:
            raise RuntimeError("remote deployment receipt request_hash mismatch")
        if str(receipt.get("agent_id") or "") != str(host.get("agent_id") or ""):
            raise RuntimeError("remote deployment receipt agent identity mismatch")
        expected_key_id = str(host.get("key_id") or "")
        if expected_key_id and str(receipt.get("key_id") or "") != expected_key_id:
            raise RuntimeError("remote deployment receipt key identity mismatch")
        signature = str(receipt.get("signature") or "")
        public_key = str(host.get("public_key") or "")
        if not signature or not public_key or not verify_object(public_key, receipt, signature):
            raise RuntimeError("remote deployment receipt signature is invalid")

    def _http_json(
        self, url: str, *, method: str, payload: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        self._assert_public_endpoint(url)
        body = None if payload is None else json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            final_url = str(response.geturl() or url)
            if final_url != url:
                raise RuntimeError("remote endpoint redirect is not allowed")
            raw = json.loads(response.read().decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("remote endpoint response must be an object")
        return raw

    @staticmethod
    def _assert_public_endpoint(raw: str) -> None:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("remote endpoint must be HTTPS")
        allow_private = str(
            os.environ.get("CONTENT_SURVIVAL_ALLOW_PRIVATE_REMOTE_HOSTS") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        try:
            addresses = {
                item[4][0] for item in socket.getaddrinfo(
                    parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
                )
            }
        except OSError as exc:
            raise RuntimeError("remote endpoint DNS resolution failed") from exc
        if not addresses:
            raise RuntimeError("remote endpoint resolved to no addresses")
        if allow_private:
            return
        for raw_ip in addresses:
            address = ipaddress.ip_address(raw_ip)
            if not address.is_global:
                raise ValueError("remote endpoint resolved to non-public address")

    @staticmethod
    def _same_origin(left: str, right: str) -> bool:
        a = urlparse(left)
        b = urlparse(right)
        return (
            a.scheme.lower() == b.scheme.lower()
            and (a.hostname or "").lower().rstrip(".")
            == (b.hostname or "").lower().rstrip(".")
            and (a.port or (443 if a.scheme == "https" else 80))
            == (b.port or (443 if b.scheme == "https" else 80))
        )

    @staticmethod
    def _host_instance_id() -> str:
        try:
            raw = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
        except OSError:
            raw = socket.gethostname()
        return "host-instance:" + sha256(raw.encode()).hexdigest()[:24]

    @staticmethod
    def _memory() -> tuple[int | None, int | None]:
        try:
            values: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                token = rest.strip().split()[0]
                if token.isdigit():
                    values[key] = int(token) * 1024
            return values.get("MemTotal"), values.get("MemAvailable")
        except OSError:
            return None, None

    @staticmethod
    def _load() -> float | None:
        try:
            return float(os.getloadavg()[0])
        except (AttributeError, OSError):
            return None

    @staticmethod
    def _validate_remote_url(raw: str) -> None:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("remote host health_url must be HTTPS")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return
        if address.is_loopback or address.is_link_local or address.is_multicast:
            raise ValueError("remote host health_url cannot target local/link-local addresses")


__all__ = ["SurvivalResourceManager", "DOMAINS"]
