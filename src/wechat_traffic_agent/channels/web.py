"""Static Website channel for public ContentArtifact publication."""
from __future__ import annotations

import html
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..content_artifact import ContentArtifact, ContentKind


class StaticWebsiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_name: str = Field(min_length=2, max_length=120)
    base_url: str
    description: str = Field(min_length=1, max_length=300)
    contact_email: str | None = Field(default=None, max_length=254)
    adsense_client: str | None = Field(default=None, pattern=r"^ca-pub-[0-9]+$")
    indexable: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        cleaned = value.rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        return cleaned


class StaticWebChannel:
    """Build an immutable static site with no runtime dependency on sources."""

    def __init__(self, config: StaticWebsiteConfig) -> None:
        self.config = config

    def build(self, artifacts: list[ContentArtifact], output_dir: Path) -> Path:
        if output_dir.exists():
            raise FileExistsError(f"website build target already exists: {output_dir}")
        self._validate_artifacts(artifacts)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
        try:
            self._render_site(artifacts, temp)
            os.replace(temp, output_dir)
        except BaseException:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        return output_dir

    def _validate_artifacts(self, artifacts: list[ContentArtifact]) -> None:
        unsafe = [item.artifact_id for item in artifacts if not item.public_safe]
        if unsafe:
            raise ValueError(f"public website refuses non-public-safe artifacts: {unsafe}")
        routes = [_route_for(item) for item in artifacts]
        if len(routes) != len(set(routes)):
            raise ValueError("duplicate public content route")

    def _render_site(self, artifacts: list[ContentArtifact], root: Path) -> None:
        ordered = sorted(artifacts, key=lambda item: item.created_at, reverse=True)
        self._write(root / "index.html", self._listing("Latest", ordered, "/"))
        articles = [item for item in ordered if item.kind is not ContentKind.RESEARCH]
        self._write(root / "articles" / "index.html", self._listing("Articles", articles, "/articles/"))
        research = [item for item in ordered if item.kind is ContentKind.RESEARCH]
        self._write(root / "research" / "index.html", self._listing("Research", research, "/research/"))
        for item in ordered:
            route = _route_for(item)
            self._write(root / route.strip("/") / "index.html", self._artifact_page(item, route))
        self._write(root / "about" / "index.html", self._simple_page("About", self.config.description, "/about/"))
        privacy = "This site publishes original and sourced content. Analytics or advertising services may process technical request data when configured."
        self._write(root / "privacy" / "index.html", self._simple_page("Privacy", privacy, "/privacy/"))
        contact = self.config.contact_email or "Contact details will be published before external launch."
        self._write(root / "contact" / "index.html", self._simple_page("Contact", contact, "/contact/"))
        urls = ["/", "/articles/", "/research/", "/about/", "/privacy/", "/contact/"]
        urls.extend(_route_for(item) for item in ordered)
        self._write(root / "sitemap.xml", self._sitemap(urls))
        if self.config.indexable:
            robots = f"User-agent: *\nAllow: /\nSitemap: {self.config.base_url}/sitemap.xml\n"
        else:
            robots = "User-agent: *\nDisallow: /\n"
        self._write(root / "robots.txt", robots)
        ads = ""
        if self.config.adsense_client:
            ads = f"google.com, {self.config.adsense_client.removeprefix('ca-')}, DIRECT, f08c47fec0942fa0\n"
        self._write(root / "ads.txt", ads)

    def _listing(self, heading: str, artifacts: list[ContentArtifact], route: str) -> str:
        cards = "".join(
            f'<article><h2><a href="{html.escape(_route_for(item), quote=True)}">{html.escape(item.title)}</a></h2>'
            f'<p>{html.escape(item.summary)}</p></article>'
            for item in artifacts
        ) or "<p>No published content yet.</p>"
        return self._layout(heading, self.config.description, route, f"<h1>{html.escape(heading)}</h1>{cards}")

    def _artifact_page(self, item: ContentArtifact, route: str) -> str:
        sections = []
        for section in item.sections:
            paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in section.paragraphs)
            sections.append(f"<section><h2>{html.escape(section.heading)}</h2>{paragraphs}</section>")
        provenance = ""
        if item.source_refs:
            rows = "".join(
                f"<li>{html.escape(ref.source)} · {html.escape(ref.ref_id)}</li>" for ref in item.source_refs
            )
            provenance = f"<aside><h2>Sources & provenance</h2><ul>{rows}</ul></aside>"
        disclosure = f"<p class=disclosure>{html.escape(item.disclosure)}</p>" if item.disclosure else ""
        body = f"<article><h1>{html.escape(item.title)}</h1><p class=summary>{html.escape(item.summary)}</p>{''.join(sections)}{provenance}{disclosure}</article>"
        return self._layout(item.title, item.summary, route, body)

    def _simple_page(self, title: str, text: str, route: str) -> str:
        return self._layout(title, text, route, f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>")

    def _layout(self, title: str, description: str, route: str, body: str) -> str:
        canonical = html.escape(f"{self.config.base_url}{route}", quote=True)
        ads = ""
        if self.config.adsense_client:
            client = html.escape(self.config.adsense_client, quote=True)
            ads = f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={client}" crossorigin="anonymous"></script>'
        robots = "" if self.config.indexable else '<meta name="robots" content="noindex,nofollow">'
        css = "body{font-family:system-ui,sans-serif;max-width:860px;margin:auto;padding:24px;line-height:1.7;color:#171717}nav{display:flex;gap:16px;margin-bottom:40px}a{color:#1255a2}article{margin:32px 0}.summary,.disclosure{color:#555}aside{border-top:1px solid #ddd;margin-top:36px;padding-top:16px}"
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)} · {html.escape(self.config.site_name)}</title>"
            f"<meta name=\"description\" content=\"{html.escape(description, quote=True)}\"><link rel=\"canonical\" href=\"{canonical}\">{robots}{ads}<style>{css}</style></head>"
            f"<body><nav><a href=\"/\">{html.escape(self.config.site_name)}</a><a href=\"/articles/\">Articles</a><a href=\"/research/\">Research</a><a href=\"/about/\">About</a></nav><main>{body}</main></body></html>"
        )

    def _sitemap(self, routes: list[str]) -> str:
        entries = "".join(
            f"<url><loc>{html.escape(self.config.base_url + route)}</loc></url>" for route in sorted(set(routes))
        )
        return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _route_for(item: ContentArtifact) -> str:
    prefix = "research" if item.kind is ContentKind.RESEARCH else "articles"
    return f"/{prefix}/{item.slug}/"
