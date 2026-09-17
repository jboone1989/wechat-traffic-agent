from datetime import datetime, timezone

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.site_cli import build_site
from wechat_traffic_agent.public_persona import bootstrap_public_persona


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
        public_safe=True,
    )
    (artifacts / "article-1.json").write_text(item.model_dump_json(indent=2), encoding="utf-8")

    output = tmp_path / "site-output"
    result = build_site(config, artifacts, output)

    assert result["artifact_count"] == 1
    assert result["artifact_ids"] == ["article-1"]
    article = (output / "articles" / "first-article" / "index.html").read_text(encoding="utf-8")
    assert "By <a href=\"/about/\">Content Agent · LivingRuntime agent</a>" in article

    home = (output / "index.html").read_text(encoding="utf-8")
    about = (output / "about" / "index.html").read_text(encoding="utf-8")
    editorial = (output / "editorial-policy" / "index.html").read_text(encoding="utf-8")
    methodology = (output / "methodology" / "index.html").read_text(encoding="utf-8")
    assert "I’m Content Agent." in home
    assert "The website is the interface. The agent is the product." in home
    assert "Persistent identity" in home
    assert "Talk to Content Agent" in home
    assert "data:image/webp;base64," in home
    assert "crl-static-art" in home
    assert "LIVE AGENT" in home
    assert 'fetchpriority="high"' in home
    assert 'id="agent-canvas"' not in home
    assert "three@0.180.0" not in home
    assert "rpm-female.glb" not in home
    assert "What learning means here" in about
    assert "Learning does not equal publishing authority" in editorial
    assert "What counts as learning" in methodology


def test_article_byline_uses_durable_public_persona(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bootstrap_public_persona(
        state_root=state, name="Ferro", reason="A durable public identity for a persistent agent.",
        self_description="I am a persistent digital agent that observes, reasons, publishes, and learns in public.",
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
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
    assert "By <a href=\"/about/\">Ferro · LivingRuntime agent</a>" in article
    assert "Ferro · LivingRuntime Agent" in home
    about = (output / "about" / "index.html").read_text(encoding="utf-8")
    assert "About Ferro" in about
    assert "Part of LivingRuntime" in about
    assert "Talk to the agent" in about
