from pathlib import Path
from types import SimpleNamespace

import yaml

from wechat_traffic_agent.runtime_app import (
    CAP_CONTENT_STATUS,
    ContentApplication,
    ContentCapabilityPack,
    _capability_pack_deployment,
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

    def persist(self):
        self.persisted += 1


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


def test_runtime_composition_does_not_import_concrete_web_providers():
    text = (Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent/runtime_app.py").read_text()
    assert "SearXNGSearchProvider" not in text
    assert "Crawl4AIHTTPProvider" not in text
