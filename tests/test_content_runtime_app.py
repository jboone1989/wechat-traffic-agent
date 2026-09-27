from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import yaml

from wechat_traffic_agent.runtime_app import (
    CAP_CONTENT_STATUS,
    ContentApplication,
    ContentCapabilityPack,
    _capability_pack_deployment,
    _reconcile_host_capabilities,
)


class _Subject:
    subject_id = "content-agent"

    def __init__(self, tick=0):
        self.tick = tick
        self.persisted = 0
        self.observations = []

    def step(self, observation):
        self.tick += 1
        self.observations.append(observation)

    def persist(self):
        self.persisted += 1


class _Runtime:
    subject_id = "content-agent"

    def __init__(self, observation, cycle=0):
        self.cycle = cycle
        self.observation = dict(observation)
        self.persisted = 0

    def next_cycle(self):
        self.cycle += 1
        return self.cycle

    def invoke(self, capability_id, payload):
        assert capability_id == CAP_CONTENT_STATUS
        assert payload == {}
        return SimpleNamespace(payload={"ok": True, "observation": dict(self.observation)})

    def persist(self):
        self.persisted += 1


class _DomainRuntime:
    cycle = 7
    has_advisory_backend = False
    agent_manifest = SimpleNamespace(agent_type="content-agent")
    identity = SimpleNamespace(agent_id="domain-content-1")

    def __init__(self):
        self.persisted = 0
        self.external_experiences = []

    def persist(self):
        self.persisted += 1

    def observe_external_experience(self, **kwargs):
        self.external_experiences.append(dict(kwargs))
        self.cycle += 1
        return {
            "cycle": self.cycle,
            "outcome_id": "out:test",
            "experience_id": "evx:test",
        }


class _Manager:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _observation(**kwargs):
    return kwargs


def test_tick_persists_runtime_virtualbrain_and_domain_state(tmp_path):
    status = {
        "product": "content-agent",
        "agent_type": "content-agent",
        "domain_agent_id": "domain-content-1",
        "domain_runtime_cycle": 7,
        "generation_ready": False,
        "publish_mode": "manual",
    }
    subject = _Subject()
    runtime = _Runtime(status)
    domain = _DomainRuntime()
    app = ContentApplication(
        root=tmp_path,
        subject=subject,
        runtime=runtime,
        domain_runtime=domain,
        observation_factory=_observation,
    )

    result = app.run_tick(as_of_time="2026-09-14T00:00:00+00:00")

    assert result.status == "DEGRADED"
    assert result.runtime_cycle == 1
    assert result.subject_tick == 1
    assert result.domain_runtime_cycle == 7
    assert result.generation_ready is False
    assert subject.persisted == runtime.persisted == domain.persisted == 1
    assert app.health_path.exists()
    assert subject.observations[0]["world_state"]["content_agent"]["publish_mode"] == "manual"
    assert subject.observations[0]["recent_events"][0]["kind"] == "uncertainty"


def test_status_capability_is_read_only_and_bounded():
    pack = ContentCapabilityPack(lambda: {
        "domain_agent_id": "domain-content-1",
        "domain_runtime_cycle": 2,
        "generation_ready": True,
    })
    manifest = pack.manifest()
    row = manifest["capabilities"][0]
    assert row["capability_id"] == CAP_CONTENT_STATUS
    assert row["side_effect_level"] == "READ_ONLY"
    assert pack.invoke(CAP_CONTENT_STATUS, {})["observation"]["generation_ready"] is True
    assert pack.invoke(CAP_CONTENT_STATUS, {"mutate": True})["status"] == "INVALID_REQUEST"


def test_settled_task_is_ingested_as_bounded_domain_experience(tmp_path):
    domain = _DomainRuntime()
    app = ContentApplication(
        root=tmp_path,
        subject=_Subject(),
        runtime=_Runtime({}),
        domain_runtime=domain,
        observation_factory=_observation,
    )
    receipt = app.observe_domain_task_result(
        task="OBSERVE_WORLD",
        result={
            "status": "OK",
            "nested": {"world_utility": 0.4},
            "large_internal_detail": {"ignored_by_world_projection": True},
        },
        observed_at=datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc),
        evidence_refs=("world-evidence:1",),
        goal_id="autonomous-goal:1",
    )

    assert receipt["status"] == "RECORDED"
    assert receipt["cycle"] == 8
    row = domain.external_experiences[-1]
    assert row["action_kind"] == "OBSERVE_WORLD"
    assert row["success"] is True
    assert row["world_utility"] == 0.4
    assert row["goal_id"] == "autonomous-goal:1"
    assert row["world_updates"] == {
        "last_wakeful_task": "OBSERVE_WORLD",
        "last_wakeful_status": "OK",
        "last_world_utility": 0.4,
    }
    assert row["evidence_refs"][0] == "world-evidence:1"
    assert row["evidence_refs"][-1].startswith("task-result:")


def test_pending_chatgpt_is_not_ingested_as_domain_experience(tmp_path):
    domain = _DomainRuntime()
    app = ContentApplication(
        root=tmp_path,
        subject=_Subject(),
        runtime=_Runtime({}),
        domain_runtime=domain,
        observation_factory=_observation,
    )
    receipt = app.observe_domain_task_result(
        task="CONTENT_COGNITION",
        result={"status": "WAITING_FOR_CHATGPT", "request_id": "llmreq:1"},
        observed_at=datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc),
    )

    assert receipt == {"status": "DEFERRED", "reason": "cognition_not_settled"}
    assert domain.cycle == 7
    assert domain.external_experiences == []


def test_negative_world_utility_is_not_marked_successful(tmp_path):
    domain = _DomainRuntime()
    app = ContentApplication(
        root=tmp_path,
        subject=_Subject(),
        runtime=_Runtime({}),
        domain_runtime=domain,
        observation_factory=_observation,
    )
    app.observe_domain_task_result(
        task="RESOURCE_REVIEW",
        result={"status": "OK", "world_utility": -0.2},
        observed_at=datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc),
    )

    assert domain.external_experiences[-1]["success"] is False


def test_status_reports_only_explicit_website_automatic_mode(tmp_path, monkeypatch):
    app = ContentApplication(root=tmp_path, subject=_Subject(), runtime=_Runtime({}),
                             domain_runtime=_DomainRuntime(), observation_factory=_observation)
    monkeypatch.setenv('CONTENT_AUTO_PUBLISH_ENABLED', '1')
    monkeypatch.delenv('CONTENT_CONTINUOUS_ENABLED', raising=False)
    assert app.status_observation()['publish_mode'] == 'manual'
    monkeypatch.setenv('CONTENT_CONTINUOUS_ENABLED', '1')
    assert app.status_observation()['publish_mode'] == 'website_automatic'


def test_capability_pack_deployment_is_explicit(monkeypatch):
    monkeypatch.setenv("CONTENT_RUNTIME_TRUSTED_PACKS", "searxng-search,jina-reader")
    monkeypatch.setenv("CONTENT_SEARXNG_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv(
        "CONTENT_SEARXNG_PRIMARY_ENGINES",
        "google,duckduckgo web,mwmbl",
    )
    monkeypatch.setenv("CONTENT_SEARXNG_FALLBACK_ENGINES", "google")
    monkeypatch.setenv("CONTENT_JINA_READER_URL", "https://r.jina.ai")
    monkeypatch.setenv("CONTENT_JINA_API_KEY", "deployment-only-test-key")
    trusted, preferred, configs = _capability_pack_deployment()
    assert trusted == ("searxng-search", "jina-reader")
    assert preferred == {"web.search": "searxng-search", "web.fetch": "jina-reader"}
    assert configs["searxng-search"]["base_url"] == "http://127.0.0.1:8080"
    assert configs["searxng-search"]["primary_engines"] == (
        "google", "duckduckgo web", "mwmbl",
    )
    assert configs["searxng-search"]["fallback_engines"] == ("google",)
    assert configs["jina-reader"]["reader_base_url"] == "https://r.jina.ai"
    assert configs["jina-reader"]["api_key"] == "deployment-only-test-key"


def test_capability_manager_close_is_idempotent(tmp_path):
    manager = _Manager()
    app = ContentApplication(
        root=tmp_path, subject=_Subject(), runtime=_Runtime({}),
        domain_runtime=_DomainRuntime(), observation_factory=_observation,
        capability_manager=manager,
    )
    app.close()
    app.close()
    assert manager.closed == 1


def test_manifest_requires_search_and_fetch_but_media_is_optional():
    manifest = yaml.safe_load(Path("agent.yaml").read_text(encoding="utf-8"))
    required = {row["capability_id"]: row for row in manifest["required_capabilities"]}
    optional = {row["capability_id"]: row for row in manifest["optional_capabilities"]}
    assert set(required) == {"web.search", "web.fetch"}
    assert all(row["max_side_effect_level"] == "READ_ONLY" for row in required.values())
    assert set(optional) == {"image.search", "image.generate", "video.generate"}
    assert optional["image.generate"]["required_permissions"] == ["media.generate"]
    assert optional["video.generate"]["required_permissions"] == ["media.generate"]


def test_verified_host_capabilities_correct_self_model_without_faking_backend_execution():
    from agent_runtime.capabilities import CapabilityInventory

    inventory = CapabilityInventory()
    inventory.sync_backend_capabilities(
        {
            "advisory_reasoning": True,
            "structured_reasoning": True,
            "workspace_execution": False,
            "web_research": False,
            "mcp_access": False,
            "resumable_agent_session": False,
        },
        provider="legacy-provider",
    )

    class SelfModel:
        def __init__(self):
            self.values = {}

        def apply_update(
            self, path, value, *, reason, uncertainty, evidence
        ):
            self.values[path] = {
                "value": value,
                "reason": reason,
                "uncertainty": uncertainty,
                "evidence": list(evidence),
            }

    class Domain:
        def __init__(self):
            self.capability_inventory = inventory
            self.self_model = SelfModel()
            self.persisted = 0

        def persist(self):
            self.persisted += 1

    domain = Domain()
    app = SimpleNamespace(
        runtime=SimpleNamespace(
            available_capabilities=lambda: (
                "content.status",
                "web.search",
                "web.fetch",
                "x.identity",
            )
        ),
        domain_runtime=domain,
    )

    reconciled = _reconcile_host_capabilities(
        app,
        resumable_chatgpt=True,
    )

    assert reconciled == ("web_research", "resumable_agent_session")
    assert inventory.usable("web_research") is True
    assert inventory.usable("resumable_agent_session") is True
    assert inventory.usable("workspace_execution") is False
    assert inventory.usable("mcp_access") is False
    assert inventory.get("web_research").provider == "content-application-runtime"
    assert inventory.get("resumable_agent_session").provider == "livingruntime-chatgpt"
    assert domain.self_model.values["capabilities.web_research"]["value"] is True
    assert domain.self_model.values["capabilities.resumable_agent_session"]["value"] is True
    assert domain.self_model.values["capabilities.workspace_execution"]["value"] is False
    assert domain.self_model.values["capabilities.mcp_access"]["value"] is False
    assert domain.persisted == 1


def test_runtime_composition_does_not_import_concrete_web_providers():
    text = (Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent/runtime_app.py").read_text()
    assert "SearXNGSearchProvider" not in text
    assert "Crawl4AIHTTPProvider" not in text
