"""Capability placement control: keep the subject core stable and move replaceable work."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


TRUST_EPHEMERAL = "EPHEMERAL"
TRUST_TRUSTED = "TRUSTED"
TRUST_AUTHORITATIVE = "AUTHORITATIVE"
TRUST_LEVELS = (TRUST_EPHEMERAL, TRUST_TRUSTED, TRUST_AUTHORITATIVE)
_TRUST_RANK = {name: index for index, name in enumerate(TRUST_LEVELS)}

CONF_PUBLIC = "PUBLIC"
CONF_AUTHENTICATED = "AUTHENTICATED"
CONF_PRIVATE = "PRIVATE"
_CONFIDENTIALITY = {CONF_PUBLIC, CONF_AUTHENTICATED, CONF_PRIVATE}

AUTH_NONE = "NONE"
AUTH_EXTERNAL_WRITE = "EXTERNAL_WRITE"
AUTH_SUBJECT_STATE = "SUBJECT_STATE"
_AUTHORITIES = {AUTH_NONE, AUTH_EXTERNAL_WRITE, AUTH_SUBJECT_STATE}

COST_FREE = "FREE"
COST_LOW = "LOW"
COST_PAID = "PAID"
_COSTS = {COST_FREE, COST_LOW, COST_PAID}
_COST_SCORE = {COST_FREE: 3.0, COST_LOW: 1.5, COST_PAID: 0.0}

TASK_PENDING = "PENDING"
TASK_LEASED = "LEASED"
TASK_COMPLETED = "COMPLETED"
TASK_RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
TASK_FAILED = "FAILED"
_TASK_STATUSES = {
    TASK_PENDING, TASK_LEASED, TASK_COMPLETED, TASK_RECONCILE_REQUIRED, TASK_FAILED,
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("placement timestamps require timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("placement timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _number(value: object, field: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"{field} is outside allowed range")
    return number


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)

@dataclass(frozen=True)
class PlacementContract:
    task_id: str
    capability_id: str
    confidentiality: str = CONF_PUBLIC
    authority: str = AUTH_NONE
    stateful: bool = False
    idempotent: bool = True
    replayable: bool = True
    min_cpu_cores: float = 0.0
    min_memory_gib: float = 0.0
    min_disk_gib: float = 0.0
    preferred_cost: str = COST_FREE
    required_reliability: float = 0.5
    created_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "placement task_id"))
        object.__setattr__(self, "capability_id", _text(self.capability_id, "placement capability_id"))
        if self.confidentiality not in _CONFIDENTIALITY:
            raise ValueError("unsupported placement confidentiality")
        if self.authority not in _AUTHORITIES:
            raise ValueError("unsupported placement authority")
        if self.preferred_cost not in _COSTS:
            raise ValueError("unsupported placement preferred_cost")
        for field in ("stateful", "idempotent", "replayable"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"placement {field} must be boolean")
        for field in ("min_cpu_cores", "min_memory_gib", "min_disk_gib"):
            object.__setattr__(self, field, _number(getattr(self, field), f"placement {field}"))
        object.__setattr__(
            self, "required_reliability",
            _number(self.required_reliability, "placement required_reliability", maximum=1.0),
        )
        if self.created_at is not None:
            _parse(_text(self.created_at, "placement created_at"))

    @property
    def retry_safe(self) -> bool:
        return self.idempotent or (self.replayable and self.authority == AUTH_NONE)

    @property
    def required_trust(self) -> str:
        if self.authority == AUTH_SUBJECT_STATE:
            return TRUST_AUTHORITATIVE
        if (
            self.confidentiality in {CONF_AUTHENTICATED, CONF_PRIVATE}
            or self.authority == AUTH_EXTERNAL_WRITE
            or (self.stateful and not self.replayable)
        ):
            return TRUST_TRUSTED
        return TRUST_EPHEMERAL

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityWorker:
    worker_id: str
    trust_tier: str
    capability_ids: tuple[str, ...]
    source: str = "NETWORK"
    network_agent_id: str | None = None
    network_id: str | None = None
    credential_ref: str | None = None
    cpu_cores: float | None = None
    memory_gib: float | None = None
    disk_free_gib: float | None = None
    reliability: float = 0.5
    cost_class: str = COST_FREE
    lease_expires_at: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_id", _text(self.worker_id, "worker_id"))
        if self.trust_tier not in TRUST_LEVELS:
            raise ValueError("unsupported worker trust_tier")
        caps = tuple(dict.fromkeys(_text(item, "worker capability") for item in self.capability_ids))
        if not caps:
            raise ValueError("worker requires at least one capability")
        object.__setattr__(self, "capability_ids", caps)
        object.__setattr__(self, "source", _text(self.source, "worker source").upper())
        if self.network_agent_id is not None:
            object.__setattr__(self, "network_agent_id", _text(self.network_agent_id, "network_agent_id"))
        if self.network_id is not None:
            object.__setattr__(self, "network_id", _text(self.network_id, "network_id"))
        if self.credential_ref is not None and not self.credential_ref.startswith("secret:"):
            raise ValueError("worker credential_ref must be a secret reference, never plaintext")
        for field in ("cpu_cores", "memory_gib", "disk_free_gib"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _number(value, f"worker {field}"))
        object.__setattr__(
            self, "reliability", _number(self.reliability, "worker reliability", maximum=1.0)
        )
        if self.cost_class not in _COSTS:
            raise ValueError("unsupported worker cost_class")
        if self.lease_expires_at is not None:
            _parse(_text(self.lease_expires_at, "worker lease_expires_at"))
        if self.first_seen_at is not None:
            _parse(_text(self.first_seen_at, "worker first_seen_at"))
        if self.last_seen_at is not None:
            _parse(_text(self.last_seen_at, "worker last_seen_at"))
        if type(self.enabled) is not bool:
            raise ValueError("worker enabled must be boolean")

    def is_live(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        # Network-addressed workers must be re-discovered/heartbeated before use.
        if self.network_agent_id is not None and self.lease_expires_at is None:
            return False
        return self.lease_expires_at is None or _parse(self.lease_expires_at) > now.astimezone(timezone.utc)

    def payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["capability_ids"] = list(self.capability_ids)
        return row

@dataclass(frozen=True)
class PlacementDecision:
    task_id: str
    mode: str
    worker_id: str | None
    capability_id: str
    reason: str
    required_trust: str
    considered_workers: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["considered_workers"] = list(self.considered_workers)
        return row


@dataclass(frozen=True)
class PlacementTask:
    contract: PlacementContract
    payload_ref: str | None
    status: str = TASK_PENDING
    worker_id: str | None = None
    attempts: int = 0
    lease_expires_at: str | None = None
    result_ref: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _TASK_STATUSES:
            raise ValueError("unsupported placement task status")
        if self.payload_ref is not None:
            object.__setattr__(self, "payload_ref", _text(self.payload_ref, "payload_ref"))
        if self.worker_id is not None:
            object.__setattr__(self, "worker_id", _text(self.worker_id, "task worker_id"))
        if type(self.attempts) is not int or self.attempts < 0:
            raise ValueError("placement attempts must be non-negative")
        if self.lease_expires_at is not None:
            _parse(_text(self.lease_expires_at, "task lease_expires_at"))

    def payload(self) -> dict[str, Any]:
        return {
            "contract": self.contract.payload(),
            "payload_ref": self.payload_ref,
            "status": self.status,
            "worker_id": self.worker_id,
            "attempts": self.attempts,
            "lease_expires_at": self.lease_expires_at,
            "result_ref": self.result_ref,
            "last_error": self.last_error,
        }


def _contract_from_payload(raw: Mapping[str, Any]) -> PlacementContract:
    return PlacementContract(**dict(raw))


def _task_from_payload(raw: Mapping[str, Any]) -> PlacementTask:
    data = dict(raw)
    data["contract"] = _contract_from_payload(data["contract"])
    return PlacementTask(**data)


class CapabilityWorkerRegistry:
    SCHEMA = "content-capability-workers:v1"

    def __init__(self, *, state_root: Path) -> None:
        self.root = Path(state_root) / "application" / "habitat_placement"
        self.path = self.root / "workers.json"

    def workers(self) -> list[CapabilityWorker]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if type(raw) is not dict or raw.get("schema") != self.SCHEMA or type(raw.get("workers")) is not list:
            raise ValueError("worker registry schema mismatch")
        return [CapabilityWorker(**{**row, "capability_ids": tuple(row["capability_ids"])}) for row in raw["workers"]]

    def _persist(self, workers: Sequence[CapabilityWorker]) -> None:
        _atomic_json(self.path, {
            "schema": self.SCHEMA,
            "workers": [row.payload() for row in sorted(workers, key=lambda item: item.worker_id)],
        })

    def configure(self, worker: CapabilityWorker) -> CapabilityWorker:
        rows = {row.worker_id: row for row in self.workers()}
        rows[worker.worker_id] = worker
        self._persist(list(rows.values()))
        return worker

    def by_id(self, worker_id: str) -> CapabilityWorker | None:
        return next((row for row in self.workers() if row.worker_id == worker_id), None)

    def expire(self, worker_id: str, *, now: datetime) -> CapabilityWorker | None:
        rows = {row.worker_id: row for row in self.workers()}
        current = rows.get(worker_id)
        if current is None:
            return None
        expired = replace(current, lease_expires_at=_iso(now - timedelta(seconds=1)))
        rows[worker_id] = expired
        self._persist(list(rows.values()))
        return expired

    def heartbeat(
        self, worker_id: str, *, now: datetime, lease_seconds: int = 120,
        cpu_cores: float | None = None, memory_gib: float | None = None,
        disk_free_gib: float | None = None, reliability: float | None = None,
    ) -> CapabilityWorker:
        if lease_seconds < 30:
            raise ValueError("worker lease_seconds must be at least 30")
        rows = {row.worker_id: row for row in self.workers()}
        current = rows.get(worker_id)
        if current is None:
            raise ValueError("unknown worker_id")
        updated = replace(
            current,
            lease_expires_at=_iso(now + timedelta(seconds=lease_seconds)),
            cpu_cores=current.cpu_cores if cpu_cores is None else float(cpu_cores),
            memory_gib=current.memory_gib if memory_gib is None else float(memory_gib),
            disk_free_gib=current.disk_free_gib if disk_free_gib is None else float(disk_free_gib),
            reliability=current.reliability if reliability is None else float(reliability),
            last_seen_at=_iso(now),
            first_seen_at=current.first_seen_at or _iso(now),
        )
        rows[worker_id] = updated
        self._persist(list(rows.values()))
        return updated

    def upsert_network(
        self, *, agent_id: str, network_id: str | None, capability_id: str,
        now: datetime, reliability: float = 0.5, lease_seconds: int = 120,
    ) -> CapabilityWorker:
        agent_id = _text(agent_id, "network agent_id")
        capability_id = _text(capability_id, "network capability_id")
        rows = {row.worker_id: row for row in self.workers()}
        current = next((row for row in rows.values() if row.network_agent_id == agent_id), None)
        if current is None:
            worker_id = "network:" + sha256(
                ((network_id or "") + "\0" + agent_id).encode()
            ).hexdigest()[:24]
            current = CapabilityWorker(
                worker_id=worker_id,
                trust_tier=TRUST_EPHEMERAL,
                capability_ids=(capability_id,),
                source="NETWORK",
                network_agent_id=agent_id,
                network_id=network_id,
                reliability=reliability,
                cost_class=COST_FREE,
                lease_expires_at=_iso(now + timedelta(seconds=lease_seconds)),
                first_seen_at=_iso(now),
                last_seen_at=_iso(now),
            )
        else:
            caps = tuple(dict.fromkeys((*current.capability_ids, capability_id)))
            current = replace(
                current,
                capability_ids=caps,
                network_id=network_id or current.network_id,
                reliability=max(current.reliability, reliability),
                lease_expires_at=_iso(now + timedelta(seconds=lease_seconds)),
                first_seen_at=current.first_seen_at or _iso(now),
                last_seen_at=_iso(now),
            )
        rows[current.worker_id] = current
        self._persist(list(rows.values()))
        return current


class PlacementTaskQueue:
    SCHEMA = "content-placement-tasks:v1"
    MAX_TASKS = 512

    def __init__(self, *, state_root: Path) -> None:
        self.root = Path(state_root) / "application" / "habitat_placement"
        self.path = self.root / "tasks.json"

    def tasks(self) -> list[PlacementTask]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if type(raw) is not dict or raw.get("schema") != self.SCHEMA or type(raw.get("tasks")) is not list:
            raise ValueError("placement task queue schema mismatch")
        return [_task_from_payload(row) for row in raw["tasks"]]

    def _persist(self, tasks: Sequence[PlacementTask]) -> None:
        rows = list(tasks)
        active = [
            row for row in rows
            if row.status not in {TASK_COMPLETED, TASK_FAILED}
        ]
        terminal = [
            row for row in rows
            if row.status in {TASK_COMPLETED, TASK_FAILED}
        ]
        slots = max(0, self.MAX_TASKS - len(active))
        terminal.sort(key=lambda row: (row.contract.created_at or "", row.contract.task_id))
        bounded = active + terminal[-slots:] if slots else active
        _atomic_json(self.path, {
            "schema": self.SCHEMA,
            "tasks": [row.payload() for row in sorted(bounded, key=lambda item: item.contract.task_id)],
        })

    def enqueue(self, contract: PlacementContract, *, payload_ref: str | None = None) -> PlacementTask:
        rows = {row.contract.task_id: row for row in self.tasks()}
        prior = rows.get(contract.task_id)
        if prior is not None:
            if prior.contract != contract or prior.payload_ref != payload_ref:
                raise ValueError("placement task_id already exists with different contract")
            return prior
        task = PlacementTask(contract=contract, payload_ref=payload_ref)
        rows[contract.task_id] = task
        self._persist(list(rows.values()))
        return task

    def claim(
        self, task_id: str, *, worker_id: str, now: datetime, lease_seconds: int = 120,
    ) -> PlacementTask:
        if lease_seconds < 30:
            raise ValueError("task lease_seconds must be at least 30")
        rows = {row.contract.task_id: row for row in self.tasks()}
        current = rows.get(task_id)
        if current is None:
            raise ValueError("unknown placement task")
        if current.status != TASK_PENDING:
            raise ValueError("only pending placement tasks can be claimed")
        claimed = replace(
            current,
            status=TASK_LEASED,
            worker_id=_text(worker_id, "placement worker_id"),
            attempts=current.attempts + 1,
            lease_expires_at=_iso(now + timedelta(seconds=lease_seconds)),
            last_error=None,
        )
        rows[task_id] = claimed
        self._persist(list(rows.values()))
        return claimed

    def complete(self, task_id: str, *, result_ref: str) -> PlacementTask:
        rows = {row.contract.task_id: row for row in self.tasks()}
        current = rows.get(task_id)
        if current is None or current.status != TASK_LEASED:
            raise ValueError("only leased placement tasks can complete")
        completed = replace(
            current, status=TASK_COMPLETED, lease_expires_at=None,
            result_ref=_text(result_ref, "placement result_ref"), last_error=None,
        )
        rows[task_id] = completed
        self._persist(list(rows.values()))
        return completed

    def record_failure(
        self, task_id: str, *, error_code: str, after_dispatch: bool,
    ) -> PlacementTask:
        rows = {row.contract.task_id: row for row in self.tasks()}
        current = rows.get(task_id)
        if current is None or current.status != TASK_LEASED:
            raise ValueError("only leased placement tasks can fail")
        safe_retry = (not after_dispatch) or current.contract.retry_safe
        failed = replace(
            current,
            status=TASK_PENDING if safe_retry else TASK_RECONCILE_REQUIRED,
            worker_id=None if safe_retry else current.worker_id,
            lease_expires_at=None,
            last_error=_text(error_code, "placement error_code"),
        )
        rows[task_id] = failed
        self._persist(list(rows.values()))
        return failed

    def reconcile_expired(self, *, now: datetime) -> dict[str, Any]:
        rows = {row.contract.task_id: row for row in self.tasks()}
        retried: list[str] = []
        reconcile: list[str] = []
        for task_id, current in list(rows.items()):
            if current.status != TASK_LEASED or current.lease_expires_at is None:
                continue
            if _parse(current.lease_expires_at) > now.astimezone(timezone.utc):
                continue
            if current.contract.retry_safe:
                rows[task_id] = replace(
                    current, status=TASK_PENDING, worker_id=None,
                    lease_expires_at=None, last_error="WORKER_LEASE_EXPIRED",
                )
                retried.append(task_id)
            else:
                rows[task_id] = replace(
                    current, status=TASK_RECONCILE_REQUIRED,
                    lease_expires_at=None, last_error="WORKER_LEASE_EXPIRED",
                )
                reconcile.append(task_id)
        if retried or reconcile:
            self._persist(list(rows.values()))
        return {
            "retried_task_ids": retried,
            "reconcile_required_task_ids": reconcile,
        }


class HabitatPlacementController:
    """Select disposable capability workers without moving subject authority."""

    def __init__(self, *, state_root: Path, network_service: Any | None = None) -> None:
        self.state_root = Path(state_root)
        self.registry = CapabilityWorkerRegistry(state_root=self.state_root)
        self.queue = PlacementTaskQueue(state_root=self.state_root)
        self.network_service = network_service
        self.learning: Any | None = None
        self.root = self.state_root / "application" / "habitat_placement"

    @staticmethod
    def _resource_ok(worker: CapabilityWorker, contract: PlacementContract) -> bool:
        checks = (
            (contract.min_cpu_cores, worker.cpu_cores),
            (contract.min_memory_gib, worker.memory_gib),
            (contract.min_disk_gib, worker.disk_free_gib),
        )
        for required, available in checks:
            if required <= 0:
                continue
            if available is None or available < required:
                return False
        return True

    @staticmethod
    def _score(worker: CapabilityWorker, contract: PlacementContract) -> float:
        score = worker.reliability * 10.0 + _COST_SCORE[worker.cost_class]
        # Use the least privileged worker that is sufficient for the contract.
        score -= 0.15 * _TRUST_RANK[worker.trust_tier]
        if contract.preferred_cost == worker.cost_class:
            score += 1.0
        return score

    def choose(
        self, contract: PlacementContract, *, now: datetime,
        resource_pressure: str = "NORMAL",
    ) -> PlacementDecision:
        if contract.authority == AUTH_SUBJECT_STATE:
            return PlacementDecision(
                task_id=contract.task_id, mode="LOCAL_CORE", worker_id=None,
                capability_id=contract.capability_id,
                reason="subject authority is pinned to the core; habitat migration is a separate governed action",
                required_trust=TRUST_AUTHORITATIVE,
            )
        considered: list[str] = []
        eligible: list[CapabilityWorker] = []
        required_rank = _TRUST_RANK[contract.required_trust]
        for worker in self.registry.workers():
            if contract.capability_id not in worker.capability_ids:
                continue
            considered.append(worker.worker_id)
            if not worker.is_live(now):
                continue
            if _TRUST_RANK[worker.trust_tier] < required_rank:
                continue
            if worker.reliability < contract.required_reliability:
                continue
            if not self._resource_ok(worker, contract):
                continue
            eligible.append(worker)
        if not eligible:
            return PlacementDecision(
                task_id=contract.task_id, mode="LOCAL_CORE", worker_id=None,
                capability_id=contract.capability_id,
                reason="no eligible remote worker; keep work on the subject core",
                required_trust=contract.required_trust,
                considered_workers=tuple(considered),
            )
        eligible.sort(key=lambda row: (-self._score(row, contract), row.worker_id))
        selected = eligible[0]
        if resource_pressure == "NORMAL" and contract.preferred_cost != COST_FREE:
            return PlacementDecision(
                task_id=contract.task_id, mode="LOCAL_CORE", worker_id=None,
                capability_id=contract.capability_id,
                reason="host pressure is normal and the contract does not prefer free offload",
                required_trust=contract.required_trust,
                considered_workers=tuple(considered),
            )
        return PlacementDecision(
            task_id=contract.task_id, mode="REMOTE_WORKER", worker_id=selected.worker_id,
            capability_id=contract.capability_id,
            reason="eligible replaceable worker selected without moving subject authority",
            required_trust=contract.required_trust,
            considered_workers=tuple(considered),
        )

    def sync_network(
        self, *, capability_ids: Sequence[str], now: datetime, lease_seconds: int = 120,
    ) -> dict[str, Any]:
        service = self.network_service
        if service is None or not getattr(service, "joined", False):
            return {"status": "NETWORK_UNAVAILABLE", "discovered_workers": 0}
        discovered: set[str] = set()
        failures: list[dict[str, str]] = []
        for capability_id in tuple(dict.fromkeys(str(item) for item in capability_ids if str(item).strip())):
            try:
                rows = service.discover(capability_id, include_reputation=True)
            except Exception as exc:  # network discovery is opportunistic
                failures.append({"capability_id": capability_id, "error_type": type(exc).__name__})
                continue
            for raw in rows:
                agent_id = str(raw.get("agent_id") or "").strip()
                if not agent_id or agent_id == getattr(service, "agent_id", None):
                    continue
                reputation = raw.get("reputation") or {}
                reliability = 0.5
                if isinstance(reputation, Mapping):
                    for key in ("score", "success_rate", "pass_rate"):
                        value = reputation.get(key)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            reliability = max(0.0, min(1.0, float(value)))
                            break
                worker = self.registry.upsert_network(
                    agent_id=agent_id,
                    network_id=str(raw.get("network_id") or "") or None,
                    capability_id=capability_id,
                    now=now,
                    reliability=reliability,
                    lease_seconds=lease_seconds,
                )
                discovered.add(worker.worker_id)
        return {
            "status": "SYNCED" if not failures else "PARTIAL",
            "discovered_workers": len(discovered),
            "worker_ids": sorted(discovered),
            "failures": failures,
        }

    def review(
        self, *, now: datetime, resource_pressure: str,
        capability_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        network = self.sync_network(capability_ids=capability_ids, now=now) if capability_ids else {
            "status": "NO_CAPABILITIES_REQUESTED", "discovered_workers": 0,
        }
        reconciliation = self.queue.reconcile_expired(now=now)
        workers = self.registry.workers()
        live = [row for row in workers if row.is_live(now)]
        result = {
            "status": "READY",
            "resource_pressure": resource_pressure,
            "network": network,
            "worker_count": len(workers),
            "live_worker_count": len(live),
            "offload_status": "AVAILABLE" if live else "UNAVAILABLE",
            "trust_counts": {
                tier: sum(1 for row in live if row.trust_tier == tier)
                for tier in TRUST_LEVELS
            },
            "pending_task_count": sum(1 for row in self.queue.tasks() if row.status == TASK_PENDING),
            "leased_task_count": sum(1 for row in self.queue.tasks() if row.status == TASK_LEASED),
            "reconcile_required_count": sum(
                1 for row in self.queue.tasks() if row.status == TASK_RECONCILE_REQUIRED
            ),
            "lease_reconciliation": reconciliation,
        }
        _atomic_json(self.root / "review-latest.json", result)
        return result

    def dispatch_task(
        self, task_id: str, *, now: datetime, resource_pressure: str,
        payload_loader: Callable[[PlacementTask], Mapping[str, Any]],
        local_executor: Callable[[PlacementTask, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        task = next((row for row in self.queue.tasks() if row.contract.task_id == task_id), None)
        if task is None:
            raise ValueError("unknown placement task")
        if task.status != TASK_PENDING:
            return {"status": task.status, "task_id": task.contract.task_id}
        decision = self.choose(task.contract, now=now, resource_pressure=resource_pressure)
        if decision.mode == "LOCAL_CORE":
            if local_executor is None:
                return {"status": "LOCAL_EXECUTOR_REQUIRED", "decision": decision.payload()}
            claimed = self.queue.claim(
                task.contract.task_id, worker_id="subject-core", now=now, lease_seconds=lease_seconds
            )
            try:
                result = dict(local_executor(claimed, dict(payload_loader(claimed))))
            except Exception as exc:
                failed = self.queue.record_failure(
                    task.contract.task_id, error_code=type(exc).__name__, after_dispatch=True,
                )
                return {"status": failed.status, "decision": decision.payload()}
            digest = sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
            self.queue.complete(task.contract.task_id, result_ref="local-result:sha256:" + digest)
            return {"status": "COMPLETED", "decision": decision.payload(), "result": result}

        worker = self.registry.by_id(str(decision.worker_id))
        if worker is None or not worker.network_agent_id:
            return {"status": "WORKER_EXECUTOR_REQUIRED", "decision": decision.payload()}
        service = self.network_service
        if service is None:
            return {"status": "NETWORK_UNAVAILABLE", "decision": decision.payload()}
        claimed = self.queue.claim(
            task.contract.task_id, worker_id=worker.worker_id, now=now, lease_seconds=lease_seconds
        )
        payload = dict(payload_loader(claimed))
        try:
            result = service.delegate_remote(
                agent_id=worker.network_agent_id,
                capability=task.contract.capability_id,
                payload=payload,
                objective=f"Execute placement task {task.contract.task_id}",
                idempotency_key=task.contract.task_id if task.contract.idempotent else None,
                placement_metadata={
                    "task_id": task.contract.task_id,
                    "confidentiality": task.contract.confidentiality,
                    "authority": task.contract.authority,
                    "replayable": task.contract.replayable,
                },
            )
        except Exception as exc:
            failed = self.queue.record_failure(
                task.contract.task_id, error_code=type(exc).__name__, after_dispatch=True,
            )
            return {"status": failed.status, "decision": decision.payload(), "error_type": type(exc).__name__}

        metadata = result.get("metadata") if isinstance(result, Mapping) else {}
        status = result.get("status") if isinstance(result, Mapping) else {}
        delivery = metadata.get("delivery") if isinstance(metadata, Mapping) else {}
        remote_payload = metadata.get("result") if isinstance(metadata, Mapping) else None
        state = str(status.get("state") or "").lower() if isinstance(status, Mapping) else ""
        if isinstance(delivery, Mapping) and delivery.get("artifact_manifest_hash"):
            result_ref = "network-artifact:" + str(delivery["artifact_manifest_hash"])
            self.queue.complete(task.contract.task_id, result_ref=result_ref)
            return {
                "status": "COMPLETED",
                "decision": decision.payload(),
                "result_ref": result_ref,
                "result": dict(remote_payload) if isinstance(remote_payload, Mapping) else None,
            }
        if state in {"completed", "success", "succeeded"}:
            digest = sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
            self.queue.complete(task.contract.task_id, result_ref="network-result:sha256:" + digest)
            return {
                "status": "COMPLETED",
                "decision": decision.payload(),
                "result": dict(remote_payload) if isinstance(remote_payload, Mapping) else dict(result),
            }
        # Unknown remote outcome remains leased. Expiry later retries only retry-safe work.
        return {"status": "DELEGATED_UNCONFIRMED", "decision": decision.payload()}

    def dispatch_next(
        self, *, now: datetime, resource_pressure: str,
        payload_loader: Callable[[PlacementTask], Mapping[str, Any]],
        local_executor: Callable[[PlacementTask, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        pending = [row for row in self.queue.tasks() if row.status == TASK_PENDING]
        if not pending:
            return {"status": "NO_PENDING_TASK"}
        pending.sort(key=lambda row: row.contract.task_id)
        return self.dispatch_task(
            pending[0].contract.task_id,
            now=now,
            resource_pressure=resource_pressure,
            payload_loader=payload_loader,
            local_executor=local_executor,
            lease_seconds=lease_seconds,
        )


__all__ = [
    "AUTH_EXTERNAL_WRITE", "AUTH_NONE", "AUTH_SUBJECT_STATE",
    "CONF_AUTHENTICATED", "CONF_PRIVATE", "CONF_PUBLIC",
    "COST_FREE", "COST_LOW", "COST_PAID",
    "TRUST_AUTHORITATIVE", "TRUST_EPHEMERAL", "TRUST_TRUSTED",
    "TASK_COMPLETED", "TASK_FAILED", "TASK_LEASED", "TASK_PENDING", "TASK_RECONCILE_REQUIRED",
    "PlacementContract", "CapabilityWorker", "PlacementDecision", "PlacementTask",
    "CapabilityWorkerRegistry", "PlacementTaskQueue", "HabitatPlacementController",
]
