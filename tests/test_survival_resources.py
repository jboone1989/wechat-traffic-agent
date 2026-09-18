from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.survival_resources import SurvivalResourceManager


NOW = datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc)


def test_observe_builds_unified_local_resource_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=40 * 1024**3, free=60 * 1024**3),
    )
    monkeypatch.setattr(
        SurvivalResourceManager, "_memory",
        staticmethod(lambda: (8 * 1024**3, 5 * 1024**3)),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda: 0.5))
    manager = SurvivalResourceManager(tmp_path)
    observed = manager.observe(now=NOW)

    assert observed["pressure"] == "NORMAL"
    domains = {row["domain"] for row in observed["resources"]}
    assert {"STORAGE", "COMPUTE", "HOSTING", "SERVICE"} <= domains
    assert observed["needs"] == []
    assert manager.services()[0]["service_id"] == "content-agent-core"


def test_pressure_self_authorizes_safe_local_repair_and_remote_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=96 * 1024**3, free=4 * 1024**3),
    )
    monkeypatch.setattr(
        SurvivalResourceManager, "_memory",
        staticmethod(lambda: (8 * 1024**3, 4 * 1024**3)),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda: 0.5))
    stale = tmp_path / "application" / "scratch.tmp"
    stale.parent.mkdir(parents=True)
    stale.write_text("x" * 32)
    os.utime(stale, (NOW.timestamp() - 90000, NOW.timestamp() - 90000))

    manager = SurvivalResourceManager(tmp_path)
    observed = manager.observe(now=NOW)
    needs = {row["action"]: row for row in observed["needs"]}
    assert needs["RECLAIM_TRANSIENT_STORAGE"]["status"] == "APPROVED"
    assert needs["RECLAIM_TRANSIENT_STORAGE"]["authorization"] == "AUTONOMOUS_SAFE_POLICY"
    assert needs["DISCOVER_REMOTE_MACHINE"]["status"] == "APPROVED"

    result = manager.execute_approved(now=NOW)
    assert not stale.exists()
    assert any(row["action"] == "RECLAIM_TRANSIENT_STORAGE" for row in result["results"])
    assert any(row["status"] == "AWAITING_RESOURCE" for row in result["results"])


def test_operator_decision_required_for_non_core_offload_need(tmp_path, monkeypatch):
    manager = SurvivalResourceManager(tmp_path)
    manager.needs_path.parent.mkdir(parents=True, exist_ok=True)
    manager.needs_path.write_text(json.dumps([{
        "need_id": "survival-need:offload",
        "domain": "SERVICE",
        "action": "OFFLOAD_NON_CORE_SERVICE",
        "status": "PENDING",
        "authorization": "OPERATOR_REQUIRED",
        "created_at": NOW.isoformat(),
        "reason": "pressure",
        "urgency": "NORMAL",
    }]))
    row = manager.decide_need("survival-need:offload", approved=True, now=NOW)
    assert row["status"] == "APPROVED"
    with pytest.raises(ValueError, match="already approved"):
        manager.decide_need("survival-need:offload", approved=False, now=NOW)


def test_remote_host_must_verify_signed_deploy_before_it_can_receive_work(tmp_path, monkeypatch):
    from agent_network.crypto import Identity

    manager = SurvivalResourceManager(tmp_path)
    remote_identity = Identity.generate("remote.edge.1")
    host = manager.register_remote_host(
        name="edge-one",
        health_url="https://edge.example.test/health",
        control_url="https://edge.example.test/v1/workloads",
        capacity={"cpu_count": 2, "memory_gib": 4},
        reliability="BEST_EFFORT",
        now=NOW,
    )
    with pytest.raises(ValueError, match="active verified"):
        manager.plan_offload("website-static", host["host_id"], now=NOW)

    def remote_call(url, *, method, payload=None, timeout):
        if method == "GET":
            return {
                "status":"HEALTHY",
                "agent_id":remote_identity.agent_id,
                "key_id":remote_identity.key_id,
                "public_key":remote_identity.public_key,
                "capabilities":["survival.workload.deploy.v1"],
            }
        assert payload and payload["type"] == "survival.workload.deploy.v1"
        receipt = {
            "type":"survival.workload.deploy.receipt.v1",
            "status":"DEPLOYED",
            "action_id":payload["action_id"],
            "request_hash":payload["object_hash"],
            "deployment_id":"deployment:test-1",
            "agent_id":remote_identity.agent_id,
            "key_id":remote_identity.key_id,
        }
        receipt["signature"] = remote_identity.sign_object(receipt)
        return receipt

    monkeypatch.setattr(manager, "_http_json", remote_call)
    verified = manager.verify_remote_host(host["host_id"], now=NOW)
    assert verified["status"] == "ACTIVE"
    assert verified["deployment_capable"] is True
    plan = manager.plan_offload("website-static", host["host_id"], now=NOW)
    assert plan["status"] == "PLANNED"
    deployed = manager.execute_offload(plan["action_id"], now=NOW)
    assert deployed["status"] == "DEPLOYED"
    service = next(row for row in manager.services() if row["service_id"] == "website-static")
    assert service["placement"] == host["host_id"]
    with pytest.raises(ValueError, match="primary stateful"):
        manager.plan_offload("content-agent-core", host["host_id"], now=NOW)


def test_remote_host_url_rejects_non_https_and_loopback(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    with pytest.raises(ValueError, match="HTTPS"):
        manager.register_remote_host(
            name="bad", health_url="http://example.com/health",
            capacity={}, reliability="EXPERIMENTAL", now=NOW,
        )
    with pytest.raises(ValueError, match="local/link-local"):
        manager.register_remote_host(
            name="bad-local", health_url="https://127.0.0.1/health",
            capacity={}, reliability="EXPERIMENTAL", now=NOW,
        )
