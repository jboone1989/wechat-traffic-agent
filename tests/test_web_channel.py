from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.content_artifact import (
    ContentArtifact,
    ContentKind,
    ContentSection,
    ContentSourceRef,
)
from wechat_traffic_agent.channels.web import StaticWebChannel, StaticWebsiteConfig


def _artifact(*, safe: bool = True, title: str = "Why backtests fail in paper trading") -> ContentArtifact:
    return ContentArtifact(
        artifact_id="research-1",
        kind=ContentKind.RESEARCH,
        slug="backtest-vs-paper-trading",
        title=title,
        summary="A sourced research note about validation gaps.",
        sections=(
            ContentSection(
                heading="What happened",
                paragraphs=("Historical validation passed, but shadow evidence did not.",),
            ),
        ),
        source_refs=(
            ContentSourceRef(ref_id="trading:evidence:1", source="trading", revision="v1"),
        ),
        created_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        public_safe=safe,
    )


def _config() -> StaticWebsiteConfig:
    return StaticWebsiteConfig(
        site_name="Content Lab",
        base_url="https://example.com/",
        description="Evidence-backed content research.",
        contact_email="hello@example.com",
        adsense_client="ca-pub-1234567890",
    )


def test_static_web_channel_builds_public_site(tmp_path):
    target = tmp_path / "site"
    StaticWebChannel(_config()).build([_artifact()], target)

    article = target / "research" / "backtest-vs-paper-trading" / "index.html"
    assert article.exists()
    assert "trading:evidence:1" in article.read_text(encoding="utf-8")
    assert "https://example.com/research/backtest-vs-paper-trading/" in article.read_text(encoding="utf-8")
    assert "ca-pub-1234567890" in article.read_text(encoding="utf-8")
    assert "pub-1234567890" in (target / "ads.txt").read_text(encoding="utf-8")
    assert "https://example.com/sitemap.xml" in (target / "robots.txt").read_text(encoding="utf-8")
    assert "https://example.com/research/backtest-vs-paper-trading/" in (target / "sitemap.xml").read_text(encoding="utf-8")


def test_static_web_channel_refuses_unsafe_content(tmp_path):
    with pytest.raises(ValueError, match="non-public-safe"):
        StaticWebChannel(_config()).build([_artifact(safe=False)], tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_static_web_channel_escapes_content(tmp_path):
    target = tmp_path / "site"
    StaticWebChannel(_config()).build([_artifact(title="Research <script>alert(1)</script>")], target)
    rendered = (target / "research" / "backtest-vs-paper-trading" / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_static_web_channel_build_is_immutable(tmp_path):
    target = tmp_path / "site"
    channel = StaticWebChannel(_config())
    channel.build([_artifact()], target)
    with pytest.raises(FileExistsError):
        channel.build([_artifact()], target)
