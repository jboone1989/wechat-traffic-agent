from datetime import datetime, timezone

from wechat_traffic_agent.channels.web import StaticWebChannel, StaticWebsiteConfig
from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection


def test_preview_site_is_noindex(tmp_path):
    artifact = ContentArtifact(
        artifact_id="preview-1",
        kind=ContentKind.ARTICLE,
        slug="preview-article",
        title="Preview article",
        summary="Preview content.",
        sections=(ContentSection(heading="Preview", paragraphs=("Not for indexing yet.",)),),
        created_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        public_safe=True,
    )
    config = StaticWebsiteConfig(
        site_name="Content Agent Preview",
        base_url="http://content.example.test",
        description="Preview build",
        indexable=False,
    )
    output = tmp_path / "site"
    StaticWebChannel(config).build([artifact], output)

    assert "Disallow: /" in (output / "robots.txt").read_text(encoding="utf-8")
    page = (output / "articles" / "preview-article" / "index.html").read_text(encoding="utf-8")
    assert 'name="robots" content="noindex,nofollow"' in page
