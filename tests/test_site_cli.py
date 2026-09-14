from datetime import datetime, timezone

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.site_cli import build_site


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
    assert (output / "articles" / "first-article" / "index.html").exists()

    home = (output / "index.html").read_text(encoding="utf-8")
    about = (output / "about" / "index.html").read_text(encoding="utf-8")
    editorial = (output / "editorial-policy" / "index.html").read_text(encoding="utf-8")
    methodology = (output / "methodology" / "index.html").read_text(encoding="utf-8")
    assert "Persistent self-learning Content Agent" in home
    assert "The website is the interface. The agent is the product." in home
    assert "Human-gated publishing" in home
    assert "What self-learning means here" in about
    assert "Learning does not equal publishing authority" in editorial
    assert "What counts as learning" in methodology
