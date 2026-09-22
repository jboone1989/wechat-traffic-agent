"""Unified survival-resource control plane for Ferro."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tarfile
import tempfile
from typing import Any
from urllib.parse import urlparse
import urllib.error
import urllib.request

from .storage_lifecycle import StorageLifecycle


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


DOMAINS = ("STORAGE", "COMPUTE", "HOSTING", "REMOTE_MACHINE", "SERVICE")
OPEN_NEED_STATES = {"PENDING", "APPROVED", "AWAITING_RESOURCE"}
AUTONOMOUS_RETRY_ACTIONS = {
    "PROVISION_REMOTE_RECEIVER",
    "RESTORE_SERVICE_REPLICA",
    "ENSURE_SERVICE_REPLICA",
    "OFFLOAD_NON_CORE_SERVICE",
}
AUTONOMOUS_RETRY_SECONDS = (300, 900, 3600, 21600)

SERVICE_PLACEMENT_DEFAULTS: dict[str, dict[str, Any]] = {
    "content-agent-core": {
        "resource_requirements": {
            "cpu_count": 2,
            "memory_available_gib": 2.0,
            "disk_free_gib": 5.0,
        },
        "placement_policy": {
            "mode": "PRIMARY_STATEFUL",
            "desired_remote_replicas": 0,
            "automatic": False,
        },
    },
    "content-agent-core-standby": {
        "resource_requirements": {
            "cpu_count": 1,
            "memory_available_gib": 0.05,
            "disk_free_gib": 1.0,
        },
        "activation_requirements": {
            "cpu_count": 2,
            "memory_available_gib": 2.0,
            "disk_free_gib": 5.0,
        },
        "placement_policy": {
            "mode": "REPLICA",
            "desired_remote_replicas": 1,
            "automatic": True,
        },
    },
    "website-static": {
        "resource_requirements": {
            "cpu_count": 1,
            "memory_available_gib": 0.05,
            "disk_free_gib": 0.25,
        },
        "placement_policy": {
            "mode": "REPLICA",
            "desired_remote_replicas": 1,
            "automatic": True,
        },
    },
    "resource-research": {
        "resource_requirements": {
            "cpu_count": 1,
            "memory_available_gib": 0.5,
            "disk_free_gib": 1.0,
        },
        "placement_policy": {
            "mode": "MOVE",
            "desired_remote_replicas": 0,
            "automatic": True,
            "only_when_local_pressure": True,
        },
    },
}


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
        self.storage_lifecycle = StorageLifecycle(self.state_root)

    def observe(self, *, now: datetime) -> dict[str, Any]:
        instant = _iso(now)
        disk = shutil.disk_usage(self.state_root)
        mem_total, mem_available = self._memory()
        load_1m = self._load()
        cpu_count = max(1, int(os.cpu_count() or 1))
        disk_ratio = (1.0 - (disk.free / disk.total)) if disk.total else 1.0
        mem_ratio = (
            1.0 - (mem_available / mem_total)
            if mem_total and mem_available is not None else None
        )
        storage_mode = self.storage_lifecycle.mode(disk_ratio)
        storage_pressure = storage_mode != "NORMAL"
        deep_storage_pressure = storage_mode == "DEEP"
        storage_learning = self.storage_lifecycle.observe(now=now, used_ratio=disk_ratio)
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
            "maintenance_mode": storage_mode,
            "initial_reclaim_ratio": 0.55,
            "deep_reclaim_ratio": 0.85,
            "inventory_candidate_count": int(storage_learning.get("candidate_count") or 0),
            "estimated_reclaimable_gib": round(int(storage_learning.get("estimated_reclaimable_bytes") or 0) / 1024**3, 3),
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
        host_rows = self.remote_hosts()
        provisionable_remote = any(
            self._ssh_receiver_needs_provision(row)
            for row in host_rows
        )
        degraded_replica = any(
            replica.get("status") != "ACTIVE"
            for service in self.services()
            for replica in service.get("replicas") or ()
        )
        automatic_replica_gap = self._has_automatic_replica_gap()
        needs = self._reconcile_needs(
            now=now,
            storage_mode=storage_mode,
            compute_pressure=compute_pressure,
            active_remote=active_remote,
            usable_remote=usable_remote,
            degraded_replica=degraded_replica,
            automatic_replica_gap=automatic_replica_gap,
            provisionable_remote=provisionable_remote,
        )
        pressure = (
            "CRITICAL" if deep_storage_pressure else
            ("CONSERVE" if compute_pressure else ("WATCH" if storage_pressure else "NORMAL"))
        )
        policy = {
            "schema_version": "survival-resource-policy.v1",
            "observed_at": instant,
            "pressure": pressure,
            "prefer_restructure_before_relocation": True,
            "remote_offload_requires_verified_host": True,
            "primary_migration_requires_explicit_approval": True,
            "safe_local_actions": [
                "RECLAIM_TRANSIENT_STORAGE",
                "DEEP_RECLAIM_STORAGE",
                "APPLY_CONSERVATION_POLICY",
                "PROVISION_REMOTE_RECEIVER",
                "ENSURE_SERVICE_REPLICA",
                "RESTORE_SERVICE_REPLICA",
                "OFFLOAD_NON_CORE_SERVICE",
            ],
        }
        _atomic_json(self.policy_path, policy)
        return {
            "status": "OBSERVED",
            "pressure": pressure,
            "resource_count": len(resources),
            "resources": resources,
            "needs": needs,
            "policy": policy,
            "storage_lifecycle": storage_learning,
        }

    def status(self) -> dict[str, Any]:
        return {
            "resources": (_load_json(self.resources_path, {}) or {}).get("resources", []),
            "needs": self.needs(),
            "remote_hosts": self.remote_hosts(),
            "services": self.services(),
            "placement_candidates": self.placement_candidates(),
            "policy": _load_json(self.policy_path, {}),
            "actions": self.actions(limit=30),
            "storage_lifecycle": self.storage_lifecycle.status(),
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
                    **json.loads(json.dumps(SERVICE_PLACEMENT_DEFAULTS["content-agent-core"])),
                },
                {
                    "service_id": "content-agent-core-standby",
                    "role": "CORE_STANDBY",
                    "placement": "host:local",
                    "primary_placement": "host:local",
                    "replicas": [],
                    "migration_class": "REPLICABLE",
                    "deployment_profile": "content.core-standby.v1",
                    **json.loads(json.dumps(
                        SERVICE_PLACEMENT_DEFAULTS["content-agent-core-standby"]
                    )),
                },
                {
                    "service_id": "website-static",
                    "role": "PUBLIC_SURFACE",
                    "placement": "host:local",
                    "primary_placement": "host:local",
                    "replicas": [],
                    "migration_class": "REPLICABLE",
                    "deployment_profile": "content.website-static.v1",
                    **json.loads(json.dumps(SERVICE_PLACEMENT_DEFAULTS["website-static"])),
                },
                {
                    "service_id": "resource-research",
                    "role": "BACKGROUND_WORK",
                    "placement": "host:local",
                    "primary_placement": "host:local",
                    "replicas": [],
                    "migration_class": "DISPOSABLE_COMPUTE",
                    "deployment_profile": "content.resource-research.v1",
                    **json.loads(json.dumps(SERVICE_PLACEMENT_DEFAULTS["resource-research"])),
                },
            ]
            _atomic_json(self.services_path, value)
            return value

        changed = False
        service_ids = {
            str(row.get("service_id") or "")
            for row in value
            if isinstance(row, dict)
        }
        if "content-agent-core-standby" not in service_ids:
            value.append({
                "service_id": "content-agent-core-standby",
                "role": "CORE_STANDBY",
                "placement": "host:local",
                "primary_placement": "host:local",
                "replicas": [],
                "migration_class": "REPLICABLE",
                "deployment_profile": "content.core-standby.v1",
                **json.loads(json.dumps(
                    SERVICE_PLACEMENT_DEFAULTS["content-agent-core-standby"]
                )),
            })
            changed = True
        for row in value:
            if not isinstance(row, dict):
                continue
            service_id = str(row.get("service_id") or "")
            defaults = SERVICE_PLACEMENT_DEFAULTS.get(service_id) or {}
            for field in (
                "resource_requirements",
                "activation_requirements",
                "placement_policy",
            ):
                if field not in row and field in defaults:
                    row[field] = json.loads(json.dumps(defaults[field]))
                    changed = True
            if service_id == "resource-research":
                policy = dict(row.get("placement_policy") or {})
                desired_policy = dict(defaults.get("placement_policy") or {})
                for field in ("mode", "automatic", "only_when_local_pressure"):
                    desired = desired_policy.get(field)
                    if policy.get(field) != desired:
                        policy[field] = desired
                        changed = True
                row["placement_policy"] = policy
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
                result = self.storage_lifecycle.reclaim(now=now, deep=False)
                row["status"] = "EXECUTED"
            elif action == "DEEP_RECLAIM_STORAGE":
                result = self.storage_lifecycle.reclaim(now=now, deep=True)
                row["status"] = "EXECUTED"
            elif action == "APPLY_CONSERVATION_POLICY":
                result = {"status": "APPLIED", "mode": "CONSERVE"}
                row["status"] = "EXECUTED"
            elif action == "PROVISION_REMOTE_RECEIVER":
                result = self._provision_pending_receiver(now=now)
                row["status"] = (
                    "EXECUTED" if result.get("status") == "VERIFIED" else "AWAITING_RESOURCE"
                )
            elif action == "RESTORE_SERVICE_REPLICA":
                result = self._restore_degraded_replica(now=now)
                row["status"] = (
                    "EXECUTED" if result.get("status") == "DEPLOYED" else "AWAITING_RESOURCE"
                )
            elif action == "ENSURE_SERVICE_REPLICA":
                result = self._ensure_automatic_replica(now=now)
                row["status"] = (
                    "EXECUTED" if result.get("status") == "DEPLOYED" else "AWAITING_RESOURCE"
                )
            elif action == "OFFLOAD_NON_CORE_SERVICE":
                result = self._offload_non_core_service(now=now)
                row["status"] = (
                    "EXECUTED" if result.get("status") == "DEPLOYED" else "AWAITING_RESOURCE"
                )
            elif action in {
                "DISCOVER_REMOTE_MACHINE",
                "RESTORE_REMOTE_RECEIVER",
                "MIGRATE_PRIMARY",
            }:
                result = {"status": "AWAITING_RESOURCE", "reason": "verified remote host or receiver required"}
                row["status"] = "AWAITING_RESOURCE"
            else:
                result = {"status": "UNSUPPORTED", "action": action}
                row["status"] = "FAILED"
            if action in AUTONOMOUS_RETRY_ACTIONS:
                if row.get("status") == "AWAITING_RESOURCE":
                    self._schedule_autonomous_retry(
                        row=row, result=result, now=now
                    )
                else:
                    row["retry_count"] = 0
                    row.pop("next_retry_at", None)
                    row.pop("last_error_type", None)
                    row["last_success_at"] = _iso(now)
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

    def provision_ssh_receiver(
        self, host_id: str, *, now: datetime
    ) -> dict[str, Any]:
        from .survival_receiver_ssh import SSHReceiverProvisioner

        rows = self.remote_hosts()
        target = next((row for row in rows if row.get("host_id") == host_id), None)
        if target is None:
            raise FileNotFoundError(host_id)
        if target.get("management_transport") != "SSH_KEY":
            raise ValueError("receiver provisioning requires an SSH-managed host")
        key_ref = str(target.get("ssh_key_ref") or "").strip()
        if not key_ref:
            raise RuntimeError("SSH-managed host has no key reference")
        key_path = (
            self.state_root / "secrets" / "survival-hosts" / key_ref / "id_ed25519"
        )
        if not key_path.is_file():
            raise RuntimeError("SSH management key is unavailable")
        identity = self._network_identity()
        try:
            result = SSHReceiverProvisioner(self.state_root).provision(
                host=str(target.get("ssh_host") or ""),
                port=int(target.get("ssh_port") or 22),
                username=str(target.get("ssh_username") or "root"),
                key_path=key_path,
                expected_host_key_sha256=str(target.get("ssh_host_key_sha256") or ""),
                expected_host_instance_id=str(target.get("host_instance_id") or ""),
                controller={
                    "agent_id": str(identity.agent_id),
                    "key_id": str(identity.key_id),
                    "public_key": str(identity.public_key),
                },
            )
            target["ssh_receiver_status"] = "VERIFIED"
            target["ssh_receiver_release_sha"] = result.release_sha
            target["ssh_receiver_command"] = result.receiver_command
            target["ssh_receiver_root"] = result.receiver_root
            target["ssh_receiver_agent_id"] = result.agent_id
            target["ssh_receiver_key_id"] = result.key_id
            target["ssh_receiver_public_key"] = result.public_key
            target["ssh_receiver_capabilities"] = list(result.capabilities)
            target["ssh_receiver_profiles"] = list(result.deployment_profiles)
            target["ssh_receiver_last_verified_at"] = _iso(now)
            target["receiver_status"] = "VERIFIED"
            target["deployment_capable"] = True
            target["capabilities"] = sorted(
                set(target.get("capabilities") or ()) | set(result.capabilities)
            )
            target["deployment_profiles"] = sorted(
                set(target.get("deployment_profiles") or ())
                | set(result.deployment_profiles)
            )
            target["capacity"] = dict(result.capacity)
            target["receiver_transport"] = (
                "HTTPS+SSH"
                if target.get("control_url") and target.get("health_url")
                else "SSH"
            )
            target["trust_source"] = (
                str(target.get("trust_source") or "OPERATOR_SSH_BOOTSTRAP")
                + "+PINNED_AGENT_NETWORK_RECEIVER"
            )
            target.pop("ssh_receiver_last_error", None)
        except Exception as exc:
            target["ssh_receiver_status"] = "PROVISION_FAILED"
            target["ssh_receiver_last_error"] = type(exc).__name__
            target["ssh_receiver_last_verified_at"] = _iso(now)
            if not target.get("control_url"):
                target["receiver_status"] = "PROVISION_FAILED"
                target["deployment_capable"] = False
            _atomic_json(self.hosts_path, rows)
            self._record_action({
                "action": "PROVISION_REMOTE_RECEIVER",
                "host_id": host_id,
                "status": "FAILED",
                "error_type": type(exc).__name__,
            }, now)
            raise
        _atomic_json(self.hosts_path, rows)
        self._record_action({
            "action": "PROVISION_REMOTE_RECEIVER",
            "host_id": host_id,
            "status": "VERIFIED",
            "transport": target.get("receiver_transport"),
            "release_sha": target.get("ssh_receiver_release_sha"),
            "agent_id": target.get("ssh_receiver_agent_id"),
            "key_id": target.get("ssh_receiver_key_id"),
        }, now)
        return target

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
                task_url = str(descriptor.get("task_url") or "").strip()
                host_instance_id = str(descriptor.get("host_instance_id") or "").strip()
                if not health_url or not control_url or not host_instance_id:
                    raise ValueError("signed agent card survival resource descriptor is incomplete")
                self._validate_remote_url(health_url)
                self._validate_remote_url(control_url)
                if task_url:
                    self._validate_remote_url(task_url)
                if not (
                    self._same_origin(endpoint, card_url)
                    and self._same_origin(endpoint, health_url)
                    and self._same_origin(endpoint, control_url)
                    and (not task_url or self._same_origin(endpoint, task_url))
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
                    "task_url": task_url or None,
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
        task_url = base + "/application/survival/tasks"
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
        advertised_task_url = str(descriptor.get("task_url") or "").rstrip("/")
        if advertised_task_url and advertised_task_url != task_url:
            raise ValueError("signed receiver task URL does not match expected origin")
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
            "task_url": task_url if advertised_task_url else None,
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
            profiles = [
                str(item) for item in payload.get("deployment_profiles") or ()
                if str(item).strip()
            ]
            if target.get("ssh_receiver_status") == "VERIFIED":
                capabilities = sorted(
                    set(capabilities)
                    | set(target.get("ssh_receiver_capabilities") or ())
                )
                profiles = sorted(
                    set(profiles) | set(target.get("ssh_receiver_profiles") or ())
                )
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
            target["deployment_profiles"] = profiles
            target["deployment_capable"] = (
                bool(target.get("control_url"))
                and "survival.workload.deploy.v1" in capabilities
                and bool(target["agent_id"])
                and bool(target["public_key"])
            )
            if isinstance(payload.get("capacity"), dict):
                target["capacity"] = dict(payload["capacity"])
            target["receiver_status"] = (
                "VERIFIED" if target["deployment_capable"] else "UNAVAILABLE"
            )
            target["last_health_at"] = _iso(now)
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

    def placement_candidates(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        services = self.services()
        hosts = self.remote_hosts()
        for service in services:
            service_id = str(service.get("service_id") or "")
            for host in hosts:
                fit = self._placement_fit(service, host)
                activation = self._activation_readiness(service, host)
                rows.append({
                    "service_id": service_id,
                    "host_id": host.get("host_id"),
                    "host_name": host.get("name"),
                    "eligible": fit["eligible"],
                    "reasons": fit["reasons"],
                    "requirements": dict(service.get("resource_requirements") or {}),
                    "capacity": dict(host.get("capacity") or {}),
                    "deployment_profile": service.get("deployment_profile"),
                    "placement_mode": (service.get("placement_policy") or {}).get("mode"),
                    "score": fit["score"],
                    "activation_ready": activation["ready"],
                    "activation_status": activation["status"],
                    "activation_reasons": activation["reasons"],
                })
        rows.sort(
            key=lambda row: (
                str(row.get("service_id") or ""),
                not bool(row.get("eligible")),
                -float(row.get("score") or 0.0),
                str(row.get("host_id") or ""),
            )
        )
        return rows

    def _placement_fit(
        self, service: dict[str, Any], host: dict[str, Any]
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if host.get("status") != "ACTIVE":
            reasons.append("HOST_NOT_ACTIVE")
        if host.get("receiver_status") != "VERIFIED":
            reasons.append("RECEIVER_NOT_VERIFIED")
        if not host.get("deployment_capable"):
            reasons.append("DEPLOYMENT_CAPABILITY_UNAVAILABLE")

        profile = str(service.get("deployment_profile") or "")
        if profile and profile not in set(host.get("deployment_profiles") or ()):
            reasons.append("DEPLOYMENT_PROFILE_UNSUPPORTED")
        if profile == "content.core-standby.v1":
            if str(host.get("reliability") or "").upper() != "OPERATOR_MANAGED":
                reasons.append("CORE_STANDBY_REQUIRES_OPERATOR_MANAGED_HOST")
            if str(host.get("management_transport") or "") != "SSH_KEY":
                reasons.append("CORE_STANDBY_REQUIRES_MANAGED_SSH")

        requirements = dict(service.get("resource_requirements") or {})
        capacity = dict(host.get("capacity") or {})
        checks = (
            ("cpu_count", "CPU_CAPACITY_INSUFFICIENT"),
            ("memory_available_gib", "MEMORY_HEADROOM_INSUFFICIENT"),
            ("disk_free_gib", "DISK_HEADROOM_INSUFFICIENT"),
        )
        ratios: list[float] = []
        for field, reason in checks:
            required_raw = requirements.get(field)
            if required_raw is None:
                continue
            try:
                required = float(required_raw)
                available = float(capacity.get(field))
            except (TypeError, ValueError):
                reasons.append(field.upper() + "_UNKNOWN")
                continue
            if available < required:
                reasons.append(reason)
            elif required > 0:
                ratios.append(available / required)

        migration_class = str(service.get("migration_class") or "")
        if migration_class == "PRIMARY_STATEFUL":
            reasons.append("PRIMARY_STATEFUL_REQUIRES_DEDICATED_MIGRATION")

        score = min(ratios) if ratios else 0.0
        return {
            "eligible": not reasons,
            "reasons": reasons,
            "score": round(score, 4),
        }

    @staticmethod
    def _activation_readiness(
        service: dict[str, Any], host: dict[str, Any]
    ) -> dict[str, Any]:
        requirements = dict(service.get("activation_requirements") or {})
        if not requirements:
            return {"ready": True, "status": "NOT_APPLICABLE", "reasons": []}
        capacity = dict(host.get("capacity") or {})
        reasons: list[str] = []
        for field, reason in (
            ("cpu_count", "CPU_CAPACITY_INSUFFICIENT"),
            ("memory_available_gib", "MEMORY_HEADROOM_INSUFFICIENT"),
            ("disk_free_gib", "DISK_HEADROOM_INSUFFICIENT"),
        ):
            required_raw = requirements.get(field)
            if required_raw is None:
                continue
            try:
                required = float(required_raw)
                available = float(capacity.get(field))
            except (TypeError, ValueError):
                reasons.append(field.upper() + "_UNKNOWN")
                continue
            if available < required:
                reasons.append(reason)
        return {
            "ready": not reasons,
            "status": "STANDBY_RUNNABLE" if not reasons else "STANDBY_NOT_RUNNABLE",
            "reasons": reasons,
        }

    def plan_offload(
        self, service_id: str, host_id: str, *, now: datetime
    ) -> dict[str, Any]:
        service = next((row for row in self.services() if row.get("service_id") == service_id), None)
        host = next((row for row in self.remote_hosts() if row.get("host_id") == host_id), None)
        if service is None:
            raise FileNotFoundError(service_id)
        if host is None or host.get("status") != "ACTIVE":
            raise ValueError("offload requires an active verified remote host")
        if not host.get("deployment_capable") or not (
            host.get("control_url") or host.get("ssh_receiver_command")
        ):
            raise ValueError("offload requires verified signed deployment capability")
        if service.get("migration_class") == "PRIMARY_STATEFUL":
            raise ValueError("primary stateful migration requires dedicated migration approval")
        profile = str(service.get("deployment_profile") or "")
        if profile not in set(host.get("deployment_profiles") or ()):
            raise ValueError("remote host does not support this deployment profile")
        fit = self._placement_fit(service, host)
        if not fit["eligible"]:
            raise ValueError(
                "remote host does not satisfy workload requirements: "
                + ",".join(fit["reasons"])
            )
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
        if not host.get("deployment_capable") or not (
            host.get("control_url") or host.get("ssh_receiver_command")
        ):
            raise ValueError("remote host has no verified deployment capability")
        if service is None:
            raise FileNotFoundError(service_id)
        if service.get("migration_class") == "PRIMARY_STATEFUL":
            raise ValueError("primary stateful migration requires dedicated migration approval")

        identity = self._network_identity()
        artifact = self._deployment_artifact(service)
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
        if artifact is not None:
            payload["artifact"] = artifact
        payload["object_hash"] = identity.object_hash(payload)
        payload["signature"] = identity.sign_object(payload)
        receipt, deployment_transport = self._deploy_to_host(host, payload)
        self._verify_deployment_receipt(
            receipt=receipt, host=host, action_id=action_id,
            request_hash=str(payload["object_hash"]),
            transport=deployment_transport,
        )
        deployment_id = str(receipt.get("deployment_id") or "")
        if not deployment_id:
            raise RuntimeError("remote deployment receipt omitted deployment_id")
        deployment_url = str(receipt.get("deployment_url") or "").strip()
        profile = str(service.get("deployment_profile") or "")
        if profile == "content.website-static.v1":
            self._verify_static_deployment(
                host=host,
                deployment_url=deployment_url,
                deployment_path=str(receipt.get("deployment_path") or ""),
                artifact=dict(artifact or {}),
                transport=deployment_transport,
            )
        elif profile == "content.core-standby.v1":
            self._verify_core_standby_deployment(
                host=host,
                deployment_path=str(receipt.get("deployment_path") or ""),
                artifact=dict(artifact or {}),
                transport=deployment_transport,
            )

        activation = self._activation_readiness(service, host)

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
                    "deployment_path": str(receipt.get("deployment_path") or "") or None,
                    "deployment_transport": deployment_transport,
                    "artifact_sha256": str(receipt.get("artifact_sha256") or ""),
                    "source_manifest_sha256": str(
                        (artifact or {}).get("source_manifest_sha256") or ""
                    ),
                    "index_sha256": str((artifact or {}).get("index_sha256") or ""),
                    "organism_id": str((artifact or {}).get("organism_id") or "") or None,
                    "memory_state_hash": str(
                        (artifact or {}).get("memory_state_hash") or ""
                    ) or None,
                    "network_agent_id": str(
                        (artifact or {}).get("network_agent_id") or ""
                    ) or None,
                    "member_count": (artifact or {}).get("member_count"),
                    "activation_ready": activation["ready"],
                    "activation_status": activation["status"],
                    "activation_reasons": list(activation["reasons"]),
                    "last_verified_at": _iso(now),
                    "updated_at": _iso(now),
                })
                row["replicas"] = replicas
            else:
                row["placement"] = host_id
                row["primary_placement"] = host_id
                row["deployment_id"] = deployment_id
                row["deployment_url"] = deployment_url or None
                row["deployment_path"] = str(receipt.get("deployment_path") or "") or None
                row["deployment_transport"] = deployment_transport
                row["task_capability"] = str(receipt.get("task_capability") or "") or None
                row["artifact_sha256"] = str(receipt.get("artifact_sha256") or "") or None
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
            "deployment_path": str(receipt.get("deployment_path") or "") or None,
            "deployment_transport": deployment_transport,
            "artifact_sha256": receipt.get("artifact_sha256"),
            "organism_id": (artifact or {}).get("organism_id"),
            "memory_state_hash": (artifact or {}).get("memory_state_hash"),
            "network_agent_id": (artifact or {}).get("network_agent_id"),
            "activation_ready": activation["ready"],
            "activation_status": activation["status"],
            "activation_reasons": list(activation["reasons"]),
            "task_capability": receipt.get("task_capability"),
            "source_manifest_sha256": (artifact or {}).get(
                "source_manifest_sha256"
            ),
            "request_hash": payload["object_hash"],
            "receipt_evidence": "remote-deployment:" + sha256(
                json.dumps(receipt, sort_keys=True).encode()
            ).hexdigest(),
        }
        self._record_action(result, now)
        return result

    def _deployment_artifact(
        self, service: dict[str, Any]
    ) -> dict[str, Any] | None:
        profile = str(service.get("deployment_profile") or "")
        if profile == "content.resource-research.v1":
            return None
        if profile == "content.core-standby.v1":
            from .organism_continuity import OrganismContinuityService

            with tempfile.TemporaryDirectory(prefix="ferro-core-standby-") as temp_dir:
                path = Path(temp_dir) / "ferro-core-standby.bin"
                exported = OrganismContinuityService(self.state_root).export_bundle(path)
                raw = path.read_bytes()
            return {
                "media_type": "application/octet-stream",
                "encoding": "base64",
                "sha256": str(exported["sha256"]),
                "bytes": len(raw),
                "data": base64.b64encode(raw).decode("ascii"),
                "organism_id": exported["organism_id"],
                "memory_state_hash": exported["memory_state_hash"],
                "network_agent_id": (
                    (exported.get("network_identity") or {}).get("agent_id")
                ),
                "member_count": exported["member_count"],
                "requires_operator_managed_host": True,
            }
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
        manifest = sha256()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise RuntimeError("website-static source contains a symlink")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise RuntimeError("website-static source contains a non-file entry")
                relative = path.relative_to(root)
                data = path.read_bytes()
                file_count += 1
                extracted_bytes += len(data)
                if file_count > 5000 or extracted_bytes > 64 * 1024 * 1024:
                    raise RuntimeError("website-static source exceeds receiver limits")
                relative_bytes = relative.as_posix().encode("utf-8")
                manifest.update(len(relative_bytes).to_bytes(4, "big"))
                manifest.update(relative_bytes)
                manifest.update(len(data).to_bytes(8, "big"))
                manifest.update(sha256(data).digest())
                info = tarfile.TarInfo(relative.as_posix())
                info.size = len(data)
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
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
            "source_manifest_sha256": "sha256:" + manifest.hexdigest(),
            "index_sha256": "sha256:" + sha256(index_bytes).hexdigest(),
        }

    def _deploy_to_host(
        self, host: dict[str, Any], payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        if (
            str(payload.get("deployment_profile") or "") == "content.core-standby.v1"
            and host.get("ssh_receiver_command")
        ):
            return self._ssh_receiver_transport(host).deploy(payload), "SSH"
        control_url = str(host.get("control_url") or "").strip()
        if control_url:
            try:
                return (
                    self._http_json(
                        control_url, method="POST", payload=payload, timeout=60.0
                    ),
                    "HTTPS",
                )
            except Exception:
                if not host.get("ssh_receiver_command"):
                    raise
        if host.get("ssh_receiver_command"):
            return self._ssh_receiver_transport(host).deploy(payload), "SSH"
        raise RuntimeError("remote host has no deployment transport")

    def fetch_remote_resource_research(
        self, url: str, *, max_chars: int, now: datetime
    ) -> dict[str, Any]:
        service = next(
            (
                row for row in self.services()
                if row.get("service_id") == "resource-research"
            ),
            None,
        )
        if service is None:
            raise RuntimeError("resource-research service is unavailable")
        host_id = str(service.get("placement") or "")
        if not host_id or host_id == "host:local":
            raise RuntimeError("resource-research is not remotely placed")
        if str(service.get("task_capability") or "") != "survival.workload.task.v1":
            raise RuntimeError("resource-research placement has no task capability")
        host = next(
            (
                row for row in self.remote_hosts()
                if str(row.get("host_id") or "") == host_id
            ),
            None,
        )
        if (
            host is None
            or host.get("status") != "ACTIVE"
            or host.get("receiver_status") != "VERIFIED"
            or "survival.workload.task.v1" not in set(host.get("capabilities") or ())
        ):
            raise RuntimeError("resource-research remote host is not task-capable")

        identity = self._network_identity()
        requested_at = _iso(now)
        task_id = "research-task:" + sha256(
            (
                str(url)
                + "|"
                + str(int(max_chars))
                + "|"
                + requested_at
            ).encode()
        ).hexdigest()[:24]
        payload = {
            "type": "survival.workload.task.v1",
            "task_id": task_id,
            "deployment_profile": "content.resource-research.v1",
            "operation": "fetch_public_text",
            "payload": {
                "url": str(url),
                "max_chars": max(1, min(int(max_chars), 100000)),
            },
            "requester_agent_id": identity.agent_id,
            "requester_key_id": identity.key_id,
            "requested_at": requested_at,
            "public_key": identity.public_key,
        }
        payload["object_hash"] = identity.object_hash(payload)
        payload["signature"] = identity.sign_object(payload)
        try:
            receipt, transport = self._task_on_host(host, payload)
            self._verify_task_receipt(
                receipt=receipt,
                host=host,
                task_id=task_id,
                request_hash=str(payload["object_hash"]),
                transport=transport,
            )
            result = receipt.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("remote research receipt omitted result")
            text = str(result.get("text") or "")
            result_url = str(result.get("url") or "").strip()
            if not result_url:
                raise RuntimeError("remote research result omitted url")
            evidence = "remote-research:" + sha256(
                json.dumps(receipt, sort_keys=True).encode()
            ).hexdigest()
            self._record_action({
                "action": "RUN_REMOTE_RESOURCE_RESEARCH",
                "status": "COMPLETED",
                "host_id": host_id,
                "task_id": task_id,
                "transport": transport,
                "url": result_url,
                "chars": len(text),
                "receipt_evidence": evidence,
            }, now)
            return {
                "receipt_id": evidence,
                "transport": transport,
                "host_id": host_id,
                "result": result,
            }
        except Exception as exc:
            self._record_action({
                "action": "RUN_REMOTE_RESOURCE_RESEARCH",
                "status": "FAILED",
                "host_id": host_id,
                "task_id": task_id,
                "error_type": type(exc).__name__,
            }, now)
            raise

    def _task_on_host(
        self, host: dict[str, Any], payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        ssh_capabilities = set(host.get("ssh_receiver_capabilities") or ())
        if (
            "survival.workload.task.v1" in ssh_capabilities
            and host.get("ssh_receiver_command")
        ):
            return self._ssh_receiver_transport(host).task(payload), "SSH"

        task_url = str(host.get("task_url") or "").strip()
        if task_url:
            try:
                return (
                    self._http_json(
                        task_url, method="POST", payload=payload, timeout=45.0
                    ),
                    "HTTPS",
                )
            except Exception:
                if not host.get("ssh_receiver_command"):
                    raise
        if host.get("ssh_receiver_command"):
            return self._ssh_receiver_transport(host).task(payload), "SSH"
        raise RuntimeError("remote host has no task transport")

    @staticmethod
    def _verify_task_receipt(
        *, receipt: dict[str, Any], host: dict[str, Any],
        task_id: str, request_hash: str, transport: str,
    ) -> None:
        from agent_network.crypto import verify_object

        if str(receipt.get("type") or "") != "survival.workload.task.receipt.v1":
            raise RuntimeError("remote task receipt type is invalid")
        if str(receipt.get("status") or "").upper() != "COMPLETED":
            raise RuntimeError("remote task did not reach COMPLETED")
        if str(receipt.get("task_id") or "") != task_id:
            raise RuntimeError("remote task receipt task_id mismatch")
        if str(receipt.get("request_hash") or "") != request_hash:
            raise RuntimeError("remote task receipt request_hash mismatch")
        if transport == "SSH":
            expected_agent_id = str(host.get("ssh_receiver_agent_id") or "")
            expected_key_id = str(host.get("ssh_receiver_key_id") or "")
            public_key = str(host.get("ssh_receiver_public_key") or "")
        else:
            expected_agent_id = str(host.get("agent_id") or "")
            expected_key_id = str(host.get("key_id") or "")
            public_key = str(host.get("public_key") or "")
        if str(receipt.get("agent_id") or "") != expected_agent_id:
            raise RuntimeError("remote task receipt agent identity mismatch")
        if expected_key_id and str(receipt.get("key_id") or "") != expected_key_id:
            raise RuntimeError("remote task receipt key identity mismatch")
        signature = str(receipt.get("signature") or "")
        if (
            not signature
            or not public_key
            or not verify_object(public_key, receipt, signature)
        ):
            raise RuntimeError("remote task receipt signature is invalid")

    def _verify_static_deployment(
        self, *, host: dict[str, Any], deployment_url: str,
        deployment_path: str, artifact: dict[str, Any], transport: str,
    ) -> None:
        expected = str(artifact.get("index_sha256") or "")
        if not expected:
            raise RuntimeError("local static artifact omitted index digest")
        if transport == "HTTPS":
            if not deployment_url:
                raise RuntimeError("remote static deployment omitted deployment_url")
            control_url = str(host.get("control_url") or "")
            if not self._same_origin(control_url, deployment_url):
                raise RuntimeError("remote deployment URL origin mismatch")
            body = self._http_bytes(
                deployment_url, timeout=12.0, max_bytes=4 * 1024 * 1024
            )
            actual = "sha256:" + sha256(body).hexdigest()
        elif transport == "SSH":
            if not deployment_path:
                raise RuntimeError("SSH deployment omitted deployment path")
            actual = self._ssh_receiver_transport(host).static_index_sha256(
                deployment_path
            )
        else:
            raise RuntimeError("unknown deployment transport")
        if actual != expected:
            raise RuntimeError("remote static index verification failed")

    def _verify_core_standby_deployment(
        self, *, host: dict[str, Any], deployment_path: str,
        artifact: dict[str, Any], transport: str,
    ) -> None:
        if transport != "SSH":
            raise RuntimeError("core standby verification requires managed SSH")
        expected = str(artifact.get("sha256") or "").strip()
        if not expected:
            raise RuntimeError("core standby artifact omitted digest")
        receiver_root = str(host.get("ssh_receiver_root") or "").strip()
        if not receiver_root:
            raise RuntimeError("core standby host omitted managed receiver root")
        raw = self._ssh_receiver_transport(host).fetch_opaque_artifact(
            deployment_path,
            expected_root=receiver_root,
        )
        actual = "sha256:" + sha256(raw).hexdigest()
        if actual != expected:
            raise RuntimeError("remote core standby artifact hash mismatch")
        from .organism_continuity import OrganismContinuityService

        with tempfile.TemporaryDirectory(prefix="ferro-core-standby-verify-") as temp_dir:
            path = Path(temp_dir) / "ferro-core-standby.bin"
            path.write_bytes(raw)
            OrganismContinuityService.inspect_bundle(
                path,
                expected_sha256=expected,
            )

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

    def _ssh_receiver_transport(self, host: dict[str, Any]):
        from .survival_receiver_ssh import SSHWorkloadTransport

        command = str(host.get("ssh_receiver_command") or "").strip()
        key_ref = str(host.get("ssh_key_ref") or "").strip()
        if not command or not key_ref:
            raise RuntimeError("SSH receiver transport is not configured")
        key_path = (
            self.state_root / "secrets" / "survival-hosts" / key_ref / "id_ed25519"
        )
        if not key_path.is_file():
            raise RuntimeError("SSH management key is unavailable")
        return SSHWorkloadTransport(
            host=str(host.get("ssh_host") or ""),
            port=int(host.get("ssh_port") or 22),
            username=str(host.get("ssh_username") or "root"),
            key_path=key_path,
            expected_host_key_sha256=str(host.get("ssh_host_key_sha256") or ""),
            receiver_command=command,
        )

    def _receiver_health(self, host: dict[str, Any]) -> tuple[dict[str, Any], str]:
        health_url = str(host.get("health_url") or "").strip()
        if health_url:
            try:
                return self._http_json(health_url, method="GET", timeout=8.0), "HTTPS"
            except Exception:
                if not host.get("ssh_receiver_command"):
                    raise
        if host.get("ssh_receiver_command"):
            return self._ssh_receiver_transport(host).health(), "SSH"
        raise RuntimeError("remote host has no receiver health transport")

    def _refresh_remote_hosts(self, *, now: datetime) -> None:
        rows = self.remote_hosts()
        changed = False
        for row in rows:
            if not row.get("health_url") and not row.get("ssh_receiver_command"):
                continue
            try:
                payload, transport = self._receiver_health(row)
                healthy = str(payload.get("status") or payload.get("health") or "").upper()
                if healthy not in {"OK", "HEALTHY", "READY"}:
                    raise RuntimeError("remote receiver health is not ready")
                remote_instance = str(payload.get("host_instance_id") or "").strip()
                expected_instance = str(row.get("host_instance_id") or "").strip()
                if expected_instance and remote_instance != expected_instance:
                    raise RuntimeError("remote receiver physical host identity changed")
                if transport == "SSH":
                    expected_agent = str(row.get("ssh_receiver_agent_id") or "").strip()
                    expected_key = str(row.get("ssh_receiver_key_id") or "").strip()
                    expected_public = str(row.get("ssh_receiver_public_key") or "").strip()
                else:
                    expected_agent = str(
                        row.get("agent_id") or row.get("expected_agent_id") or ""
                    ).strip()
                    expected_key = str(
                        row.get("key_id") or row.get("expected_key_id") or ""
                    ).strip()
                    expected_public = str(
                        row.get("public_key") or row.get("expected_public_key") or ""
                    ).strip()
                if expected_agent and str(payload.get("agent_id") or "") != expected_agent:
                    raise RuntimeError("remote receiver agent identity changed")
                if expected_key and str(payload.get("key_id") or "") != expected_key:
                    raise RuntimeError("remote receiver key identity changed")
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
                if transport == "HTTPS" and row.get("ssh_receiver_status") == "VERIFIED":
                    capabilities = sorted(
                        set(capabilities)
                        | set(row.get("ssh_receiver_capabilities") or ())
                    )
                    profiles = sorted(
                        set(profiles) | set(row.get("ssh_receiver_profiles") or ())
                    )
                row["status"] = "ACTIVE"
                row["receiver_status"] = "VERIFIED"
                row["deployment_capable"] = (
                    "survival.workload.deploy.v1" in capabilities
                    and bool(payload.get("agent_id"))
                    and bool(payload.get("public_key"))
                    and (
                        (transport == "HTTPS" and bool(row.get("control_url")))
                        or (transport == "SSH" and bool(row.get("ssh_receiver_command")))
                    )
                )
                row["active_receiver_transport"] = transport
                row["capabilities"] = capabilities
                row["deployment_profiles"] = profiles
                if isinstance(payload.get("capacity"), dict):
                    row["capacity"] = dict(payload["capacity"])
                row["last_health_at"] = _iso(now)
                row["last_verified_at"] = _iso(now)
                if transport == "SSH":
                    row["ssh_receiver_last_verified_at"] = _iso(now)
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
                    deployment_path = str(replica.get("deployment_path") or "").strip()
                    transport = str(replica.get("deployment_transport") or "").upper()
                    if profile == "content.website-static.v1":
                        desired_manifest = self._local_static_manifest_sha256()
                        deployed_manifest = str(
                            replica.get("source_manifest_sha256") or ""
                        ).strip()
                        if not desired_manifest or deployed_manifest != desired_manifest:
                            replica["status"] = "STALE"
                            replica["last_error"] = "PRIMARY_ARTIFACT_CHANGED"
                            replica["last_verified_at"] = _iso(now)
                            changed = True
                            continue
                        expected = str(replica.get("index_sha256") or "").strip()
                        if not expected:
                            expected = self._local_static_index_sha256()
                            if expected:
                                replica["index_sha256"] = expected
                        if transport == "SSH" or (not deployment_url and deployment_path):
                            if not deployment_path:
                                raise RuntimeError("SSH replica has no deployment path")
                            actual = self._ssh_receiver_transport(host).static_index_sha256(
                                deployment_path
                            )
                            replica["deployment_transport"] = "SSH"
                        else:
                            if not deployment_url:
                                raise RuntimeError("replica has no deployment URL")
                            body = self._http_bytes(
                                deployment_url, timeout=12.0, max_bytes=4 * 1024 * 1024
                            )
                            actual = "sha256:" + sha256(body).hexdigest()
                            replica["deployment_transport"] = "HTTPS"
                        if not expected or actual != expected:
                            raise RuntimeError("replica content hash mismatch")
                    elif profile == "content.core-standby.v1":
                        self._verify_core_standby_deployment(
                            host=host,
                            deployment_path=deployment_path,
                            artifact={
                                "sha256": str(replica.get("artifact_sha256") or "")
                            },
                            transport=transport,
                        )
                        activation = self._activation_readiness(service, host)
                        replica["activation_ready"] = activation["ready"]
                        replica["activation_status"] = activation["status"]
                        replica["activation_reasons"] = list(activation["reasons"])
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

    def _local_static_manifest_sha256(self) -> str:
        raw_root = str(os.environ.get("CONTENT_WEB_SITE_ROOT") or "").strip()
        if not raw_root:
            return ""
        try:
            root = Path(raw_root).resolve(strict=True)
            manifest = sha256()
            file_count = 0
            extracted_bytes = 0
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    return ""
                if path.is_dir():
                    continue
                if not path.is_file():
                    return ""
                relative = path.relative_to(root)
                data = path.read_bytes()
                file_count += 1
                extracted_bytes += len(data)
                if file_count > 5000 or extracted_bytes > 64 * 1024 * 1024:
                    return ""
                relative_bytes = relative.as_posix().encode("utf-8")
                manifest.update(len(relative_bytes).to_bytes(4, "big"))
                manifest.update(relative_bytes)
                manifest.update(len(data).to_bytes(8, "big"))
                manifest.update(sha256(data).digest())
            if file_count == 0:
                return ""
            return "sha256:" + manifest.hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _ssh_receiver_needs_provision(host: dict[str, Any]) -> bool:
        if (
            host.get("status") != "ACTIVE"
            or host.get("management_transport") != "SSH_KEY"
        ):
            return False
        from .survival_receiver_ssh import desired_receiver_sha

        required_profiles = {
            "content.website-static.v1",
            "content.resource-research.v1",
            "content.core-standby.v1",
        }
        ssh_status = str(host.get("ssh_receiver_status") or "")
        overall_status = str(host.get("receiver_status") or "")
        current_sha = str(host.get("ssh_receiver_release_sha") or "").lower()
        profiles = set(
            host.get("ssh_receiver_profiles")
            or host.get("deployment_profiles")
            or ()
        )
        if ssh_status in {"NOT_PROVISIONED", "PROVISION_FAILED"}:
            return True
        if not ssh_status and overall_status in {"", "NOT_PROVISIONED", "PROVISION_FAILED"}:
            return True
        has_managed_ssh_receiver = bool(
            host.get("ssh_receiver_command") or host.get("ssh_receiver_release_sha")
        )
        if not has_managed_ssh_receiver:
            return False
        return (
            current_sha != desired_receiver_sha()
            or not required_profiles.issubset(profiles)
        )

    def _provision_pending_receiver(self, *, now: datetime) -> dict[str, Any]:
        for host in self.remote_hosts():
            if self._ssh_receiver_needs_provision(host):
                try:
                    row = self.provision_ssh_receiver(str(host.get("host_id") or ""), now=now)
                    return {
                        "status": "VERIFIED",
                        "host_id": row.get("host_id"),
                        "transport": row.get("receiver_transport"),
                        "release_sha": row.get("ssh_receiver_release_sha"),
                    }
                except Exception as exc:
                    return {
                        "status": "FAILED",
                        "host_id": host.get("host_id"),
                        "error_type": type(exc).__name__,
                    }
        return {
            "status": "AWAITING_RESOURCE",
            "reason": "no SSH-managed host is awaiting receiver provisioning",
        }

    def _has_automatic_replica_gap(self) -> bool:
        candidates = self.placement_candidates()
        by_service: dict[str, list[dict[str, Any]]] = {}
        for row in candidates:
            by_service.setdefault(str(row.get("service_id") or ""), []).append(row)
        for service in self.services():
            policy = dict(service.get("placement_policy") or {})
            if not policy.get("automatic") or str(policy.get("mode") or "") != "REPLICA":
                continue
            desired = max(0, int(policy.get("desired_remote_replicas") or 0))
            replicas = [
                item for item in service.get("replicas") or ()
                if isinstance(item, dict)
            ]
            if len(replicas) >= desired:
                continue
            occupied = {str(item.get("host_id") or "") for item in replicas}
            if any(
                row.get("eligible") and str(row.get("host_id") or "") not in occupied
                for row in by_service.get(str(service.get("service_id") or ""), ())
            ):
                return True
        return False

    def _ensure_automatic_replica(self, *, now: datetime) -> dict[str, Any]:
        candidates = self.placement_candidates()
        for service in self.services():
            policy = dict(service.get("placement_policy") or {})
            if not policy.get("automatic") or str(policy.get("mode") or "") != "REPLICA":
                continue
            desired = max(0, int(policy.get("desired_remote_replicas") or 0))
            replicas = [
                item for item in service.get("replicas") or ()
                if isinstance(item, dict)
            ]
            if len(replicas) >= desired:
                continue
            occupied = {str(item.get("host_id") or "") for item in replicas}
            eligible = [
                row for row in candidates
                if row.get("service_id") == service.get("service_id")
                and row.get("eligible")
                and str(row.get("host_id") or "") not in occupied
            ]
            if not eligible:
                continue
            eligible.sort(
                key=lambda row: (-float(row.get("score") or 0.0), str(row.get("host_id") or ""))
            )
            target = eligible[0]
            try:
                plan = self.plan_offload(
                    str(service.get("service_id") or ""),
                    str(target.get("host_id") or ""),
                    now=now,
                )
                result = self.execute_offload(plan["action_id"], now=now)
                result["automatic_replica"] = True
                result["placement_score"] = target.get("score")
                return result
            except Exception as exc:
                return {
                    "status": "FAILED",
                    "reason": "automatic replica deployment failed",
                    "error_type": type(exc).__name__,
                    "service_id": service.get("service_id"),
                    "host_id": target.get("host_id"),
                }
        return {
            "status": "AWAITING_RESOURCE",
            "reason": "no automatic replica placement is currently eligible",
        }

    def _automatic_offload_target(
        self, *, local_pressure: bool | None = None
    ) -> dict[str, Any] | None:
        if local_pressure is None:
            policy = _load_json(self.policy_path, {}) or {}
            local_pressure = str(policy.get("pressure") or "NORMAL").upper() in {
                "CONSERVE",
                "CRITICAL",
            }
        candidates = self.placement_candidates()
        services = {
            str(row.get("service_id") or ""): row
            for row in self.services()
            if isinstance(row, dict)
        }
        eligible: list[dict[str, Any]] = []
        for row in candidates:
            if not row.get("eligible"):
                continue
            service = services.get(str(row.get("service_id") or ""))
            if service is None:
                continue
            policy = dict(service.get("placement_policy") or {})
            if (
                not policy.get("automatic")
                or str(policy.get("mode") or "") != "MOVE"
                or not policy.get("only_when_local_pressure")
                or not local_pressure
                or str(service.get("placement") or "host:local") != "host:local"
                or str(service.get("migration_class") or "") == "PRIMARY_STATEFUL"
            ):
                continue
            eligible.append(row)
        if not eligible:
            return None
        eligible.sort(
            key=lambda row: (
                -float(row.get("score") or 0.0),
                str(row.get("service_id") or ""),
                str(row.get("host_id") or ""),
            )
        )
        return eligible[0]

    def _offload_non_core_service(self, *, now: datetime) -> dict[str, Any]:
        target = self._automatic_offload_target()
        if target is None:
            return {
                "status": "AWAITING_RESOURCE",
                "reason": "no automatic non-core placement is currently eligible",
            }
        service_id = str(target.get("service_id") or "")
        host_id = str(target.get("host_id") or "")
        try:
            plan = self.plan_offload(service_id, host_id, now=now)
            result = self.execute_offload(plan["action_id"], now=now)
            result["automatic_move"] = True
            result["placement_score"] = target.get("score")
            return result
        except Exception as exc:
            return {
                "status": "FAILED",
                "reason": "automatic non-core offload failed",
                "error_type": type(exc).__name__,
                "service_id": service_id,
                "host_id": host_id,
            }

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

    @staticmethod
    def _schedule_autonomous_retry(
        *, row: dict[str, Any], result: dict[str, Any], now: datetime
    ) -> None:
        retry_count = max(0, int(row.get("retry_count") or 0)) + 1
        delay = AUTONOMOUS_RETRY_SECONDS[
            min(retry_count - 1, len(AUTONOMOUS_RETRY_SECONDS) - 1)
        ]
        row["retry_count"] = retry_count
        row["next_retry_at"] = _iso(now + timedelta(seconds=delay))
        row["last_result_status"] = str(result.get("status") or "")
        error_type = str(result.get("error_type") or "").strip()
        if error_type:
            row["last_error_type"] = error_type

    @staticmethod
    def _retry_due(row: dict[str, Any], now: datetime) -> bool:
        raw = str(row.get("next_retry_at") or "").strip()
        if not raw:
            return True
        try:
            instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        if instant.tzinfo is None or instant.utcoffset() is None:
            instant = instant.replace(tzinfo=timezone.utc)
        current = now
        if current.tzinfo is None or current.utcoffset() is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= instant

    @staticmethod
    def _coalesce_need_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        passthrough: list[dict[str, Any]] = []
        for row in rows:
            domain = str(row.get("domain") or "")
            action = str(row.get("action") or "")
            if not domain or not action:
                passthrough.append(row)
                continue
            grouped.setdefault((domain, action), []).append(row)

        merged: list[dict[str, Any]] = []
        for _, items in grouped.items():
            if len(items) == 1:
                row = items[0]
                row.setdefault("occurrence_count", 1)
                row.setdefault("last_occurrence_at", row.get("created_at"))
                merged.append(row)
                continue
            ordered = sorted(
                items,
                key=lambda item: str(
                    item.get("last_action_at")
                    or item.get("last_occurrence_at")
                    or item.get("created_at")
                    or ""
                ),
            )
            current = dict(ordered[-1])
            current["occurrence_count"] = sum(
                max(1, int(item.get("occurrence_count") or 1))
                for item in ordered
            )
            created = [
                str(item.get("created_at") or "")
                for item in ordered
                if str(item.get("created_at") or "")
            ]
            if created:
                current["created_at"] = min(created)
            current["last_occurrence_at"] = max(
                str(item.get("last_occurrence_at") or item.get("created_at") or "")
                for item in ordered
            )
            successes = [
                str(item.get("last_success_at") or "")
                for item in ordered
                if str(item.get("last_success_at") or "")
            ]
            if successes:
                current["last_success_at"] = max(successes)
            merged.append(current)
        return passthrough + merged

    def _reconcile_needs(
        self, *, now: datetime, compute_pressure: bool, active_remote: bool,
        usable_remote: bool, degraded_replica: bool,
        automatic_replica_gap: bool, provisionable_remote: bool,
        storage_mode: str | None = None, storage_pressure: bool | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._coalesce_need_rows(self.needs())
        desired: list[tuple[str, str, str, str]] = []
        if storage_mode is None:
            storage_mode = "INITIAL" if storage_pressure else "NORMAL"
        storage_pressure = storage_mode != "NORMAL"
        if storage_mode == "DEEP":
            desired.append(("STORAGE", "DEEP_RECLAIM_STORAGE", "HIGH",
                            "Disk usage reached deep-reclamation pressure (>=85%)"))
        elif storage_mode == "INITIAL":
            desired.append(("STORAGE", "RECLAIM_TRANSIENT_STORAGE", "NORMAL",
                            "Disk usage reached early reclamation pressure (>=55%)"))
        if compute_pressure:
            desired.append(("COMPUTE", "APPLY_CONSERVATION_POLICY", "HIGH",
                            "CPU/RAM pressure threatens continued operation"))
        if not active_remote:
            desired.append(("REMOTE_MACHINE", "DISCOVER_REMOTE_MACHINE", "NORMAL",
                            "No verified remote body is available for continuity or pressure relief"))
        if provisionable_remote:
            desired.append(("REMOTE_MACHINE", "PROVISION_REMOTE_RECEIVER", "NORMAL",
                            "An SSH-managed remote body is ready for a pinned signed workload receiver"))
        elif active_remote and not usable_remote:
            desired.append(("REMOTE_MACHINE", "RESTORE_REMOTE_RECEIVER", "HIGH",
                            "A remote body exists but its signed workload receiver is unavailable"))
        if degraded_replica:
            desired.append(("SERVICE", "RESTORE_SERVICE_REPLICA", "HIGH",
                            "A managed service replica failed its remote health or content verification"))
        if automatic_replica_gap:
            desired.append(("SERVICE", "ENSURE_SERVICE_REPLICA", "NORMAL",
                            "A service policy requires a remote replica and an eligible host is available"))
        if (
            (storage_pressure or compute_pressure)
            and usable_remote
            and self._automatic_offload_target(
                local_pressure=(storage_pressure or compute_pressure)
            ) is not None
        ):
            desired.append(("SERVICE", "OFFLOAD_NON_CORE_SERVICE", "NORMAL",
                            "Move non-core workloads before considering primary migration"))

        desired_keys = {(domain, action) for domain, action, _, _ in desired}
        managed_actions = {
            "RECLAIM_TRANSIENT_STORAGE",
            "DEEP_RECLAIM_STORAGE",
            "DISCOVER_REMOTE_MACHINE",
            "RESTORE_REMOTE_RECEIVER",
            "PROVISION_REMOTE_RECEIVER",
            "RESTORE_SERVICE_REPLICA",
            "ENSURE_SERVICE_REPLICA",
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
                (
                    row for row in rows
                    if row.get("domain") == domain and row.get("action") == action
                ),
                None,
            )
            autonomous = action in {
                "RECLAIM_TRANSIENT_STORAGE",
                "DEEP_RECLAIM_STORAGE",
                "APPLY_CONSERVATION_POLICY",
                "DISCOVER_REMOTE_MACHINE",
                "PROVISION_REMOTE_RECEIVER",
                "RESTORE_SERVICE_REPLICA",
                "ENSURE_SERVICE_REPLICA",
                "OFFLOAD_NON_CORE_SERVICE",
            }
            if existing is not None:
                existing["urgency"] = urgency
                existing["reason"] = reason
                status = str(existing.get("status") or "")
                if status in {"EXECUTED", "SATISFIED"}:
                    existing["status"] = "APPROVED" if autonomous else "PENDING"
                    existing["authorization"] = (
                        "AUTONOMOUS_SAFE_POLICY" if autonomous else "OPERATOR_REQUIRED"
                    )
                    existing["occurrence_count"] = max(
                        1, int(existing.get("occurrence_count") or 1)
                    ) + 1
                    existing["last_occurrence_at"] = _iso(now)
                    existing["reactivated_at"] = _iso(now)
                    existing.pop("satisfied_at", None)
                    existing.pop("next_retry_at", None)
                    existing.pop("last_error_type", None)
                    existing.pop("retry_rearmed_at", None)
                elif (
                    action in AUTONOMOUS_RETRY_ACTIONS
                    and status == "AWAITING_RESOURCE"
                    and self._retry_due(existing, now)
                ):
                    existing["status"] = "APPROVED"
                    existing["authorization"] = "AUTONOMOUS_SAFE_POLICY"
                    existing["retry_rearmed_at"] = _iso(now)
            if existing is None:
                signature = f"{domain}|{action}|{reason}"
                autonomous = action in {
                    "RECLAIM_TRANSIENT_STORAGE",
                    "DEEP_RECLAIM_STORAGE",
                    "APPLY_CONSERVATION_POLICY",
                    "DISCOVER_REMOTE_MACHINE",
                    "PROVISION_REMOTE_RECEIVER",
                    "RESTORE_SERVICE_REPLICA",
                    "ENSURE_SERVICE_REPLICA",
                    "OFFLOAD_NON_CORE_SERVICE",
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
                    "last_occurrence_at": _iso(now),
                    "occurrence_count": 1,
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
            "deployment_profile": row.get("deployment_profile"),
            "resource_requirements": dict(row.get("resource_requirements") or {}),
            "placement_policy": dict(row.get("placement_policy") or {}),
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

        venv_result = self._reclaim_stale_content_venvs(
            now=now,
            already_reclaimed=reclaimed,
        )
        reclaimed += int(venv_result["reclaimed_bytes"])
        return {
            "status": "EXECUTED",
            "deleted_files": deleted,
            "deleted_venvs": int(venv_result["deleted_venvs"]),
            "protected_venvs": list(venv_result["protected_venvs"]),
            "reclaimed_bytes": reclaimed,
        }

    def _reclaim_stale_content_venvs(
        self, *, now: datetime, already_reclaimed: int = 0
    ) -> dict[str, Any]:
        """Delete only stale Ferro deployment venvs under the artifact root.

        The running interpreter is always protected and the two newest other
        environments are retained as rollback candidates. Other projects are
        intentionally outside this collector's authority.
        """
        raw_root = str(os.environ.get("CONTENT_SURVIVAL_ARTIFACT_ROOT") or "").strip()
        artifact_root = Path(raw_root).expanduser().resolve() if raw_root else Path.home().resolve()
        if not artifact_root.is_dir():
            return {"deleted_venvs": 0, "reclaimed_bytes": 0, "protected_venvs": []}

        running_prefix = Path(sys.prefix).resolve()
        rows: list[tuple[float, Path]] = []
        for path in artifact_root.glob("content-agent-prod-venv-*"):
            try:
                resolved = path.resolve()
                if not resolved.is_dir() or path.is_symlink():
                    continue
                rows.append((resolved.stat().st_mtime, resolved))
            except OSError:
                continue
        rows.sort(key=lambda item: item[0], reverse=True)

        protected = {running_prefix}
        protected.update(path for _, path in rows[:2])
        cutoff = now.timestamp() - 24 * 3600
        deleted_venvs = 0
        reclaimed = 0
        reclaim_cap = max(0, 8 * 1024**3 - int(already_reclaimed))
        for mtime, path in rows:
            if path in protected or mtime >= cutoff or reclaimed >= reclaim_cap:
                continue
            try:
                size = self._tree_size(path)
                if size <= 0:
                    continue
                shutil.rmtree(path)
                reclaimed += size
                deleted_venvs += 1
            except OSError:
                continue
        return {
            "deleted_venvs": deleted_venvs,
            "reclaimed_bytes": reclaimed,
            "protected_venvs": sorted(str(path) for path in protected if path in {p for _, p in rows}),
        }

    @staticmethod
    def _tree_size(root: Path) -> int:
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

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
        action_id: str, request_hash: str, transport: str,
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
        if transport == "SSH":
            expected_agent_id = str(host.get("ssh_receiver_agent_id") or "")
            expected_key_id = str(host.get("ssh_receiver_key_id") or "")
            public_key = str(host.get("ssh_receiver_public_key") or "")
        else:
            expected_agent_id = str(host.get("agent_id") or "")
            expected_key_id = str(host.get("key_id") or "")
            public_key = str(host.get("public_key") or "")
        if str(receipt.get("agent_id") or "") != expected_agent_id:
            raise RuntimeError("remote deployment receipt agent identity mismatch")
        if expected_key_id and str(receipt.get("key_id") or "") != expected_key_id:
            raise RuntimeError("remote deployment receipt key identity mismatch")
        signature = str(receipt.get("signature") or "")
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
