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
    assert len(observed["needs"]) == 1
    remote_need = observed["needs"][0]
    assert remote_need["domain"] == "REMOTE_MACHINE"
    assert remote_need["action"] == "DISCOVER_REMOTE_MACHINE"
    assert remote_need["status"] == "APPROVED"
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

    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hello edge</h1>", encoding="utf-8")
    (site / "about.html").write_text("about", encoding="utf-8")
    monkeypatch.setenv("CONTENT_WEB_SITE_ROOT", str(site))

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
                "host_instance_id":"host-instance:remote-test-body",
                "agent_id":remote_identity.agent_id,
                "key_id":remote_identity.key_id,
                "public_key":remote_identity.public_key,
                "capabilities":["survival.workload.deploy.v1"],
                "deployment_profiles":["content.website-static.v1"],
            }
        assert payload and payload["type"] == "survival.workload.deploy.v1"
        artifact = payload["artifact"]
        assert artifact["media_type"] == "application/gzip"
        assert artifact["file_count"] == 2
        receipt = {
            "type":"survival.workload.deploy.receipt.v1",
            "status":"DEPLOYED",
            "action_id":payload["action_id"],
            "request_hash":payload["object_hash"],
            "deployment_id":"deployment:test-1",
            "deployment_url":"https://edge.example.test/application/survival/workloads/website-static/",
            "artifact_sha256":payload["artifact"]["sha256"],
            "agent_id":remote_identity.agent_id,
            "key_id":remote_identity.key_id,
        }
        receipt["signature"] = remote_identity.sign_object(receipt)
        return receipt

    monkeypatch.setattr(manager, "_http_json", remote_call)
    monkeypatch.setattr(
        manager, "_http_bytes",
        lambda url, *, timeout, max_bytes: (site / "index.html").read_bytes(),
    )
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


def test_remote_host_verification_rejects_same_physical_host(tmp_path, monkeypatch):
    manager = SurvivalResourceManager(tmp_path)
    host = manager.register_remote_host(
        name="same-body",
        health_url="https://same.example.test/health",
        capacity={},
        reliability="BEST_EFFORT",
        now=NOW,
    )

    def remote_call(url, *, method, payload=None, timeout):
        return {
            "status": "HEALTHY",
            "host_instance_id": manager._host_instance_id(),
        }

    monkeypatch.setattr(manager, "_http_json", remote_call)
    row = manager.verify_remote_host(host["host_id"], now=NOW)
    assert row["status"] == "FAILED"
    assert row["last_error"] == "RuntimeError"
    assert row["deployment_capable"] is False


def test_agent_network_discovery_requires_signed_card_and_identity_match(tmp_path, monkeypatch):
    from agent_network.a2a_card import signed_agent_card
    from agent_network.crypto import Identity

    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("remote.network.body")
    endpoint = "https://edge.example.test/a2a"
    card_url = "https://edge.example.test/.well-known/agent-card.json"
    health_url = "https://edge.example.test/application/survival/host"
    control_url = "https://edge.example.test/application/survival/workloads"
    card = signed_agent_card(
        identity=remote,
        name="Remote Body",
        description="survival worker",
        url=endpoint,
        network_id="network.cai",
        skills=[{"id":"survival.workload.deploy.v1","name":"Deploy bounded workload"}],
        extra={"survival_resource":{
            "host_instance_id":"host-instance:remote-network-body",
            "health_url":health_url,
            "control_url":control_url,
            "capacity":{"cpu_count":2,"memory_gib":4},
            "reliability":"BEST_EFFORT",
        }},
    )

    class Network:
        def discover(self, capability, *, timeout):
            assert capability == "survival.workload.deploy.v1"
            return [{
                "agent_id":remote.agent_id,
                "public_key":remote.public_key,
                "key_id":remote.key_id,
                "endpoint":endpoint,
                "agent_card_url":card_url,
                "network_id":"network.cai",
                "reachability":"REACHABLE",
            }]

    def remote_call(url, *, method, payload=None, timeout):
        if url == card_url:
            return card
        assert url == health_url
        return {
            "status":"HEALTHY",
            "host_instance_id":"host-instance:remote-network-body",
            "agent_id":remote.agent_id,
            "key_id":remote.key_id,
            "public_key":remote.public_key,
            "capabilities":["survival.workload.deploy.v1"],
        }

    monkeypatch.setattr(manager, "_http_json", remote_call)
    result = manager.discover_agent_network_hosts(Network(), now=NOW)
    assert result["status"] == "DISCOVERED"
    assert len(result["accepted_host_ids"]) == 1
    row = manager.remote_hosts()[0]
    assert row["status"] == "ACTIVE"
    assert row["deployment_capable"] is True
    assert row["trust_source"] == "AGENT_NETWORK_SIGNED_CARD"
    assert row["expected_agent_id"] == remote.agent_id


def test_agent_network_discovery_rejects_cross_origin_resource_endpoints(tmp_path, monkeypatch):
    from agent_network.a2a_card import signed_agent_card
    from agent_network.crypto import Identity

    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("remote.bad-origin")
    endpoint = "https://edge.example.test/a2a"
    card_url = "https://edge.example.test/.well-known/agent-card.json"
    card = signed_agent_card(
        identity=remote,
        name="Remote Body",
        description="survival worker",
        url=endpoint,
        network_id="network.cai",
        skills=[{"id":"survival.workload.deploy.v1","name":"Deploy bounded workload"}],
        extra={"survival_resource":{
            "host_instance_id":"host-instance:other",
            "health_url":"https://other.example.test/health",
            "control_url":"https://other.example.test/workloads",
        }},
    )

    class Network:
        def discover(self, capability, *, timeout):
            return [{
                "agent_id":remote.agent_id,"public_key":remote.public_key,
                "key_id":remote.key_id,"endpoint":endpoint,
                "agent_card_url":card_url,"network_id":"network.cai",
                "reachability":"REACHABLE",
            }]

    monkeypatch.setattr(manager, "_http_json", lambda *args, **kwargs: card)
    result = manager.discover_agent_network_hosts(Network(), now=NOW)
    assert result["status"] == "NO_VERIFIED_REMOTE_HOST"
    assert result["accepted_host_ids"] == []
    assert manager.remote_hosts() == []

def test_ssh_bootstrap_persists_key_managed_host_without_secret(tmp_path, monkeypatch):
    from wechat_traffic_agent.survival_ssh import BootstrapResult, SSHHostBootstrapper

    manager = SurvivalResourceManager(tmp_path)
    manager.observe(now=NOW)
    captured = {}

    def fake_bootstrap(self, **kwargs):
        captured.update(kwargs)
        key_dir = tmp_path / "secrets" / "survival-hosts" / "key-ref-1"
        key_dir.mkdir(parents=True, exist_ok=True)
        key_path = key_dir / "id_ed25519"
        key_path.write_text("test-key-material")
        return BootstrapResult(
            host="207.246.99.253", port=22, username="root",
            host_key_sha256="SHA256:test-host-key",
            host_instance_id="host-instance:remote-vultr",
            audit={"hostname":"vultr-edge","cpu_count":2,"memory_total_gib":4.0,
                   "memory_available_gib":3.0,"disk_total_gib":80.0,
                   "disk_free_gib":60.0,"platform":"Linux test","machine":"x86_64"},
            key_path=str(key_path), key_verified=True,
        )

    monkeypatch.setattr(SSHHostBootstrapper, "bootstrap", fake_bootstrap)
    secret = "x" * 16
    row = manager.bootstrap_ssh_host(
        host="207.246.99.253", port=22, username="root",
        password=secret, name="vultr-edge", now=NOW,
    )
    assert captured["password"] == secret
    assert row["status"] == "ACTIVE"
    assert row["management_transport"] == "SSH_KEY"
    assert row["receiver_status"] == "NOT_PROVISIONED"
    assert row["deployment_capable"] is False
    assert row["ssh_key_ref"] == "key-ref-1"
    persisted = json.dumps(manager.status())
    assert secret not in persisted
    assert "test-key-material" not in persisted
    need = next(x for x in manager.needs() if x["action"] == "DISCOVER_REMOTE_MACHINE")
    assert need["status"] == "SATISFIED"
    assert need["resource_id"] == row["host_id"]

def test_ssh_managed_host_binds_only_matching_signed_receiver(tmp_path, monkeypatch):
    from agent_network.a2a_card import signed_agent_card
    from agent_network.crypto import Identity

    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("seed01.network.cai")
    host_id = "remote-host:ssh-managed"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id": host_id,
        "name": "vultr-edge",
        "ssh_host": "207.246.99.253",
        "ssh_port": 22,
        "ssh_username": "root",
        "management_transport": "SSH_KEY",
        "status": "ACTIVE",
        "deployment_capable": False,
        "receiver_status": "NOT_PROVISIONED",
        "host_instance_id": "host-instance:vultr-body",
        "capacity": {},
    }]))
    origin = "https://edge.example.test"
    endpoint = origin + "/a2a"
    card = signed_agent_card(
        identity=remote,
        name="network seed",
        description="receiver",
        url=endpoint,
        network_id="network.cai",
        skills=[{"id":"survival.workload.deploy.v1","name":"Deploy bounded survival workload"}],
        extra={"survival_resource":{
            "host_instance_id":"host-instance:vultr-body",
            "health_url":origin + "/application/survival/host",
            "control_url":origin + "/application/survival/workloads",
            "deployment_profiles":["content.website-static.v1"],
        }},
    )

    def remote_call(url, *, method, payload=None, timeout):
        if url.endswith("/.well-known/agent-card.json"):
            return card
        return {
            "status":"HEALTHY",
            "host_instance_id":"host-instance:vultr-body",
            "agent_id":remote.agent_id,
            "key_id":remote.key_id,
            "public_key":remote.public_key,
            "capabilities":["survival.workload.deploy.v1"],
            "deployment_profiles":["content.website-static.v1"],
        }

    monkeypatch.setattr(manager, "_http_json", remote_call)
    row = manager.bind_signed_receiver(host_id, origin=origin, now=NOW)
    assert row["receiver_status"] == "VERIFIED"
    assert row["deployment_capable"] is True
    assert row["expected_agent_id"] == remote.agent_id
    assert row["deployment_profiles"] == ["content.website-static.v1"]

    card["survival_resource"]["host_instance_id"] = "host-instance:someone-else"
    with pytest.raises(Exception):
        manager.bind_signed_receiver(host_id, origin=origin, now=NOW)
