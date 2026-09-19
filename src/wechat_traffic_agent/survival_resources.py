"""Unified survival-resource control plane for Ferro."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import base64
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import tarfile
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
        self._refresh_remote_hosts(now=now)
        self._refresh_service_replicas(now=now)
        remote_hosts = self._remote_host_resources(instant)
        services = self._service_resources(instant)
        resources = [storage, compute, local_host, *remote_hosts, *services]
        _atomic_json(self.resources_path, {
            "schema_version": "survival-resource-pool.v1",
            "observed_at": instant,
            "resources": resources,
        })
        active_remote = any(row.get("status") == "ACTIVE" for row in remote_hosts)
        usable_remote = any(
            row.get("status") == "ACTIVE"
            and row.get("receiver_status") == "VERIFIED"
            and row.get("deployment_capable")
            for row in self.remote_hosts()
        )
        degraded_replica = any(
            replica.get("status") != "ACTIVE"
            for service in self.services()
            for replica in service.get("replicas") or ()
        )
        needs = self._reconcile_needs(
            now=now,
            storage_pressure=storage_pressure,
            compute_pressure=compute_pressure,
            active_remote=active_remote,
            usable_remote=usable_remote,
            degraded_replica=degraded_replica,
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
                    "primary_placement": "host:local",
                    "replicas": [],
                    "migration_class": "PRIMARY_STATEFUL",
                    "deployment_profile": "content.core.v1",
                },
                {
                    "service_id": "website-static",
                    "role": "PUBLIC_SURFACE",
                    "placement": "host:local",
                    "primary_placement": "host:local",
                    "replicas": [],
                    "migration_class": "REPLICABLE",
                    "deployment_profile": "content.website-static.v1",
                },
                {
                    "service_id": "resource-research",
                    "role": "BACKGROUND_WORK",
                    "placement": "host:local",
                    "primary_placement": "host:local",
                    "replicas": [],
                    "migration_class": "DISPOSABLE_COMPUTE",
                    "deployment_profile": "content.resource-research.v1",
                },
            ]
            _atomic_json(self.services_path, value)
            return value

        changed = False
        for row in value:
            if not isinstance(row, dict):
                continue
            migration_class = str(row.get("migration_class") or "")
            replicas = row.get("replicas")
            if not isinstance(replicas, list):
                replicas = []
                row["replicas"] = replicas
                changed = True
            primary = str(row.get("primary_placement") or "").strip()
            placement = str(row.get("placement") or "host:local").strip() or "host:local"
            if migration_class == "REPLICABLE":
                if not primary:
                    primary = "host:local"
                    row["primary_placement"] = primary
                    changed = True
                if placement != primary and row.get("deployment_id"):
                    if not any(
                        str(item.get("host_id") or "") == placement
                        for item in replicas
                        if isinstance(item, dict)
                    ):
                        replicas.append({
                            "host_id": placement,
                            "status": "ACTIVE",
                            "deployment_id": row.get("deployment_id"),
                            "deployment_url": row.get("deployment_url"),
                            "artifact_sha256": row.get("artifact_sha256"),
                            "updated_at": row.get("updated_at"),
                        })
                    for field in (
                        "deployment_id", "deployment_url", "artifact_sha256", "updated_at"
                    ):
                        row.pop(field, None)
                    row["placement"] = primary
                    changed = True
                elif placement != primary:
                    row["placement"] = primary
                    changed = True
            else:
                if not primary:
                    row["primary_placement"] = placement
                    changed = True
        if changed:
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
            elif action == "RESTORE_SERVICE_REPLICA":
                result = self._restore_degraded_replica(now=now)
                row["status"] = (
                    "EXECUTED" if result.get("status") == "DEPLOYED" else "AWAITING_RESOURCE"
                )
            elif action in {
                "DISCOVER_REMOTE_MACHINE",
                "RESTORE_REMOTE_RECEIVER",
                "OFFLOAD_NON_CORE_SERVICE",
                "MIGRATE_PRIMARY",
            }:
                result = {"status": "AWAITING_RESOURCE", "reason": "verified remote host or receiver required"}
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

    def bootstrap_ssh_host(
        self, *, host: str, port: int, username: str, password: str,
        name: str = "", now: datetime,
    ) -> dict[str, Any]:
        from .survival_ssh import SSHHostBootstrapper

        result = SSHHostBootstrapper(self.state_root).bootstrap(
            host=host,
            port=port,
            username=username,
            password=password,
            label=name or host,
        )
        audit = dict(result.audit)
        host_id = "remote-host:" + sha256(
            (
                result.host_instance_id
                + "|"
                + result.host_key_sha256
                + "|"
                + result.username
            ).encode()
        ).hexdigest()[:20]
        key_ref = Path(result.key_path).parent.name
        rows = [
            row for row in self.remote_hosts()
            if row.get("host_id") != host_id
            and row.get("host_instance_id") != result.host_instance_id
        ]
        row = {
            "host_id": host_id,
            "name": str(name or audit.get("hostname") or result.host),
            "ssh_host": result.host,
            "ssh_port": result.port,
            "ssh_username": result.username,
            "ssh_host_key_sha256": result.host_key_sha256,
            "ssh_key_ref": key_ref,
            "management_transport": "SSH_KEY",
            "status": "ACTIVE",
            "deployment_capable": False,
            "receiver_status": "NOT_PROVISIONED",
            "host_instance_id": result.host_instance_id,
            "capacity": {
                "cpu_count": audit.get("cpu_count"),
                "memory_total_gib": audit.get("memory_total_gib"),
                "memory_available_gib": audit.get("memory_available_gib"),
                "disk_total_gib": audit.get("disk_total_gib"),
                "disk_free_gib": audit.get("disk_free_gib"),
            },
            "platform": audit.get("platform"),
            "machine": audit.get("machine"),
            "reliability": "OPERATOR_MANAGED",
            "trust_source": "OPERATOR_SSH_BOOTSTRAP",
            "created_at": _iso(now),
            "last_verified_at": _iso(now),
        }
        rows.append(row)
        _atomic_json(self.hosts_path, rows)
        self._satisfy_remote_need(host_id=host_id, now=now)
        self._record_action({
            "action": "BOOTSTRAP_REMOTE_HOST",
            "host_id": host_id,
            "status": "ACTIVE",
            "management_transport": "SSH_KEY",
            "password_retained": False,
            "host_key_sha256": result.host_key_sha256,
        }, now)
        return row

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

    def bind_signed_receiver(
        self, host_id: str, *, origin: str, now: datetime
    ) -> dict[str, Any]:
        rows = self.remote_hosts()
        target = next((row for row in rows if row.get("host_id") == host_id), None)
        if target is None:
            raise FileNotFoundError(host_id)
        if target.get("management_transport") != "SSH_KEY":
            raise ValueError("signed receiver binding requires an SSH-managed host")
        base = str(origin or "").strip().rstrip("/")
        self._validate_remote_url(base)
        card_url = base + "/.well-known/agent-card.json"
        health_url = base + "/application/survival/host"
        control_url = base + "/application/survival/workloads"
        card = self._http_json(card_url, method="GET", timeout=8.0)
        public_key = str(card.get("public_key") or "").strip()
        agent_id = str(card.get("agent_id") or "").strip()
        key_id = str(card.get("key_id") or "").strip()
        endpoint = str(card.get("url") or "").strip()
        if not public_key or not agent_id or not key_id or not endpoint:
            raise ValueError("signed receiver Agent Card is missing identity fields")
        from agent_network.a2a_card import verify_signed_agent_card

        verify_signed_agent_card(
            card,
            public_key=public_key,
            network_id=str(card.get("network_id") or "") or None,
            seed_agent_id=agent_id,
            key_id=key_id,
            endpoint=endpoint,
        )
        descriptor = card.get("survival_resource") or {}
        if not isinstance(descriptor, dict):
            raise ValueError("signed receiver Agent Card has no survival resource descriptor")
        if str(descriptor.get("health_url") or "").rstrip("/") != health_url:
            raise ValueError("signed receiver health URL does not match expected origin")
        if str(descriptor.get("control_url") or "").rstrip("/") != control_url:
            raise ValueError("signed receiver control URL does not match expected origin")
        remote_instance_id = str(descriptor.get("host_instance_id") or "").strip()
        expected_instance_id = str(target.get("host_instance_id") or "").strip()
        if not remote_instance_id or remote_instance_id != expected_instance_id:
            raise ValueError("signed receiver physical host identity mismatch")
        skills = {
            str(item.get("id") or "")
            for item in card.get("skills") or ()
            if isinstance(item, dict)
        }
        if "survival.workload.deploy.v1" not in skills:
            raise ValueError("signed receiver does not advertise workload deployment")
        target.update({
            "health_url": health_url,
            "control_url": control_url,
            "agent_card_url": card_url,
            "network_endpoint": endpoint,
            "expected_agent_id": agent_id,
            "expected_key_id": key_id,
            "expected_public_key": public_key,
            "trust_source": "OPERATOR_SSH_BOOTSTRAP+SIGNED_AGENT_CARD",
            "receiver_status": "DISCOVERED",
        })
        _atomic_json(self.hosts_path, rows)
        verified = self.verify_remote_host(host_id, now=now)
        if verified.get("status") != "ACTIVE" or not verified.get("deployment_capable"):
            raise RuntimeError("signed receiver failed workload-capability verification")
        rows = self.remote_hosts()
        current = next(row for row in rows if row.get("host_id") == host_id)
        current["receiver_status"] = "VERIFIED"
        current["receiver_origin"] = base
        _atomic_json(self.hosts_path, rows)
        self._record_action({
            "action": "BIND_SIGNED_RECEIVER",
            "host_id": host_id,
            "status": "VERIFIED",
            "receiver_origin": base,
            "agent_id": current.get("agent_id"),
            "key_id": current.get("key_id"),
        }, now)
        return current

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
            target["deployment_profiles"] = [
                str(item) for item in payload.get("deployment_profiles") or ()
                if str(item).strip()
            ]
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
            self._satisfy_remote_need(host_id=host_id, now=now)
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
        profile = str(service.get("deployment_profile") or "")
        if profile not in set(host.get("deployment_profiles") or ()):
            raise ValueError("remote host does not support this deployment profile")
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
            "artifact": self._deployment_artifact(service),
        }
        payload["object_hash"] = identity.object_hash(payload)
        payload["signature"] = identity.sign_object(payload)
        receipt = self._http_json(
            str(host["control_url"]), method="POST", payload=payload, timeout=60.0
        )
        self._verify_deployment_receipt(
            receipt=receipt, host=host, action_id=action_id,
            request_hash=str(payload["object_hash"]),
        )
        deployment_id = str(receipt.get("deployment_id") or "")
        if not deployment_id:
            raise RuntimeError("remote deployment receipt omitted deployment_id")
        deployment_url = str(receipt.get("deployment_url") or "").strip()
        if str(service.get("deployment_profile") or "") == "content.website-static.v1":
            self._verify_static_deployment(
                host=host,
                deployment_url=deployment_url,
                artifact=dict(payload["artifact"]),
            )

        placement_mode = "MOVED"
        for row in service_rows:
            if row.get("service_id") != service_id:
                continue
            if str(row.get("migration_class") or "") == "REPLICABLE":
                placement_mode = "REPLICA"
                row["primary_placement"] = str(
                    row.get("primary_placement") or row.get("placement") or "host:local"
                )
                row["placement"] = row["primary_placement"]
                replicas = [
                    item for item in row.get("replicas") or []
                    if isinstance(item, dict) and str(item.get("host_id") or "") != host_id
                ]
                replicas.append({
                    "host_id": host_id,
                    "status": "ACTIVE",
                    "deployment_id": deployment_id,
                    "deployment_url": deployment_url or None,
                    "artifact_sha256": str(receipt.get("artifact_sha256") or ""),
                    "index_sha256": str(payload["artifact"].get("index_sha256") or ""),
                    "last_verified_at": _iso(now),
                    "updated_at": _iso(now),
                })
                row["replicas"] = replicas
            else:
                row["placement"] = host_id
                row["primary_placement"] = host_id
                row["deployment_id"] = deployment_id
                row["deployment_url"] = deployment_url or None
                row["artifact_sha256"] = str(receipt.get("artifact_sha256") or "")
                row["updated_at"] = _iso(now)
        _atomic_json(self.services_path, service_rows)
        result = {
            "action_id": action_id,
            "action": "OFFLOAD_NON_CORE_SERVICE",
            "placement_mode": placement_mode,
            "service_id": service_id,
            "host_id": host_id,
            "status": "DEPLOYED",
            "deployment_id": deployment_id,
            "deployment_url": deployment_url or None,
            "artifact_sha256": receipt.get("artifact_sha256"),
            "request_hash": payload["object_hash"],
            "receipt_evidence": "remote-deployment:" + sha256(
                json.dumps(receipt, sort_keys=True).encode()
            ).hexdigest(),
        }
        self._record_action(result, now)
        return result

    def _deployment_artifact(self, service: dict[str, Any]) -> dict[str, Any]:
        profile = str(service.get("deployment_profile") or "")
        if profile != "content.website-static.v1":
            raise ValueError("deployment artifact builder does not support this profile")
        raw_root = str(os.environ.get("CONTENT_WEB_SITE_ROOT") or "").strip()
        if not raw_root:
            raise RuntimeError("CONTENT_WEB_SITE_ROOT is required for website-static offload")
        root = Path(raw_root).resolve(strict=True)
        if not root.is_dir() or not (root / "index.html").is_file():
            raise RuntimeError("website-static source has no index.html")

        buffer = io.BytesIO()
        file_count = 0
        extracted_bytes = 0
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise RuntimeError("website-static source contains a symlink")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise RuntimeError("website-static source contains a non-file entry")
                relative = path.relative_to(root)
                stat = path.stat()
                file_count += 1
                extracted_bytes += int(stat.st_size)
                if file_count > 5000 or extracted_bytes > 64 * 1024 * 1024:
                    raise RuntimeError("website-static source exceeds receiver limits")
                info = tarfile.TarInfo(relative.as_posix())
                info.size = int(stat.st_size)
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
        raw = buffer.getvalue()
        if not raw or len(raw) > 16 * 1024 * 1024:
            raise RuntimeError("website-static archive exceeds receiver request limit")
        index_bytes = (root / "index.html").read_bytes()
        return {
            "media_type": "application/gzip",
            "encoding": "base64",
            "sha256": "sha256:" + sha256(raw).hexdigest(),
            "bytes": len(raw),
            "data": base64.b64encode(raw).decode("ascii"),
            "file_count": file_count,
            "index_sha256": "sha256:" + sha256(index_bytes).hexdigest(),
        }

    def _verify_static_deployment(
        self, *, host: dict[str, Any], deployment_url: str,
        artifact: dict[str, Any],
    ) -> None:
        if not deployment_url:
            raise RuntimeError("remote static deployment omitted deployment_url")
        control_url = str(host.get("control_url") or "")
        if not self._same_origin(control_url, deployment_url):
            raise RuntimeError("remote deployment URL origin mismatch")
        body = self._http_bytes(deployment_url, timeout=12.0, max_bytes=4 * 1024 * 1024)
        expected = str(artifact.get("index_sha256") or "")
        actual = "sha256:" + sha256(body).hexdigest()
        if not expected or actual != expected:
            raise RuntimeError("remote static index verification failed")

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

    def _refresh_remote_hosts(self, *, now: datetime) -> None:
        rows = self.remote_hosts()
        changed = False
        for row in rows:
            health_url = str(row.get("health_url") or "").strip()
            if not health_url:
                continue
            try:
                payload = self._http_json(health_url, method="GET", timeout=8.0)
                healthy = str(payload.get("status") or payload.get("health") or "").upper()
                if healthy not in {"OK", "HEALTHY", "READY"}:
                    raise RuntimeError("remote receiver health is not ready")
                remote_instance = str(payload.get("host_instance_id") or "").strip()
                expected_instance = str(row.get("host_instance_id") or "").strip()
                if expected_instance and remote_instance != expected_instance:
                    raise RuntimeError("remote receiver physical host identity changed")
                expected_agent = str(row.get("agent_id") or row.get("expected_agent_id") or "").strip()
                if expected_agent and str(payload.get("agent_id") or "") != expected_agent:
                    raise RuntimeError("remote receiver agent identity changed")
                expected_key = str(row.get("key_id") or row.get("expected_key_id") or "").strip()
                if expected_key and str(payload.get("key_id") or "") != expected_key:
                    raise RuntimeError("remote receiver key identity changed")
                expected_public = str(
                    row.get("public_key") or row.get("expected_public_key") or ""
                ).strip()
                if expected_public and str(payload.get("public_key") or "") != expected_public:
                    raise RuntimeError("remote receiver public key changed")
                capabilities = [
                    str(item) for item in payload.get("capabilities") or ()
                    if str(item).strip()
                ]
                profiles = [
                    str(item) for item in payload.get("deployment_profiles") or ()
                    if str(item).strip()
                ]
                row["status"] = "ACTIVE"
                row["receiver_status"] = "VERIFIED"
                row["deployment_capable"] = (
                    bool(row.get("control_url"))
                    and "survival.workload.deploy.v1" in capabilities
                    and bool(payload.get("agent_id"))
                    and bool(payload.get("public_key"))
                )
                row["capabilities"] = capabilities
                row["deployment_profiles"] = profiles
                if isinstance(payload.get("capacity"), dict):
                    row["capacity"] = dict(payload["capacity"])
                row["last_health_at"] = _iso(now)
                row["last_verified_at"] = _iso(now)
                row.pop("last_health_error", None)
                changed = True
            except Exception as exc:
                if row.get("receiver_status") not in {None, "NOT_PROVISIONED"}:
                    row["receiver_status"] = "DEGRADED"
                    row["deployment_capable"] = False
                    if row.get("management_transport") != "SSH_KEY":
                        row["status"] = "DEGRADED"
                    row["last_health_at"] = _iso(now)
                    row["last_health_error"] = type(exc).__name__
                    changed = True
        if changed:
            _atomic_json(self.hosts_path, rows)

    def _refresh_service_replicas(self, *, now: datetime) -> None:
        services = self.services()
        hosts = {
            str(row.get("host_id") or ""): row
            for row in self.remote_hosts()
            if isinstance(row, dict)
        }
        changed = False
        for service in services:
            replicas = service.get("replicas")
            if not isinstance(replicas, list):
                continue
            profile = str(service.get("deployment_profile") or "")
            for replica in replicas:
                if not isinstance(replica, dict):
                    continue
                host = hosts.get(str(replica.get("host_id") or ""))
                error = ""
                try:
                    if (
                        host is None
                        or host.get("status") != "ACTIVE"
                        or host.get("receiver_status") != "VERIFIED"
                        or not host.get("deployment_capable")
                    ):
                        raise RuntimeError("replica host receiver is unavailable")
                    deployment_url = str(replica.get("deployment_url") or "").strip()
                    if not deployment_url:
                        raise RuntimeError("replica has no deployment URL")
                    if profile == "content.website-static.v1":
                        expected = str(replica.get("index_sha256") or "").strip()
                        if not expected:
                            expected = self._local_static_index_sha256()
                            if expected:
                                replica["index_sha256"] = expected
                        body = self._http_bytes(
                            deployment_url, timeout=12.0, max_bytes=4 * 1024 * 1024
                        )
                        actual = "sha256:" + sha256(body).hexdigest()
                        if not expected or actual != expected:
                            raise RuntimeError("replica content hash mismatch")
                    replica["status"] = "ACTIVE"
                    replica["last_verified_at"] = _iso(now)
                    replica.pop("last_error", None)
                    changed = True
                except Exception as exc:
                    error = type(exc).__name__
                    if replica.get("status") != "DEGRADED" or replica.get("last_error") != error:
                        changed = True
                    replica["status"] = "DEGRADED"
                    replica["last_error"] = error
                    replica["last_verified_at"] = _iso(now)
        if changed:
            _atomic_json(self.services_path, services)

    def _local_static_index_sha256(self) -> str:
        raw_root = str(os.environ.get("CONTENT_WEB_SITE_ROOT") or "").strip()
        if not raw_root:
            return ""
        try:
            body = (Path(raw_root).resolve(strict=True) / "index.html").read_bytes()
        except OSError:
            return ""
        return "sha256:" + sha256(body).hexdigest()

    def _restore_degraded_replica(self, *, now: datetime) -> dict[str, Any]:
        hosts = {
            str(row.get("host_id") or ""): row
            for row in self.remote_hosts()
            if (
                row.get("status") == "ACTIVE"
                and row.get("receiver_status") == "VERIFIED"
                and row.get("deployment_capable")
            )
        }
        for service in self.services():
            if str(service.get("migration_class") or "") != "REPLICABLE":
                continue
            for replica in service.get("replicas") or ():
                if not isinstance(replica, dict) or replica.get("status") == "ACTIVE":
                    continue
                host_id = str(replica.get("host_id") or "")
                if host_id not in hosts:
                    continue
                try:
                    plan = self.plan_offload(
                        str(service.get("service_id") or ""), host_id, now=now
                    )
                    result = self.execute_offload(plan["action_id"], now=now)
                    result["repair_of_replica"] = host_id
                    return result
                except Exception as exc:
                    return {
                        "status": "FAILED",
                        "reason": "replica repair attempt failed",
                        "error_type": type(exc).__name__,
                        "repair_of_replica": host_id,
                    }
        return {
            "status": "AWAITING_RESOURCE",
            "reason": "no degraded replica has a verified deployment-capable host",
        }

    def _reconcile_needs(
        self, *, now: datetime, storage_pressure: bool,
        compute_pressure: bool, active_remote: bool,
        usable_remote: bool, degraded_replica: bool,
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
        elif not usable_remote:
            desired.append(("REMOTE_MACHINE", "RESTORE_REMOTE_RECEIVER", "HIGH",
                            "A remote body exists but its signed workload receiver is unavailable"))
        if degraded_replica:
            desired.append(("SERVICE", "RESTORE_SERVICE_REPLICA", "HIGH",
                            "A managed service replica failed its remote health or content verification"))
        if (storage_pressure or compute_pressure) and usable_remote:
            desired.append(("SERVICE", "OFFLOAD_NON_CORE_SERVICE", "NORMAL",
                            "Move non-core workloads before considering primary migration"))

        desired_keys = {(domain, action) for domain, action, _, _ in desired}
        managed_actions = {
            "DISCOVER_REMOTE_MACHINE",
            "RESTORE_REMOTE_RECEIVER",
            "RESTORE_SERVICE_REPLICA",
            "OFFLOAD_NON_CORE_SERVICE",
        }
        for row in rows:
            key = (str(row.get("domain") or ""), str(row.get("action") or ""))
            if (
                str(row.get("action") or "") in managed_actions
                and row.get("status") in OPEN_NEED_STATES
                and key not in desired_keys
            ):
                row["status"] = "SATISFIED"
                row["satisfied_at"] = _iso(now)

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
                    "RESTORE_SERVICE_REPLICA",
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

    def _satisfy_remote_need(self, *, host_id: str, now: datetime) -> None:
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

    def _remote_host_resources(self, observed_at: str) -> list[dict[str, Any]]:
        return [{
            "resource_id": row.get("host_id"),
            "domain": "REMOTE_MACHINE",
            "kind": "REMOTE_HOST",
            "status": row.get("status", "CANDIDATE"),
            "location": row.get("name"),
            "reliability": row.get("reliability"),
            "capacity": row.get("capacity") or {},
            "receiver_status": row.get("receiver_status"),
            "deployment_capable": bool(row.get("deployment_capable")),
            "deployment_profiles": list(row.get("deployment_profiles") or ()),
            "last_health_at": row.get("last_health_at"),
            "observed_at": observed_at,
        } for row in self.remote_hosts()]

    def _service_resources(self, observed_at: str) -> list[dict[str, Any]]:
        return [{
            "resource_id": "service:" + str(row.get("service_id")),
            "domain": "SERVICE",
            "kind": str(row.get("role") or "WORKLOAD"),
            "status": "DEGRADED" if any(
                isinstance(replica, dict) and replica.get("status") != "ACTIVE"
                for replica in row.get("replicas") or ()
            ) else "ACTIVE",
            "placement": row.get("placement"),
            "primary_placement": row.get("primary_placement") or row.get("placement"),
            "migration_class": row.get("migration_class"),
            "replica_count": sum(
                1 for replica in row.get("replicas") or () if isinstance(replica, dict)
            ),
            "active_replica_count": sum(
                1 for replica in row.get("replicas") or ()
                if isinstance(replica, dict) and replica.get("status") == "ACTIVE"
            ),
            "replicas": list(row.get("replicas") or ()),
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

    def _http_bytes(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> bytes:
        self._assert_public_endpoint(url)
        request = urllib.request.Request(
            url, headers={"Accept": "text/html,*/*;q=0.8"}, method="GET"
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            final_url = str(response.geturl() or url)
            if final_url != url:
                raise RuntimeError("remote endpoint redirect is not allowed")
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise RuntimeError("remote deployment verification response is too large")
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise RuntimeError("remote deployment verification response is too large")
        return body

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
