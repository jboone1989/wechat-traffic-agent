from datetime import datetime, timezone

from wechat_traffic_agent.channels.web import StaticWebChannel, StaticWebsiteConfig
from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.site_audit import audit_static_site


def _artifact() -> ContentArtifact:
    return ContentArtifact(
        artifact_id="audit-article",
        kind=ContentKind.ARTICLE,
        slug="audit-article",
        title="A complete audit article",
        summary="A page used to verify generated-site integrity.",
        sections=(
            ContentSection(
                heading="Why integrity checks matter",
                paragraphs=("Generated websites should fail visibly when navigation or metadata breaks.",),
            ),
        ),
        tags=("testing",),
        created_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        public_safe=True,
    )


def test_generated_site_passes_integrity_audit(tmp_path):
    output = tmp_path / "site"
    config = StaticWebsiteConfig(
        site_name="Audit Site",
        base_url="https://audit.example.org",
        description="Audit test site.",
        contact_email="hello@audit.example.org",
        indexable=True,
    )
    StaticWebChannel(config).build([_artifact()], output)

    audit = audit_static_site(output, expect_indexable=True)

    assert audit.passed is True
    assert audit.broken_links == ()
    assert audit.internal_links_checked > 0
    assert audit.html_pages >= 10


def test_audit_detects_broken_internal_link(tmp_path):
    output = tmp_path / "site"
    config = StaticWebsiteConfig(
        site_name="Audit Site",
        base_url="https://audit.example.org",
        description="Audit test site.",
        indexable=False,
    )
    StaticWebChannel(config).build([_artifact()], output)
    page = output / "index.html"
    page.write_text(page.read_text(encoding="utf-8") + '<a href="/missing/">broken</a>', encoding="utf-8")

    audit = audit_static_site(output, expect_indexable=False)

    assert audit.passed is False
    assert audit.checks["no_broken_internal_links"] is False
    assert any("/missing/" in item for item in audit.broken_links)


def test_audit_accepts_known_runtime_live_route_but_not_arbitrary_missing_routes(tmp_path):
    output = tmp_path / "site"
    config = StaticWebsiteConfig(
        site_name="Audit Site",
        base_url="https://audit.example.org",
        description="Audit test site.",
        indexable=True,
    )
    StaticWebChannel(config).build([_artifact()], output)
    page = output / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8")
        + '<a href="/live/">runtime</a><a href="/still-missing/">broken</a>',
        encoding="utf-8",
    )

    audit = audit_static_site(output, expect_indexable=True)

    assert audit.passed is False
    assert not any("/live/" in item for item in audit.broken_links)
    assert any("/still-missing/" in item for item in audit.broken_links)
