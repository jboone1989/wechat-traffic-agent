from datetime import datetime, timedelta, timezone
import json

import pytest

from wechat_traffic_agent.habitat_placement import (
    AUTH_EXTERNAL_WRITE,
    AUTH_NONE,
    AUTH_SUBJECT_STATE,
    CONF_AUTHENTICATED,
    CONF_PRIVATE,
    CONF_PUBLIC,
    COST_FREE,
    CapabilityWorker,
    CapabilityWorkerRegistry,
    HabitatPlacementController,
    PlacementContract,
    PlacementTaskQueue,
    TASK_COMPLETED,
    TASK_LEASED,
    TASK_PENDING,
    TASK_RECONCILE_REQUIRED,
    TRUST_AUTHORITATIVE,
    TRUST_EPHEMERAL,
    TRUST_TRUSTED,
)


NOW = datetime(2026, 9, 18, 2, 30, tzinfo=timezone.utc)


def _worker(
    worker_id="worker-a",
    *,
    trust=TRUST_EPHEMERAL,
    capability="web.fetch",
    network_agent_id="agent-a",
    reliability=0.9,
    memory=2.0,
    disk=10.0,
    lease_seconds=120,
):
    return CapabilityWorker(
        worker_id=worker_id,
        trust_tier=trust,
        capability_ids=(capability,),
        network_agent_id=network_agent_id,
        network_id="network.cai",
        cpu_cores=1.0,
        memory_gib=memory,
        disk_free_gib=disk,
        reliability=reliability,
        cost_class=COST_FREE,
        lease_expires_at=(NOW + timedelta(seconds=lease_seconds)).isoformat(),
    )


def test_public_replayable_work_can_use_ephemeral_worker(tmp_path):
    controller = HabitatPlacementController(state_root=tmp_path)
    controller.registry.configure(_worker())
    contract = PlacementContract(
        task_id="crawl-1",
        capability_id="web.fetch",
        confidentiality=CONF_PUBLIC,
        authority=AUTH_NONE,
        stateful=False,
        idempotent=True,
        replayable=True,
        min_memory_gib=0.5,
        required_reliability=0.8,
    )

    decision = controller.choose(contract, now=NOW, resource_pressure="WATCH")

    assert decision.mode == "REMOTE_WORKER"
    assert decision.worker_id == "worker-a"
    assert decision.required_trust == TRUST_EPHEMERAL


def test_authenticated_or_external_write_work_requires_trusted_worker(tmp_path):
    controller = HabitatPlacementController(state_root=tmp_path)
    controller.registry.configure(_worker(worker_id="ephemeral", capability="x.publish"))
    contract = PlacementContract(
        task_id="x-post-1",
        capability_id="x.publish",
        confidentiality=CONF_AUTHENTICATED,
        authority=AUTH_EXTERNAL_WRITE,
        stateful=True,
        idempotent=False,
        replayable=False,
        required_reliability=0.8,
    )

    local = controller.choose(contract, now=NOW, resource_pressure="CRITICAL")
    assert local.mode == "LOCAL_CORE"
    assert local.required_trust == TRUST_TRUSTED

    controller.registry.configure(
        _worker(worker_id="trusted", trust=TRUST_TRUSTED, capability="x.publish", network_agent_id="trusted-agent")
    )
    remote = controller.choose(contract, now=NOW, resource_pressure="CRITICAL")
    assert remote.mode == "REMOTE_WORKER"
    assert remote.worker_id == "trusted"


def test_private_read_can_use_trusted_but_subject_state_is_pinned_to_core(tmp_path):
    controller = HabitatPlacementController(state_root=tmp_path)
    controller.registry.configure(
        _worker(worker_id="trusted", trust=TRUST_TRUSTED, capability="private.analyze")
    )
    private = PlacementContract(
        task_id="private-1",
        capability_id="private.analyze",
        confidentiality=CONF_PRIVATE,
        authority=AUTH_NONE,
        required_reliability=0.8,
    )
    assert controller.choose(private, now=NOW, resource_pressure="WATCH").worker_id == "trusted"

    core = PlacementContract(
        task_id="mind-1",
        capability_id="mind.persist",
        confidentiality=CONF_PRIVATE,
        authority=AUTH_SUBJECT_STATE,
        stateful=True,
        idempotent=False,
        replayable=False,
    )
    decision = controller.choose(core, now=NOW, resource_pressure="CRITICAL")
    assert decision.mode == "LOCAL_CORE"
    assert decision.required_trust == TRUST_AUTHORITATIVE
    assert "pinned to the core" in decision.reason


def test_worker_must_meet_resource_and_reliability_contract(tmp_path):
    controller = HabitatPlacementController(state_root=tmp_path)
    controller.registry.configure(_worker(memory=0.5, disk=1.0, reliability=0.6))
    contract = PlacementContract(
        task_id="render-1",
        capability_id="web.fetch",
        min_memory_gib=1.0,
        min_disk_gib=2.0,
        required_reliability=0.8,
    )

    decision = controller.choose(contract, now=NOW, resource_pressure="WATCH")
    assert decision.mode == "LOCAL_CORE"


def test_worker_credentials_must_be_secret_references(tmp_path):
    registry = CapabilityWorkerRegistry(state_root=tmp_path)
    with pytest.raises(ValueError, match="secret reference"):
        registry.configure(CapabilityWorker(
            worker_id="bad",
            trust_tier=TRUST_TRUSTED,
            capability_ids=("x.publish",),
            credential_ref="plaintext-token",
        ))


def test_network_discovery_never_persists_endpoint_and_keeps_configured_trust(tmp_path):
    registry = CapabilityWorkerRegistry(state_root=tmp_path)
    registry.configure(CapabilityWorker(
        worker_id="trusted-network",
        trust_tier=TRUST_TRUSTED,
        capability_ids=("web.fetch",),
        network_agent_id="agent-a",
        network_id="network.cai",
        credential_ref="secret:agent-a",
        reliability=0.9,
    ))
    updated = registry.upsert_network(
        agent_id="agent-a",
        network_id="network.cai",
        capability_id="content.research",
        now=NOW,
        reliability=0.95,
    )

    assert updated.trust_tier == TRUST_TRUSTED
    assert set(updated.capability_ids) == {"web.fetch", "content.research"}
    raw = (tmp_path / "application/habitat_placement/workers.json").read_text()
    assert "endpoint" not in raw
    assert "secret:agent-a" in raw


def test_expired_retry_safe_task_returns_to_queue(tmp_path):
    queue = PlacementTaskQueue(state_root=tmp_path)
    contract = PlacementContract(
        task_id="crawl-1",
        capability_id="web.fetch",
        idempotent=True,
        replayable=True,
    )
    queue.enqueue(contract, payload_ref="public-input:1")
    queue.claim("crawl-1", worker_id="worker-a", now=NOW, lease_seconds=30)

    result = queue.reconcile_expired(now=NOW + timedelta(seconds=31))
    task = queue.tasks()[0]

    assert result["retried_task_ids"] == ["crawl-1"]
    assert task.status == TASK_PENDING
    assert task.worker_id is None


def test_expired_non_idempotent_external_write_requires_reconciliation(tmp_path):
    queue = PlacementTaskQueue(state_root=tmp_path)
    contract = PlacementContract(
        task_id="post-1",
        capability_id="x.publish",
        confidentiality=CONF_AUTHENTICATED,
        authority=AUTH_EXTERNAL_WRITE,
        stateful=True,
        idempotent=False,
        replayable=False,
    )
    queue.enqueue(contract, payload_ref="protected-input:1")
    queue.claim("post-1", worker_id="trusted-a", now=NOW, lease_seconds=30)

    result = queue.reconcile_expired(now=NOW + timedelta(seconds=31))
    task = queue.tasks()[0]

    assert result["reconcile_required_task_ids"] == ["post-1"]
    assert task.status == TASK_RECONCILE_REQUIRED
    assert task.worker_id == "trusted-a"


class _DiscoveryService:
    joined = True
    agent_id = "content.cai.001"

    def discover(self, capability, include_reputation=False):
        assert include_reputation is True
        return [
            {
                "agent_id": "worker.public.1",
                "network_id": "network.cai",
                "endpoint": "https://should-not-persist.example/a2a",
                "reputation": {"success_rate": 0.91},
            },
            {
                "agent_id": self.agent_id,
                "network_id": "network.cai",
                "endpoint": "https://self.example/a2a",
            },
        ]


def test_network_sync_creates_short_lease_ephemeral_workers_without_addresses(tmp_path):
    controller = HabitatPlacementController(
        state_root=tmp_path,
        network_service=_DiscoveryService(),
    )

    result = controller.sync_network(
        capability_ids=("content.research",),
        now=NOW,
        lease_seconds=60,
    )

    assert result["status"] == "SYNCED"
    assert result["discovered_workers"] == 1
    workers = controller.registry.workers()
    assert len(workers) == 1
    assert workers[0].trust_tier == TRUST_EPHEMERAL
    assert workers[0].network_agent_id == "worker.public.1"
    assert workers[0].reliability == pytest.approx(0.91)
    persisted = json.loads((tmp_path / "application/habitat_placement/workers.json").read_text())
    assert "endpoint" not in json.dumps(persisted)


class _DelegateService:
    joined = True
    agent_id = "content.cai.001"

    def discover(self, capability, include_reputation=False):
        return []

    def delegate_remote(self, **kwargs):
        return {
            "status": {"state": "completed"},
            "metadata": {"delivery": {"artifact_manifest_hash": "sha256:abc"}},
        }


def test_dispatch_closes_public_remote_task_with_artifact_evidence(tmp_path):
    service = _DelegateService()
    controller = HabitatPlacementController(state_root=tmp_path, network_service=service)
    controller.registry.configure(_worker())
    contract = PlacementContract(task_id="crawl-1", capability_id="web.fetch")
    controller.queue.enqueue(contract, payload_ref="public-input:1")

    result = controller.dispatch_next(
        now=NOW,
        resource_pressure="WATCH",
        payload_loader=lambda task: {"url": "https://example.test/"},
    )

    assert result["status"] == "COMPLETED"
    task = controller.queue.tasks()[0]
    assert task.status == TASK_COMPLETED
    assert task.result_ref == "network-artifact:sha256:abc"


class _AmbiguousWriteService(_DelegateService):
    def delegate_remote(self, **kwargs):
        raise TimeoutError("response lost after submit")


def test_ambiguous_external_write_failure_is_never_blindly_retried(tmp_path):
    service = _AmbiguousWriteService()
    controller = HabitatPlacementController(state_root=tmp_path, network_service=service)
    controller.registry.configure(
        _worker(worker_id="trusted", trust=TRUST_TRUSTED, capability="x.publish", network_agent_id="trusted-agent")
    )
    contract = PlacementContract(
        task_id="post-1",
        capability_id="x.publish",
        confidentiality=CONF_AUTHENTICATED,
        authority=AUTH_EXTERNAL_WRITE,
        stateful=True,
        idempotent=False,
        replayable=False,
    )
    controller.queue.enqueue(contract, payload_ref="secret-payload-ref:1")

    result = controller.dispatch_next(
        now=NOW,
        resource_pressure="CRITICAL",
        payload_loader=lambda task: {"text": "hello"},
    )

    assert result["status"] == TASK_RECONCILE_REQUIRED
    assert controller.queue.tasks()[0].status == TASK_RECONCILE_REQUIRED


def test_normal_pressure_keeps_non_free_preferred_work_local(tmp_path):
    controller = HabitatPlacementController(state_root=tmp_path)
    worker = _worker()
    controller.registry.configure(worker)
    contract = PlacementContract(
        task_id="latency-1",
        capability_id="web.fetch",
        preferred_cost="LOW",
    )

    decision = controller.choose(contract, now=NOW, resource_pressure="NORMAL")
    assert decision.mode == "LOCAL_CORE"
