import asyncio
from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.habitat_placement import (
    CapabilityWorker,
    HabitatPlacementController,
    TASK_PENDING,
    TRUST_EPHEMERAL,
)
from wechat_traffic_agent.placement_web import PlacementAwareWebSearch


NOW = datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)


class _LocalSearch:
    def __init__(self):
        self.calls = 0

    async def search(self, *, query, now):
        self.calls += 1
        return [{
            "title": "local",
            "summary": "local evidence " * 30,
            "url": "https://local.example/",
            "source": "runtime:web.fetch:local",
            "source_revision": "local-rev",
            "evidence_refs": ["local-ref"],
            "observed_at": now.isoformat(),
        }]


class _RemoteService:
    joined = True
    agent_id = "content.cai.001"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.delegations = 0

    def discover(self, capability, include_reputation=False):
        return [{
            "agent_id": "worker.public.1",
            "network_id": "network.cai",
            "endpoint": "https://not-persisted.example/a2a",
            "reputation": {"success_rate": 0.95},
        }]

    def delegate_remote(self, **kwargs):
        self.delegations += 1
        if self.fail:
            raise TimeoutError("remote response lost")
        return {
            "status": {"state": "completed"},
            "metadata": {
                "delivery": {"artifact_manifest_hash": "sha256:remote"},
                "result": {
                    "ok": True,
                    "evidence": [{
                        "title": "remote",
                        "summary": "remote evidence " * 30,
                        "url": "https://remote.example/",
                        "source": "runtime:web.fetch:remote",
                        "source_revision": "remote-rev",
                        "evidence_refs": ["remote-ref"],
                        "observed_at": NOW.isoformat(),
                    }],
                },
            },
        }


def _controller(tmp_path, service):
    controller = HabitatPlacementController(state_root=tmp_path, network_service=service)
    controller.registry.configure(CapabilityWorker(
        worker_id="worker-a",
        trust_tier=TRUST_EPHEMERAL,
        capability_ids=("content.research",),
        network_agent_id="worker.public.1",
        network_id="network.cai",
        reliability=0.95,
        cost_class="FREE",
        lease_expires_at=(NOW + timedelta(minutes=5)).isoformat(),
    ))
    return controller


def test_public_web_research_uses_remote_worker_when_available(tmp_path):
    local = _LocalSearch()
    service = _RemoteService()
    search = PlacementAwareWebSearch(
        local_search=local,
        placement=_controller(tmp_path, service),
    )

    rows = asyncio.run(search.search(query="bitcoin market structure", now=NOW))

    assert rows[0]["title"] == "remote"
    assert local.calls == 0
    assert service.delegations == 1


def test_public_web_research_falls_back_local_when_network_unavailable(tmp_path):
    class Offline(_RemoteService):
        joined = False

    local = _LocalSearch()
    search = PlacementAwareWebSearch(
        local_search=local,
        placement=HabitatPlacementController(state_root=tmp_path, network_service=Offline()),
    )

    rows = asyncio.run(search.search(query="bitcoin market structure", now=NOW))

    assert rows[0]["title"] == "local"
    assert local.calls == 1


def test_public_web_remote_timeout_safely_falls_back_and_requeues(tmp_path):
    local = _LocalSearch()
    service = _RemoteService(fail=True)
    controller = _controller(tmp_path, service)
    search = PlacementAwareWebSearch(local_search=local, placement=controller)

    rows = asyncio.run(search.search(query="bitcoin market structure", now=NOW))

    assert rows[0]["title"] == "local"
    assert local.calls == 1
    assert service.delegations == 1
    tasks = controller.queue.tasks()
    assert len(tasks) == 1
    assert tasks[0].status == TASK_PENDING
