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
    assert needs["DEEP_RECLAIM_STORAGE"]["status"] == "APPROVED"
    assert needs["DEEP_RECLAIM_STORAGE"]["authorization"] == "AUTONOMOUS_SAFE_POLICY"
    assert needs["DISCOVER_REMOTE_MACHINE"]["status"] == "APPROVED"

    result = manager.execute_approved(now=NOW)
    assert not stale.exists()
    assert any(row["action"] == "DEEP_RECLAIM_STORAGE" for row in result["results"])
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
                "capacity":{"cpu_count":2,"memory_available_gib":2.0,"disk_free_gib":10.0},
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
    assert service["placement"] == "host:local"
    assert service["primary_placement"] == "host:local"
    assert len(service["replicas"]) == 1
    assert service["replicas"][0]["host_id"] == host["host_id"]
    assert service["replicas"][0]["status"] == "ACTIVE"
    assert deployed["placement_mode"] == "REPLICA"
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
    assert row["capabilities"] == ["survival.workload.deploy.v1"]

    card["survival_resource"]["host_instance_id"] = "host-instance:someone-else"
    with pytest.raises(Exception):
        manager.bind_signed_receiver(host_id, origin=origin, now=NOW)

def test_replicable_legacy_remote_placement_migrates_to_primary_plus_replica(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("legacy", encoding="utf-8")
    monkeypatch.setenv("CONTENT_WEB_SITE_ROOT", str(site))
    manager = SurvivalResourceManager(tmp_path)
    manager.services_path.parent.mkdir(parents=True, exist_ok=True)
    manager.services_path.write_text(json.dumps([{
        "service_id":"website-static",
        "role":"PUBLIC_SURFACE",
        "placement":"remote-host:legacy",
        "migration_class":"REPLICABLE",
        "deployment_profile":"content.website-static.v1",
        "deployment_id":"deployment:legacy",
        "deployment_url":"https://edge.example.test/application/survival/workloads/website-static/",
        "artifact_sha256":"sha256:artifact",
        "updated_at":NOW.isoformat(),
    }]))
    row = manager.services()[0]
    assert row["placement"] == "host:local"
    assert row["primary_placement"] == "host:local"
    assert len(row["replicas"]) == 1
    assert row["replicas"][0]["host_id"] == "remote-host:legacy"
    assert row["replicas"][0]["deployment_id"] == "deployment:legacy"
    assert "deployment_id" not in row


def test_resource_review_degrades_and_recovers_receiver_and_replica(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    index = b"<h1>replica</h1>"
    (site / "index.html").write_bytes(index)
    monkeypatch.setenv("CONTENT_WEB_SITE_ROOT", str(site))
    manager = SurvivalResourceManager(tmp_path)
    host_id = "remote-host:health-test"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":host_id,
        "name":"edge",
        "management_transport":"SSH_KEY",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "host_instance_id":"host-instance:edge",
        "health_url":"https://edge.example.test/application/survival/host",
        "control_url":"https://edge.example.test/application/survival/workloads",
        "agent_id":"seed.edge",
        "key_id":"key-edge",
        "public_key":"ed25519:edge",
        "capacity":{"memory_available_gib":1.0},
    }]))
    manager.services_path.write_text(json.dumps([{
        "service_id":"website-static",
        "role":"PUBLIC_SURFACE",
        "placement":"host:local",
        "primary_placement":"host:local",
        "migration_class":"REPLICABLE",
        "deployment_profile":"content.website-static.v1",
        "replicas":[{
            "host_id":host_id,
            "status":"ACTIVE",
            "deployment_id":"deployment:test",
            "deployment_url":"https://edge.example.test/application/survival/workloads/website-static/",
            "source_manifest_sha256":manager._local_static_manifest_sha256(),
            "index_sha256":"sha256:" + __import__('hashlib').sha256(index).hexdigest(),
        }],
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=40*1024**3, free=60*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))
    monkeypatch.setattr(manager, "_http_json", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")))
    degraded = manager.observe(now=NOW)
    host = manager.remote_hosts()[0]
    service = manager.services()[0]
    assert host["status"] == "ACTIVE"
    assert host["receiver_status"] == "DEGRADED"
    assert host["deployment_capable"] is False
    assert service["replicas"][0]["status"] == "DEGRADED"
    needs = {(x["action"], x["status"]) for x in degraded["needs"]}
    assert ("RESTORE_REMOTE_RECEIVER", "PENDING") in needs
    assert ("RESTORE_SERVICE_REPLICA", "APPROVED") in needs

    def healthy(url, *, method, payload=None, timeout):
        return {
            "status":"HEALTHY",
            "host_instance_id":"host-instance:edge",
            "agent_id":"seed.edge",
            "key_id":"key-edge",
            "public_key":"ed25519:edge",
            "capabilities":["survival.workload.deploy.v1"],
            "deployment_profiles":["content.website-static.v1"],
            "capacity":{"cpu_count":2,"memory_available_gib":3.25,"disk_free_gib":20.0},
        }
    monkeypatch.setattr(manager, "_http_json", healthy)
    monkeypatch.setattr(manager, "_http_bytes", lambda *args, **kwargs: index)
    recovered = manager.observe(now=NOW.replace(minute=5))
    host = manager.remote_hosts()[0]
    service = manager.services()[0]
    assert host["receiver_status"] == "VERIFIED"
    assert host["deployment_capable"] is True
    assert host["capacity"]["memory_available_gib"] == 3.25
    assert service["replicas"][0]["status"] == "ACTIVE"
    current = {x["action"]:x["status"] for x in recovered["needs"]}
    assert current["RESTORE_REMOTE_RECEIVER"] == "SATISFIED"
    assert current["RESTORE_SERVICE_REPLICA"] == "SATISFIED"

def test_placement_policy_accepts_static_but_rejects_research_on_tiny_host(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:tiny",
        "name":"tiny-edge",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "control_url":"https://edge.example.test/application/survival/workloads",
        "deployment_profiles":["content.website-static.v1","content.resource-research.v1"],
        "capacity":{"cpu_count":1,"memory_available_gib":0.17,"disk_free_gib":8.4},
    }]))
    rows = manager.placement_candidates()
    by_service = {row["service_id"]:row for row in rows}
    assert by_service["website-static"]["eligible"] is True
    assert by_service["website-static"]["placement_mode"] == "REPLICA"
    assert by_service["resource-research"]["eligible"] is False
    assert "MEMORY_HEADROOM_INSUFFICIENT" in by_service["resource-research"]["reasons"]
    assert by_service["content-agent-core"]["eligible"] is False
    assert "PRIMARY_STATEFUL_REQUIRES_DEDICATED_MIGRATION" in by_service["content-agent-core"]["reasons"]


def test_resource_review_auto_deploys_missing_static_replica(tmp_path, monkeypatch):
    from agent_network.crypto import Identity

    site = tmp_path / "site"
    site.mkdir()
    index = b"<h1>automatic replica</h1>"
    (site / "index.html").write_bytes(index)
    monkeypatch.setenv("CONTENT_WEB_SITE_ROOT", str(site))
    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("seed.auto.edge")
    host_id = "remote-host:auto-edge"
    health_url = "https://edge.example.test/application/survival/host"
    control_url = "https://edge.example.test/application/survival/workloads"
    deployment_url = "https://edge.example.test/application/survival/workloads/website-static/"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":host_id,
        "name":"auto-edge",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "host_instance_id":"host-instance:auto-edge",
        "health_url":health_url,
        "control_url":control_url,
        "agent_id":remote.agent_id,
        "key_id":remote.key_id,
        "public_key":remote.public_key,
        "deployment_profiles":["content.website-static.v1"],
        "capacity":{"cpu_count":1,"memory_available_gib":0.2,"disk_free_gib":8.0},
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=40*1024**3, free=60*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))

    def remote_call(url, *, method, payload=None, timeout):
        if method == "GET":
            assert url == health_url
            return {
                "status":"HEALTHY",
                "host_instance_id":"host-instance:auto-edge",
                "agent_id":remote.agent_id,
                "key_id":remote.key_id,
                "public_key":remote.public_key,
                "capabilities":["survival.workload.deploy.v1"],
                "deployment_profiles":["content.website-static.v1"],
                "capacity":{"cpu_count":1,"memory_available_gib":0.2,"disk_free_gib":8.0},
            }
        assert url == control_url
        receipt = {
            "type":"survival.workload.deploy.receipt.v1",
            "status":"DEPLOYED",
            "action_id":payload["action_id"],
            "request_hash":payload["object_hash"],
            "deployment_id":"deployment:auto",
            "deployment_url":deployment_url,
            "artifact_sha256":payload["artifact"]["sha256"],
            "agent_id":remote.agent_id,
            "key_id":remote.key_id,
        }
        receipt["signature"] = remote.sign_object(receipt)
        return receipt

    monkeypatch.setattr(manager, "_http_json", remote_call)
    monkeypatch.setattr(manager, "_http_bytes", lambda *args, **kwargs: index)
    observed = manager.observe(now=NOW)
    ensure = next(row for row in observed["needs"] if row["action"] == "ENSURE_SERVICE_REPLICA")
    assert ensure["status"] == "APPROVED"
    result = manager.execute_approved(now=NOW)
    deployed = next(row for row in result["results"] if row["action"] == "ENSURE_SERVICE_REPLICA")
    assert deployed["status"] == "DEPLOYED"
    assert deployed["automatic_replica"] is True
    service = next(row for row in manager.services() if row["service_id"] == "website-static")
    assert service["placement"] == "host:local"
    assert len(service["replicas"]) == 1
    assert service["replicas"][0]["host_id"] == host_id

def test_resource_review_auto_provisions_ssh_managed_receiver(tmp_path, monkeypatch):
    from wechat_traffic_agent.survival_receiver_ssh import (
        ReceiverProvisionResult, SSHReceiverProvisioner, desired_receiver_sha,
    )

    manager = SurvivalResourceManager(tmp_path)
    key_dir = tmp_path / "secrets" / "survival-hosts" / "key-ref-auto"
    key_dir.mkdir(parents=True)
    (key_dir / "id_ed25519").write_text("fake-key")
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:auto-provision",
        "name":"bare-vps",
        "ssh_host":"203.0.113.10",
        "ssh_port":22,
        "ssh_username":"root",
        "ssh_host_key_sha256":"SHA256:test",
        "ssh_key_ref":"key-ref-auto",
        "management_transport":"SSH_KEY",
        "status":"ACTIVE",
        "receiver_status":"NOT_PROVISIONED",
        "deployment_capable":False,
        "host_instance_id":"host-instance:bare-vps",
        "capacity":{"cpu_count":1,"memory_available_gib":1.0,"disk_free_gib":10.0},
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=40*1024**3, free=60*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))

    captured = {}
    def fake_provision(self, **kwargs):
        captured.update(kwargs)
        return ReceiverProvisionResult(
            release_sha=desired_receiver_sha(),
            receiver_command="receiver-command",
            receiver_root="/root/.ferro-survival-receiver/state/workloads",
            agent_id="survival.bare.network.cai",
            key_id="key-remote",
            public_key="ed25519:remote",
            host_instance_id="host-instance:bare-vps",
            deployment_profiles=(
                "content.website-static.v1",
                "content.resource-research.v1",
            ),
            capacity={"cpu_count":1,"memory_available_gib":0.75,"disk_free_gib":9.0},
            capabilities=(
                "survival.workload.deploy.v1",
                "survival.workload.task.v1",
            ),
        )
    monkeypatch.setattr(SSHReceiverProvisioner, "provision", fake_provision)

    observed = manager.observe(now=NOW)
    need = next(row for row in observed["needs"] if row["action"] == "PROVISION_REMOTE_RECEIVER")
    assert need["status"] == "APPROVED"
    assert not any(row["action"] == "RESTORE_REMOTE_RECEIVER" and row["status"] in {"PENDING","APPROVED","AWAITING_RESOURCE"} for row in observed["needs"])
    result = manager.execute_approved(now=NOW)
    provisioned = next(row for row in result["results"] if row["action"] == "PROVISION_REMOTE_RECEIVER")
    assert provisioned["status"] == "VERIFIED"
    row = manager.remote_hosts()[0]
    assert row["receiver_status"] == "VERIFIED"
    assert row["ssh_receiver_status"] == "VERIFIED"
    assert row["receiver_transport"] == "SSH"
    assert row["deployment_capable"] is True
    assert row["ssh_receiver_release_sha"] == desired_receiver_sha()
    assert set(row["deployment_profiles"]) == {"content.website-static.v1", "content.resource-research.v1"}
    assert set(row["capabilities"]) == {"survival.workload.deploy.v1", "survival.workload.task.v1"}
    assert row["capacity"]["memory_available_gib"] == 0.75
    assert captured["controller"]["agent_id"]


def test_signed_static_replica_can_deploy_over_ssh_receiver_without_https(tmp_path, monkeypatch):
    from agent_network.crypto import Identity

    site = tmp_path / "site"
    site.mkdir()
    index = b"<h1>ssh-only edge</h1>"
    (site / "index.html").write_bytes(index)
    monkeypatch.setenv("CONTENT_WEB_SITE_ROOT", str(site))
    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("survival.remote.network.cai")
    host_id = "remote-host:ssh-only"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":host_id,
        "name":"ssh-only",
        "ssh_host":"203.0.113.11",
        "ssh_port":22,
        "ssh_username":"root",
        "ssh_host_key_sha256":"SHA256:test",
        "ssh_key_ref":"key-ref",
        "management_transport":"SSH_KEY",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "ssh_receiver_status":"VERIFIED",
        "receiver_transport":"SSH",
        "deployment_capable":True,
        "host_instance_id":"host-instance:ssh-only",
        "ssh_receiver_command":"receiver-command",
        "ssh_receiver_agent_id":remote.agent_id,
        "ssh_receiver_key_id":remote.key_id,
        "ssh_receiver_public_key":remote.public_key,
        "deployment_profiles":["content.website-static.v1"],
        "capacity":{"cpu_count":1,"memory_available_gib":1.0,"disk_free_gib":5.0},
    }]))

    class Transport:
        def deploy(self, payload):
            receipt = {
                "type":"survival.workload.deploy.receipt.v1",
                "status":"DEPLOYED",
                "action_id":payload["action_id"],
                "request_hash":payload["object_hash"],
                "deployment_id":"deployment:ssh-only",
                "deployment_url":None,
                "deployment_path":"/root/.ferro-survival-receiver/state/workloads/website-static/current",
                "artifact_sha256":payload["artifact"]["sha256"],
                "index_sha256":payload["artifact"]["index_sha256"],
                "agent_id":remote.agent_id,
                "key_id":remote.key_id,
            }
            receipt["signature"] = remote.sign_object(receipt)
            return receipt
        def static_index_sha256(self, deployment_path):
            assert deployment_path.endswith("/website-static/current")
            return "sha256:" + __import__('hashlib').sha256(index).hexdigest()
        def health(self):
            raise AssertionError("health not needed in this test")

    monkeypatch.setattr(manager, "_ssh_receiver_transport", lambda host: Transport())
    plan = manager.plan_offload("website-static", host_id, now=NOW)
    result = manager.execute_offload(plan["action_id"], now=NOW)
    assert result["status"] == "DEPLOYED"
    assert result["deployment_transport"] == "SSH"
    assert result["deployment_url"] is None
    service = next(row for row in manager.services() if row["service_id"] == "website-static")
    assert service["placement"] == "host:local"
    replica = service["replicas"][0]
    assert replica["host_id"] == host_id
    assert replica["deployment_transport"] == "SSH"
    assert replica["deployment_url"] is None
    assert replica["deployment_path"].endswith("/website-static/current")

def test_failed_autonomous_receiver_provision_retries_with_backoff(tmp_path, monkeypatch):
    from datetime import timedelta
    from wechat_traffic_agent.survival_receiver_ssh import SSHReceiverProvisioner

    manager = SurvivalResourceManager(tmp_path)
    key_dir = tmp_path / "secrets" / "survival-hosts" / "retry-key"
    key_dir.mkdir(parents=True)
    (key_dir / "id_ed25519").write_text("fake-key")
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:retry",
        "name":"retry-vps",
        "ssh_host":"203.0.113.20",
        "ssh_port":22,
        "ssh_username":"root",
        "ssh_host_key_sha256":"SHA256:test",
        "ssh_key_ref":"retry-key",
        "management_transport":"SSH_KEY",
        "status":"ACTIVE",
        "receiver_status":"NOT_PROVISIONED",
        "deployment_capable":False,
        "host_instance_id":"host-instance:retry",
        "capacity":{"cpu_count":1,"memory_available_gib":1.0,"disk_free_gib":10.0},
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=40*1024**3, free=60*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))
    monkeypatch.setattr(
        SSHReceiverProvisioner,
        "provision",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary install failure")),
    )

    first = manager.observe(now=NOW)
    need = next(x for x in first["needs"] if x["action"] == "PROVISION_REMOTE_RECEIVER")
    assert need["status"] == "APPROVED"
    manager.execute_approved(now=NOW)
    need = next(x for x in manager.needs() if x["action"] == "PROVISION_REMOTE_RECEIVER")
    assert need["status"] == "AWAITING_RESOURCE"
    assert need["retry_count"] == 1
    assert need["last_error_type"] == "RuntimeError"
    assert need["next_retry_at"] == (NOW + timedelta(minutes=5)).isoformat()

    manager.observe(now=NOW + timedelta(minutes=4, seconds=59))
    need = next(x for x in manager.needs() if x["action"] == "PROVISION_REMOTE_RECEIVER")
    assert need["status"] == "AWAITING_RESOURCE"

    retry_at = NOW + timedelta(minutes=5)
    manager.observe(now=retry_at)
    need = next(x for x in manager.needs() if x["action"] == "PROVISION_REMOTE_RECEIVER")
    assert need["status"] == "APPROVED"
    assert need["retry_rearmed_at"] == retry_at.isoformat()
    manager.execute_approved(now=retry_at)
    need = next(x for x in manager.needs() if x["action"] == "PROVISION_REMOTE_RECEIVER")
    assert need["status"] == "AWAITING_RESOURCE"
    assert need["retry_count"] == 2
    assert need["next_retry_at"] == (retry_at + timedelta(minutes=15)).isoformat()

def test_static_replica_detects_non_index_drift_and_redeploys(tmp_path, monkeypatch):
    from agent_network.crypto import Identity

    site = tmp_path / "site"
    site.mkdir()
    index = b"<h1>stable index</h1>"
    (site / "index.html").write_bytes(index)
    (site / "article.html").write_text("version one", encoding="utf-8")
    monkeypatch.setenv("CONTENT_WEB_SITE_ROOT", str(site))

    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("seed.drift.edge")
    host_id = "remote-host:drift-edge"
    health_url = "https://edge.example.test/application/survival/host"
    control_url = "https://edge.example.test/application/survival/workloads"
    deployment_url = "https://edge.example.test/application/survival/workloads/website-static/"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":host_id,
        "name":"drift-edge",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "host_instance_id":"host-instance:drift-edge",
        "health_url":health_url,
        "control_url":control_url,
        "agent_id":remote.agent_id,
        "key_id":remote.key_id,
        "public_key":remote.public_key,
        "deployment_profiles":["content.website-static.v1"],
        "capacity":{"cpu_count":1,"memory_available_gib":1.0,"disk_free_gib":8.0},
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=40*1024**3, free=60*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))

    deployments = []
    def remote_call(url, *, method, payload=None, timeout):
        if method == "GET":
            return {
                "status":"HEALTHY",
                "host_instance_id":"host-instance:drift-edge",
                "agent_id":remote.agent_id,
                "key_id":remote.key_id,
                "public_key":remote.public_key,
                "capabilities":["survival.workload.deploy.v1"],
                "deployment_profiles":["content.website-static.v1"],
                "capacity":{"cpu_count":1,"memory_available_gib":1.0,"disk_free_gib":8.0},
            }
        deployments.append(payload["artifact"]["source_manifest_sha256"])
        receipt = {
            "type":"survival.workload.deploy.receipt.v1",
            "status":"DEPLOYED",
            "action_id":payload["action_id"],
            "request_hash":payload["object_hash"],
            "deployment_id":"deployment:drift:" + str(len(deployments)),
            "deployment_url":deployment_url,
            "artifact_sha256":payload["artifact"]["sha256"],
            "agent_id":remote.agent_id,
            "key_id":remote.key_id,
        }
        receipt["signature"] = remote.sign_object(receipt)
        return receipt

    monkeypatch.setattr(manager, "_http_json", remote_call)
    monkeypatch.setattr(manager, "_http_bytes", lambda *args, **kwargs: index)

    first = manager.observe(now=NOW)
    assert next(x for x in first["needs"] if x["action"] == "ENSURE_SERVICE_REPLICA")["status"] == "APPROVED"
    first_actions = manager.execute_approved(now=NOW)
    assert any(x.get("status") == "DEPLOYED" for x in first_actions["results"])
    service = next(x for x in manager.services() if x["service_id"] == "website-static")
    first_manifest = service["replicas"][0]["source_manifest_sha256"]
    assert service["replicas"][0]["status"] == "ACTIVE"

    (site / "article.html").write_text("version two", encoding="utf-8")
    drifted = manager.observe(now=NOW.replace(minute=5))
    service = next(x for x in manager.services() if x["service_id"] == "website-static")
    replica = service["replicas"][0]
    assert replica["status"] == "STALE"
    assert replica["last_error"] == "PRIMARY_ARTIFACT_CHANGED"
    restore = next(x for x in drifted["needs"] if x["action"] == "RESTORE_SERVICE_REPLICA")
    assert restore["status"] == "APPROVED"

    repaired = manager.execute_approved(now=NOW.replace(minute=5))
    assert any(x.get("status") == "DEPLOYED" for x in repaired["results"])
    service = next(x for x in manager.services() if x["service_id"] == "website-static")
    replica = service["replicas"][0]
    assert replica["status"] == "ACTIVE"
    assert replica["source_manifest_sha256"] != first_manifest
    assert len(deployments) == 2

def test_recurrent_survival_need_reactivates_single_row_and_coalesces_legacy_duplicates(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    need_id = "survival-need:restore-static"
    manager.needs_path.parent.mkdir(parents=True, exist_ok=True)
    manager.needs_path.write_text(json.dumps([
        {
            "need_id":need_id,
            "domain":"SERVICE",
            "action":"RESTORE_SERVICE_REPLICA",
            "urgency":"HIGH",
            "reason":"old failure",
            "status":"EXECUTED",
            "authorization":"AUTONOMOUS_SAFE_POLICY",
            "created_at":"2026-09-18T10:00:00+00:00",
            "last_action_at":"2026-09-18T10:05:00+00:00",
            "last_success_at":"2026-09-18T10:05:00+00:00",
        },
        {
            "need_id":need_id,
            "domain":"SERVICE",
            "action":"RESTORE_SERVICE_REPLICA",
            "urgency":"HIGH",
            "reason":"newer failure",
            "status":"EXECUTED",
            "authorization":"AUTONOMOUS_SAFE_POLICY",
            "created_at":"2026-09-18T11:00:00+00:00",
            "last_action_at":"2026-09-18T11:05:00+00:00",
            "last_success_at":"2026-09-18T11:05:00+00:00",
        },
    ]))
    rows = manager._reconcile_needs(
        now=NOW,
        storage_pressure=False,
        compute_pressure=False,
        active_remote=True,
        usable_remote=True,
        degraded_replica=True,
        automatic_replica_gap=False,
        core_standby_capacity_gap=False,
        provisionable_remote=False,
    )
    restore = [x for x in rows if x["action"] == "RESTORE_SERVICE_REPLICA"]
    assert len(restore) == 1
    row = restore[0]
    assert row["need_id"] == need_id
    assert row["status"] == "APPROVED"
    assert row["occurrence_count"] == 3
    assert row["created_at"] == "2026-09-18T10:00:00+00:00"
    assert row["last_success_at"] == "2026-09-18T11:05:00+00:00"
    assert row["last_occurrence_at"] == NOW.isoformat()

    again = manager._reconcile_needs(
        now=NOW,
        storage_pressure=False,
        compute_pressure=False,
        active_remote=True,
        usable_remote=True,
        degraded_replica=True,
        automatic_replica_gap=False,
        core_standby_capacity_gap=False,
        provisionable_remote=False,
    )
    row = next(x for x in again if x["action"] == "RESTORE_SERVICE_REPLICA")
    assert row["occurrence_count"] == 3
    assert row["status"] == "APPROVED"


def test_stale_managed_ssh_receiver_requires_upgrade(monkeypatch):
    from wechat_traffic_agent.survival_receiver_ssh import desired_receiver_sha

    old = {
        "status":"ACTIVE",
        "management_transport":"SSH_KEY",
        "receiver_status":"VERIFIED",
        "ssh_receiver_status":"VERIFIED",
        "ssh_receiver_command":"receiver-command",
        "ssh_receiver_release_sha":"7" * 40,
        "ssh_receiver_profiles":["content.website-static.v1"],
    }
    assert SurvivalResourceManager._ssh_receiver_needs_provision(old) is True
    current = {
        **old,
        "ssh_receiver_release_sha":desired_receiver_sha(),
        "ssh_receiver_profiles":[
            "content.website-static.v1",
            "content.resource-research.v1",
            "content.core-standby.v1",
        ],
    }
    assert SurvivalResourceManager._ssh_receiver_needs_provision(current) is False


def test_core_standby_uses_operator_managed_storage_gate_and_separate_activation_gate(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:standby",
        "name":"standby",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "management_transport":"SSH_KEY",
        "reliability":"OPERATOR_MANAGED",
        "deployment_profiles":["content.core-standby.v1"],
        "capacity":{
            "cpu_count":1,
            "memory_available_gib":0.075,
            "disk_free_gib":7.0,
        },
    }]))
    candidate = next(
        row for row in manager.placement_candidates()
        if row["service_id"] == "content-agent-core-standby"
    )
    assert candidate["eligible"] is True
    assert candidate["activation_ready"] is False
    assert candidate["activation_status"] == "STANDBY_NOT_RUNNABLE"
    assert "CPU_CAPACITY_INSUFFICIENT" in candidate["activation_reasons"]
    assert "MEMORY_HEADROOM_INSUFFICIENT" in candidate["activation_reasons"]


def test_core_standby_capacity_gap_creates_explicit_acquisition_need(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    manager.services()

    rows = manager._reconcile_needs(
        now=NOW,
        storage_pressure=False,
        compute_pressure=False,
        active_remote=True,
        usable_remote=True,
        degraded_replica=False,
        automatic_replica_gap=False,
        core_standby_capacity_gap=True,
        provisionable_remote=False,
    )

    need = next(
        row for row in rows
        if row["action"] == "ACQUIRE_RUNNABLE_CORE_STANDBY"
    )
    assert need["status"] in {"PENDING", "APPROVED", "AWAITING_RESOURCE"}
    assert need["urgency"] == "HIGH"
    assert need["minimum_activation_requirements"]
    assert "activation_ready=true" in need["satisfaction_condition"]


def test_core_standby_rejects_unmanaged_receiver_even_when_capacity_is_large(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:public",
        "name":"public",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "management_transport":"HTTPS",
        "reliability":"BEST_EFFORT",
        "deployment_profiles":["content.core-standby.v1"],
        "capacity":{
            "cpu_count":8,
            "memory_available_gib":16.0,
            "disk_free_gib":100.0,
        },
    }]))
    candidate = next(
        row for row in manager.placement_candidates()
        if row["service_id"] == "content-agent-core-standby"
    )
    assert candidate["eligible"] is False
    assert "CORE_STANDBY_REQUIRES_OPERATOR_MANAGED_HOST" in candidate["reasons"]
    assert "CORE_STANDBY_REQUIRES_MANAGED_SSH" in candidate["reasons"]


def test_pressure_does_not_offload_research_to_insufficient_remote_memory(tmp_path, monkeypatch):
    manager = SurvivalResourceManager(tmp_path)
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:tiny",
        "name":"tiny",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "deployment_profiles":["content.resource-research.v1"],
        "capabilities":["survival.workload.deploy.v1","survival.workload.task.v1"],
        "capacity":{"cpu_count":1,"memory_available_gib":0.1,"disk_free_gib":10.0},
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=96*1024**3, free=4*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))
    observed = manager.observe(now=NOW)
    assert not any(
        row["action"] == "OFFLOAD_NON_CORE_SERVICE"
        and row["status"] in {"PENDING","APPROVED","AWAITING_RESOURCE"}
        for row in observed["needs"]
    )
    candidate = next(
        row for row in manager.placement_candidates()
        if row["service_id"] == "resource-research"
    )
    assert candidate["eligible"] is False
    assert "MEMORY_HEADROOM_INSUFFICIENT" in candidate["reasons"]


def test_pressure_autonomously_moves_resource_research_to_eligible_host(tmp_path, monkeypatch):
    from agent_network.crypto import Identity

    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("survival.remote")
    host_id = "remote-host:research"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":host_id,
        "name":"research-worker",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "control_url":"https://research.example.test/application/survival/workloads",
        "agent_id":remote.agent_id,
        "key_id":remote.key_id,
        "public_key":remote.public_key,
        "deployment_profiles":["content.resource-research.v1"],
        "capabilities":["survival.workload.deploy.v1","survival.workload.task.v1"],
        "capacity":{"cpu_count":2,"memory_available_gib":2.0,"disk_free_gib":20.0},
    }]))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100*1024**3, used=96*1024**3, free=4*1024**3),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_memory", staticmethod(lambda:(8*1024**3,5*1024**3)))
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda:0.2))
    monkeypatch.setattr(manager, "_refresh_remote_hosts", lambda **kwargs: None)

    captured = {}
    def fake_deploy(host, payload):
        captured.update(payload)
        receipt = {
            "type":"survival.workload.deploy.receipt.v1",
            "status":"DEPLOYED",
            "action_id":payload["action_id"],
            "request_hash":payload["object_hash"],
            "deployment_id":"deployment:research",
            "deployment_profile":"content.resource-research.v1",
            "deployment_path":"/srv/resource-research",
            "task_capability":"survival.workload.task.v1",
            "host_instance_id":"host-instance:research",
            "agent_id":remote.agent_id,
            "key_id":remote.key_id,
            "deployed_at":NOW.isoformat(),
        }
        receipt["signature"] = remote.sign_object(receipt)
        return receipt, "HTTPS"
    monkeypatch.setattr(manager, "_deploy_to_host", fake_deploy)

    observed = manager.observe(now=NOW)
    need = next(row for row in observed["needs"] if row["action"] == "OFFLOAD_NON_CORE_SERVICE")
    assert need["status"] == "APPROVED"
    assert need["authorization"] == "AUTONOMOUS_SAFE_POLICY"
    executed = manager.execute_approved(now=NOW)
    moved = next(row for row in executed["results"] if row["action"] == "OFFLOAD_NON_CORE_SERVICE")
    assert moved["status"] == "DEPLOYED"
    assert moved["automatic_move"] is True
    assert "artifact" not in captured
    service = next(row for row in manager.services() if row["service_id"] == "resource-research")
    assert service["placement"] == host_id
    assert service["task_capability"] == "survival.workload.task.v1"


def test_remote_resource_research_requires_valid_signed_receipt(tmp_path, monkeypatch):
    from agent_network.crypto import Identity

    manager = SurvivalResourceManager(tmp_path)
    remote = Identity.generate("survival.remote")
    host_id = "remote-host:research-task"
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":host_id,
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "capabilities":["survival.workload.deploy.v1","survival.workload.task.v1"],
        "ssh_receiver_capabilities":["survival.workload.deploy.v1","survival.workload.task.v1"],
        "ssh_receiver_command":"receiver-command",
        "ssh_receiver_agent_id":remote.agent_id,
        "ssh_receiver_key_id":remote.key_id,
        "ssh_receiver_public_key":remote.public_key,
    }]))
    manager.services_path.write_text(json.dumps([{
        "service_id":"resource-research",
        "role":"BACKGROUND_WORK",
        "placement":host_id,
        "primary_placement":host_id,
        "replicas":[],
        "migration_class":"DISPOSABLE_COMPUTE",
        "deployment_profile":"content.resource-research.v1",
        "task_capability":"survival.workload.task.v1",
        "resource_requirements":{"cpu_count":1,"memory_available_gib":0.5,"disk_free_gib":1.0},
        "placement_policy":{"mode":"MOVE","automatic":True,"only_when_local_pressure":True},
    }]))

    def signed_task(host, payload):
        receipt = {
            "type":"survival.workload.task.receipt.v1",
            "status":"COMPLETED",
            "task_id":payload["task_id"],
            "request_hash":payload["object_hash"],
            "deployment_profile":"content.resource-research.v1",
            "operation":"fetch_public_text",
            "result":{
                "url":"https://docs.example.test/pricing",
                "status_code":200,
                "content_type":"text/plain",
                "text":"Free tier, no credit card required.",
                "truncated":False,
                "bytes":35,
                "sha256":"sha256:" + "a"*64,
            },
            "host_instance_id":"host-instance:research",
            "agent_id":remote.agent_id,
            "key_id":remote.key_id,
            "completed_at":NOW.isoformat(),
        }
        receipt["signature"] = remote.sign_object(receipt)
        return receipt, "SSH"
    monkeypatch.setattr(manager, "_task_on_host", signed_task)
    result = manager.fetch_remote_resource_research(
        "https://docs.example.test/pricing", max_chars=24000, now=NOW
    )
    assert result["transport"] == "SSH"
    assert result["result"]["text"].startswith("Free tier")
    assert result["receipt_id"].startswith("remote-research:")

    def tampered_task(host, payload):
        receipt, transport = signed_task(host, payload)
        receipt["result"]["text"] = "tampered"
        return receipt, transport
    monkeypatch.setattr(manager, "_task_on_host", tampered_task)
    with pytest.raises(RuntimeError, match="signature"):
        manager.fetch_remote_resource_research(
            "https://docs.example.test/pricing", max_chars=24000, now=NOW
        )


def test_https_health_merges_verified_ssh_receiver_capabilities(tmp_path, monkeypatch):
    manager = SurvivalResourceManager(tmp_path)
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:dual",
        "health_url":"https://dual.example.test/health",
        "control_url":"https://dual.example.test/workloads",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "ssh_receiver_status":"VERIFIED",
        "ssh_receiver_command":"receiver-command",
        "ssh_receiver_capabilities":["survival.workload.task.v1"],
        "ssh_receiver_profiles":["content.resource-research.v1"],
        "host_instance_id":"host-instance:dual",
    }]))
    payload = {
        "status":"HEALTHY",
        "host_instance_id":"host-instance:dual",
        "agent_id":"https-receiver",
        "key_id":"https-key",
        "public_key":"ed25519:https",
        "capabilities":["survival.workload.deploy.v1"],
        "deployment_profiles":["content.website-static.v1"],
        "capacity":{"cpu_count":2,"memory_available_gib":2.0,"disk_free_gib":20.0},
    }
    monkeypatch.setattr(manager, "_receiver_health", lambda row: (payload, "HTTPS"))
    manager._refresh_remote_hosts(now=NOW)
    row = manager.remote_hosts()[0]
    assert set(row["capabilities"]) == {
        "survival.workload.deploy.v1", "survival.workload.task.v1",
    }
    assert set(row["deployment_profiles"]) == {
        "content.website-static.v1", "content.resource-research.v1",
    }
    assert row["deployment_capable"] is True


def test_automatic_move_remains_blocked_without_local_pressure(tmp_path):
    manager = SurvivalResourceManager(tmp_path)
    manager.hosts_path.parent.mkdir(parents=True, exist_ok=True)
    manager.hosts_path.write_text(json.dumps([{
        "host_id":"remote-host:idle",
        "name":"idle-worker",
        "status":"ACTIVE",
        "receiver_status":"VERIFIED",
        "deployment_capable":True,
        "control_url":"https://idle.example.test/workloads",
        "deployment_profiles":["content.resource-research.v1"],
        "capabilities":["survival.workload.deploy.v1","survival.workload.task.v1"],
        "capacity":{"cpu_count":2,"memory_available_gib":2.0,"disk_free_gib":20.0},
    }]))
    manager.policy_path.write_text(json.dumps({"pressure":"NORMAL"}))
    manager.needs_path.write_text(json.dumps([{
        "need_id":"survival-need:stale-offload",
        "domain":"SERVICE",
        "action":"OFFLOAD_NON_CORE_SERVICE",
        "status":"APPROVED",
        "authorization":"AUTONOMOUS_SAFE_POLICY",
        "created_at":NOW.isoformat(),
        "reason":"stale pressure",
        "urgency":"NORMAL",
    }]))
    assert manager._automatic_offload_target() is None
    result = manager.execute_approved(now=NOW)
    row = next(x for x in result["results"] if x["action"] == "OFFLOAD_NON_CORE_SERVICE")
    assert row["status"] == "AWAITING_RESOURCE"
    service = next(x for x in manager.services() if x["service_id"] == "resource-research")
    assert service["placement"] == "host:local"
