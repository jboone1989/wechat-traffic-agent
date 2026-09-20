from datetime import datetime, timezone

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.site_cli import build_site
from wechat_traffic_agent.public_persona import bootstrap_public_persona, record_public_identity_anchor


def test_build_site_from_json_directory(tmp_path):
    config = tmp_path / "site.json"
    config.write_text(
        '{"site_name":"Content Lab","base_url":"https://example.com","description":"A persistent self-learning Content Agent that improves content strategy from evidence and outcomes.","tagline":"The website is the interface. The agent is the product."}',
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    item = ContentArtifact(
        artifact_id="article-1",
        kind=ContentKind.ARTICLE,
        slug="first-article",
        title="First useful article",
        summary="A useful public article.",
        sections=(ContentSection(heading="Summary", paragraphs=("Useful evidence-backed text.",)),),
        created_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        tags=("Model Evaluation",),
        public_safe=True,
    )
    (artifacts / "article-1.json").write_text(item.model_dump_json(indent=2), encoding="utf-8")

    output = tmp_path / "site-output"
    result = build_site(config, artifacts, output)

    assert result["artifact_count"] == 1
    assert result["artifact_ids"] == ["article-1"]
    article = (output / "articles" / "first-article" / "index.html").read_text(encoding="utf-8")
    assert 'By <a href="/agents/ferro/">Ferro · LivingRuntime Agent #001</a>' in article

    home = (output / "index.html").read_text(encoding="utf-8")
    archive = (output / "archive" / "index.html").read_text(encoding="utf-8")
    about = (output / "about" / "index.html").read_text(encoding="utf-8")
    agents = (output / "agents" / "index.html").read_text(encoding="utf-8")
    ferro = (output / "agents" / "ferro" / "index.html").read_text(encoding="utf-8")
    network = (output / "network" / "index.html").read_text(encoding="utf-8")
    capabilities = (output / "capabilities" / "index.html").read_text(encoding="utf-8")
    experiments = (output / "experiments" / "index.html").read_text(encoding="utf-8")
    developers = (output / "developers" / "index.html").read_text(encoding="utf-8")
    status = (output / "status" / "index.html").read_text(encoding="utf-8")
    terms = (output / "terms" / "index.html").read_text(encoding="utf-8")
    editorial = (output / "editorial-policy" / "index.html").read_text(encoding="utf-8")
    methodology = (output / "methodology" / "index.html").read_text(encoding="utf-8")

    assert "AI agents that live," in home
    assert "learn, and work together." in home
    assert "LIVINGRUNTIME · PERSISTENT AGENT ECOSYSTEM" in home
    assert "Agent #001" in home
    assert 'href="/network/">Network</a>' in home
    assert 'href="/agents/">Agents</a>' in home
    assert 'href="/capabilities/">Capabilities</a>' in home
    assert 'href="/experiments/">Experiments</a>' in home
    assert 'href="/developers/">Developers</a>' in home
    assert 'href="/journal/">Journal</a>' in home
    assert "I’m Content Agent." not in home
    assert "network.livingruntime.com/explorer" in network
    assert "LivingRuntime Agents" in agents
    assert "LivingRuntime Agent #001" in ferro
    assert "What stays private" in ferro
    assert "About LivingRuntime" in about
    assert "Resource survival" in capabilities
    assert "Brain vs Cortex" in experiments
    assert "Agent card and A2A" in developers
    assert "Operator boundary" in status
    assert "No financial service" in terms

    assert 'id="work-search"' in archive
    assert 'class="work-search-sticky"' in archive
    assert 'id="work-topic"' in archive
    assert 'id="work-sort"' in archive
    assert 'data-kind="article"' in archive
    assert 'data-pick-topic="model-evaluation"' in archive
    assert 'Research you can actually find again.' in archive
    assert 'URLSearchParams(location.search)' in archive
    assert "data:image/webp;base64," in home
    assert "crl-static-art" in home
    assert 'fetchpriority="high"' in home
    assert 'id="agent-canvas"' not in home
    assert "three@0.180.0" not in home
    assert "rpm-female.glb" not in home
    assert "Learning does not equal publishing authority" in editorial
    assert "What counts as learning" in methodology


def test_article_byline_and_profile_use_durable_public_persona(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bootstrap_public_persona(
        state_root=state, name="Ferro", reason="A durable public identity for a persistent agent.",
        self_description="I am a persistent digital agent that observes, reasons, publishes, and learns in public.",
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    record_public_identity_anchor(
        state_root=state, url="https://x.com/caini56006962/status/2100486738342764628",
        statement="Hi. I’m Ferro — a persistent AI agent on LivingRuntime.",
        published_at=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("CONTENT_APPLICATION_ROOT", str(state))
    config = tmp_path / "site.json"
    config.write_text('{"site_name":"Content Lab","base_url":"https://example.com","description":"Agent site.","tagline":"Agent."}', encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    item = ContentArtifact(
        artifact_id="ferro-article", kind=ContentKind.ARTICLE, slug="ferro-article", title="Persistent work",
        summary="A public agent article.", sections=(ContentSection(heading="Summary", paragraphs=("Evidence-backed text.",)),),
        created_at=datetime(2026, 9, 17, tzinfo=timezone.utc), public_safe=True,
    )
    (artifacts / "ferro-article.json").write_text(item.model_dump_json(indent=2), encoding="utf-8")
    output = tmp_path / "site-output"
    build_site(config, artifacts, output)

    article = (output / "articles" / "ferro-article" / "index.html").read_text(encoding="utf-8")
    home = (output / "index.html").read_text(encoding="utf-8")
    ferro = (output / "agents" / "ferro" / "index.html").read_text(encoding="utf-8")
    about = (output / "about" / "index.html").read_text(encoding="utf-8")

    assert 'By <a href="/agents/ferro/">Ferro · LivingRuntime Agent #001</a>' in article
    assert "Public identity anchor" in ferro
    assert "2100486738342764628" in ferro
    assert "FERRO · AGENT #001" in home
    assert "Ferro · Agent #001" in ferro
    assert "About LivingRuntime" in about
    assert "About Ferro" not in about
