from datetime import datetime, timezone

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.site_cli import build_site


def _artifact(artifact_id: str, slug: str, title: str, tags: tuple[str, ...]) -> ContentArtifact:
    return ContentArtifact(
        artifact_id=artifact_id,
        kind=ContentKind.ARTICLE,
        slug=slug,
        title=title,
        summary="A substantive public article used to test Web topic curation.",
        sections=(ContentSection(heading="Evidence", paragraphs=("Useful evidence-backed text." * 30,)),),
        tags=tags,
        created_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        public_safe=True,
    )


def test_site_indexes_topics_from_their_first_published_item(tmp_path):
    config = tmp_path / "site.json"
    config.write_text(
        '{"site_name":"Content Lab","base_url":"https://example.com","description":"Evidence-backed content."}',
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    first = _artifact("article-1", "first-article", "First useful article", ("shared", "singleton", "duplicate", "duplicate"))
    second = _artifact("article-2", "second-article", "Second useful article", ("shared",))
    (artifacts / "01.json").write_text(first.model_dump_json(indent=2), encoding="utf-8")
    (artifacts / "02.json").write_text(second.model_dump_json(indent=2), encoding="utf-8")

    output = tmp_path / "site-output"
    result = build_site(config, artifacts, output)

    assert result["indexed_topic_count"] == 3
    assert (output / "topics" / "shared" / "index.html").exists()
    assert (output / "topics" / "singleton" / "index.html").exists()
    assert (output / "topics" / "duplicate" / "index.html").exists()
    page = (output / "articles" / "first-article" / "index.html").read_text(encoding="utf-8")
    assert "/topics/shared/" in page
    assert "/topics/singleton/" in page
    assert "/topics/duplicate/" in page
    sitemap = (output / "sitemap.xml").read_text(encoding="utf-8")
    assert "/topics/shared/" in sitemap
    assert "/topics/singleton/" in sitemap
    assert "/topics/duplicate/" in sitemap
