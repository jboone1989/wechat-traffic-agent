from types import SimpleNamespace

from wechat_traffic_agent.runtime_app import (
    CAP_CONTENT_STATUS,
    ContentApplication,
    ContentCapabilityPack,
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


def test_runtime_web_providers_are_explicit_and_provider_owned(monkeypatch):
    import sys
    from wechat_traffic_agent.runtime_app import _register_runtime_web_providers

    created = []
    class SearchProvider:
        def __init__(self, **kwargs):
            self.kind, self.kwargs = 'search', kwargs
            created.append(self)
    class FetchProvider:
        def __init__(self, **kwargs):
            self.kind, self.kwargs = 'fetch', kwargs
            created.append(self)
    monkeypatch.setitem(sys.modules, 'agent_runtime.integrations', SimpleNamespace(
        SearXNGSearchProvider=SearchProvider, Crawl4AIHTTPProvider=FetchProvider))
    monkeypatch.setenv('CONTENT_RUNTIME_WEB_SEARCH_ENABLED', '1')
    monkeypatch.setenv('CONTENT_RUNTIME_WEB_FETCH_ENABLED', '1')
    monkeypatch.setenv('CONTENT_RUNTIME_WEB_SEARCH_URL', 'http://127.0.0.1:8080')
    monkeypatch.setenv('CONTENT_RUNTIME_WEB_FETCH_URL', 'http://127.0.0.1:11235')
    monkeypatch.setenv('CRAWL4AI_API_TOKEN', 'test-secret')

    class Registry:
        def __init__(self): self.providers = []
        def register_provider(self, provider): self.providers.append(provider)
    registry = Registry()
    _register_runtime_web_providers(registry)

    assert [p.kind for p in registry.providers] == ['search', 'fetch']
    assert registry.providers[0].kwargs == {'base_url': 'http://127.0.0.1:8080', 'max_results': 8}
    assert registry.providers[1].kwargs == {'base_url': 'http://127.0.0.1:11235', 'api_token': 'test-secret'}
