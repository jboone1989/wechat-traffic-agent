"""Static Website channel for public ContentArtifact publication."""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..content_artifact import ContentArtifact, ContentKind


_TAG_SLUG_RE = re.compile(r"[^a-z0-9]+")


class StaticWebsiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_name: str = Field(min_length=2, max_length=120)
    base_url: str
    description: str = Field(min_length=1, max_length=300)
    tagline: str = Field(default="Evidence before confidence.", max_length=160)
    contact_email: str | None = Field(default=None, max_length=254)
    publisher_name: str | None = Field(default=None, max_length=160)
    adsense_client: str | None = Field(default=None, pattern=r"^ca-pub-[0-9]+$")
    google_site_verification: str | None = Field(default=None, max_length=200)
    analytics_measurement_id: str | None = Field(default=None, pattern=r"^G-[A-Z0-9]+$")
    language: str = Field(default="en", min_length=2, max_length=16)
    locale: str = Field(default="en_US", min_length=2, max_length=24)
    indexable: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        cleaned = value.rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        return cleaned


class SiteReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code_ready: bool
    ready_for_adsense_connection: bool
    checks: dict[str, bool]
    external_dependencies: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


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
        ordered = sorted(artifacts, key=lambda item: item.updated_at or item.created_at, reverse=True)
        categories = {
            "Articles": [item for item in ordered if item.kind is ContentKind.ARTICLE],
            "Guides": [item for item in ordered if item.kind is ContentKind.GUIDE],
            "Research": [item for item in ordered if item.kind is ContentKind.RESEARCH],
            "Notes": [item for item in ordered if item.kind is ContentKind.NOTE],
        }
        self._write(root / "index.html", self._home(ordered, categories))
        for heading, items in categories.items():
            route = f"/{heading.lower()}/"
            self._write(root / heading.lower() / "index.html", self._listing(heading, items, route))
        self._write(root / "archive" / "index.html", self._archive(ordered))
        self._render_topics(root, ordered)
        for item in ordered:
            route = _route_for(item)
            self._write(root / route.strip("/") / "index.html", self._artifact_page(item, route, ordered))

        static_pages = self._static_pages()
        for route, (title, body, description) in static_pages.items():
            self._write(root / route.strip("/") / "index.html", self._simple_page(title, body, route, description))

        self._write(root / "404.html", self._not_found())
        urls = ["/", "/articles/", "/guides/", "/research/", "/notes/", "/archive/", "/topics/"]
        urls.extend(static_pages)
        urls.extend(_route_for(item) for item in ordered)
        urls.extend(self._topic_routes(ordered))
        self._write(root / "sitemap.xml", self._sitemap(urls, ordered))
        self._write(root / "feed.xml", self._feed(ordered))
        if self.config.indexable:
            robots = (
                "User-agent: *\n"
                "Allow: /\n"
                f"Sitemap: {self.config.base_url}/sitemap.xml\n"
            )
        else:
            robots = "User-agent: *\nDisallow: /\n"
        self._write(root / "robots.txt", robots)
        ads = ""
        if self.config.adsense_client:
            ads = (
                "google.com, "
                f"{self.config.adsense_client.removeprefix('ca-')}, "
                "DIRECT, f08c47fec0942fa0\n"
            )
        self._write(root / "ads.txt", ads)

    def _home(self, artifacts: list[ContentArtifact], categories: dict[str, list[ContentArtifact]]) -> str:
        latest = artifacts[:6]
        latest_html = self._cards(latest)
        category_html = "".join(
            f'<section class="category"><div><p class="eyebrow">{html.escape(name)}</p>'
            f"<h2>{len(items)} published</h2></div>"
            f'<a class="text-link" href="/{name.lower()}/">Browse {html.escape(name)} →</a></section>'
            for name, items in categories.items()
        )
        body = (
            '<section class="hero">'
            f'<p class="eyebrow">Independent research & explainers</p>'
            f"<h1>{html.escape(self.config.tagline)}</h1>"
            f"<p>{html.escape(self.config.description)}</p>"
            '<div class="hero-actions"><a class="button" href="/guides/">Start with the guides</a>'
            '<a class="text-link" href="/methodology/">See our methodology →</a></div>'
            "</section>"
            '<section class="trust-strip"><span>Original analysis</span><span>Source-aware</span>'
            '<span>Clear uncertainty</span><span>No trading execution access</span></section>'
            f'<section class="section-head"><div><p class="eyebrow">Latest</p><h2>Recent publishing</h2></div>'
            '<a class="text-link" href="/archive/">View archive →</a></section>'
            f'<div class="card-grid">{latest_html}</div>'
            '<section class="section-head"><div><p class="eyebrow">Explore</p><h2>Built for useful reading</h2></div></section>'
            f'<div class="category-grid">{category_html}</div>'
            '<section class="method-card"><div><p class="eyebrow">How we work</p>'
            '<h2>Evidence first, publication second.</h2>'
            '<p>Generation and publication are separate steps. Public pages are built only from artifacts '
            'that have passed an explicit public-safety gate, and sourced research retains provenance.</p></div>'
            '<a class="button secondary" href="/editorial-policy/">Editorial policy</a></section>'
        )
        return self._layout(
            self.config.site_name,
            self.config.description,
            "/",
            body,
            structured_data=self._website_schema(),
        )

    def _listing(self, heading: str, artifacts: list[ContentArtifact], route: str) -> str:
        cards = self._cards(artifacts) or (
            '<div class="empty-state"><h2>No published content yet.</h2>'
            "<p>This section is intentionally empty rather than padded with thin pages.</p></div>"
        )
        body = (
            f'<header class="page-head"><p class="eyebrow">Library</p><h1>{html.escape(heading)}</h1>'
            f"<p>{html.escape(self.config.description)}</p></header>"
            f'<div class="card-grid">{cards}</div>'
        )
        return self._layout(heading, self.config.description, route, body)

    def _archive(self, artifacts: list[ContentArtifact]) -> str:
        groups: dict[str, list[ContentArtifact]] = {}
        for item in artifacts:
            key = item.created_at.strftime("%Y-%m")
            groups.setdefault(key, []).append(item)
        rows = []
        for key in sorted(groups, reverse=True):
            month = datetime.strptime(key, "%Y-%m").strftime("%B %Y")
            links = "".join(
                f'<li><a href="{html.escape(_route_for(item), quote=True)}">{html.escape(item.title)}</a>'
                f'<span>{html.escape(item.kind.value.title())}</span></li>'
                for item in groups[key]
            )
            rows.append(f"<section><h2>{html.escape(month)}</h2><ul class=\"archive-list\">{links}</ul></section>")
        body = (
            '<header class="page-head"><p class="eyebrow">Archive</p><h1>Everything we have published</h1>'
            "<p>A chronological index of articles, guides, and research notes.</p></header>"
            + "".join(rows)
        )
        return self._layout("Archive", "Chronological publishing archive.", "/archive/", body)

    def _render_topics(self, root: Path, artifacts: list[ContentArtifact]) -> None:
        topic_map: dict[str, tuple[str, list[ContentArtifact]]] = {}
        for item in artifacts:
            for tag in item.tags:
                slug = _tag_slug(tag)
                if not slug:
                    continue
                display, items = topic_map.setdefault(slug, (tag.strip(), []))
                items.append(item)
                topic_map[slug] = (display, items)
        topic_cards = "".join(
            f'<a class="topic-card" href="/topics/{html.escape(slug, quote=True)}/">'
            f"<strong>{html.escape(display)}</strong><span>{len(items)} pieces</span></a>"
            for slug, (display, items) in sorted(topic_map.items())
        )
        body = (
            '<header class="page-head"><p class="eyebrow">Topics</p><h1>Browse by subject</h1>'
            "<p>Topic pages group related work without creating empty search-engine doorway pages.</p></header>"
            f'<div class="topic-grid">{topic_cards or "<p>No topics published yet.</p>"}</div>'
        )
        self._write(root / "topics" / "index.html", self._layout("Topics", "Browse published topics.", "/topics/", body))
        for slug, (display, items) in topic_map.items():
            route = f"/topics/{slug}/"
            page = (
                f'<header class="page-head"><p class="eyebrow">Topic</p><h1>{html.escape(display)}</h1>'
                f"<p>{len(items)} published pieces on this subject.</p></header>"
                f'<div class="card-grid">{self._cards(sorted(items, key=lambda x: x.created_at, reverse=True))}</div>'
            )
            self._write(
                root / "topics" / slug / "index.html",
                self._layout(display, f"Published work about {display}.", route, page),
            )

    def _topic_routes(self, artifacts: list[ContentArtifact]) -> list[str]:
        return sorted(
            {f"/topics/{slug}/" for item in artifacts for tag in item.tags if (slug := _tag_slug(tag))}
        )

    def _artifact_page(
        self,
        item: ContentArtifact,
        route: str,
        all_artifacts: list[ContentArtifact],
    ) -> str:
        sections = []
        for section in item.sections:
            paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in section.paragraphs)
            sections.append(f"<section><h2>{html.escape(section.heading)}</h2>{paragraphs}</section>")
        provenance = ""
        if item.source_refs:
            rows = "".join(
                self._source_row(ref.source, ref.ref_id, ref.locator)
                for ref in item.source_refs
            )
            provenance = (
                '<aside class="provenance"><p class="eyebrow">Provenance</p>'
                "<h2>Sources & evidence references</h2>"
                "<p>These references record where source material came from; they do not imply endorsement.</p>"
                f"<ul>{rows}</ul></aside>"
            )
        disclosure = (
            f'<div class="disclosure"><strong>Disclosure:</strong> {html.escape(item.disclosure)}</div>'
            if item.disclosure
            else ""
        )
        tags = "".join(
            f'<a href="/topics/{html.escape(_tag_slug(tag), quote=True)}/">#{html.escape(tag)}</a>'
            for tag in item.tags
            if _tag_slug(tag)
        )
        related = self._related(item, all_artifacts)
        related_html = ""
        if related:
            related_html = (
                '<section class="related"><p class="eyebrow">Continue reading</p><h2>Related</h2>'
                f'<div class="card-grid compact">{self._cards(related)}</div></section>'
            )
        published = item.created_at.astimezone(timezone.utc)
        modified = (item.updated_at or item.created_at).astimezone(timezone.utc)
        words = _word_count(item)
        minutes = max(1, round(words / 220))
        body = (
            '<article class="article-page">'
            f'<header class="article-head"><p class="eyebrow">{html.escape(item.kind.value)}</p>'
            f"<h1>{html.escape(item.title)}</h1><p class=\"summary\">{html.escape(item.summary)}</p>"
            f'<div class="byline"><span>{published.strftime("%B %d, %Y")}</span>'
            f"<span>{minutes} min read</span><span>{words} words</span></div>"
            f'<div class="tag-row">{tags}</div></header>'
            f'<div class="prose">{"".join(sections)}</div>{provenance}{disclosure}</article>{related_html}'
        )
        return self._layout(
            item.title,
            item.summary,
            route,
            body,
            og_type="article",
            structured_data=self._article_schema(item, route, published, modified),
        )

    def _source_row(self, source: str, ref_id: str, locator: str | None) -> str:
        label = f"{html.escape(source)} · {html.escape(ref_id)}"
        if locator and urlparse(locator).scheme in {"http", "https"}:
            safe = html.escape(locator, quote=True)
            return f'<li><a href="{safe}" rel="nofollow noopener noreferrer">{label}</a></li>'
        return f"<li>{label}</li>"

    def _related(self, item: ContentArtifact, all_artifacts: list[ContentArtifact]) -> list[ContentArtifact]:
        tags = {tag.casefold() for tag in item.tags}
        ranked = []
        for candidate in all_artifacts:
            if candidate.artifact_id == item.artifact_id:
                continue
            overlap = len(tags & {tag.casefold() for tag in candidate.tags})
            if overlap:
                ranked.append((overlap, candidate.created_at, candidate))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in ranked[:3]]

    def _simple_page(self, title: str, body: str, route: str, description: str) -> str:
        content = (
            f'<article class="policy-page"><header class="page-head"><p class="eyebrow">{html.escape(title)}</p>'
            f"<h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></header>"
            f'<div class="prose">{body}</div></article>'
        )
        return self._layout(title, description, route, content)

    def _static_pages(self) -> dict[str, tuple[str, str, str]]:
        site = html.escape(self.config.site_name)
        publisher = html.escape(self.config.publisher_name or self.config.site_name)
        email = html.escape(self.config.contact_email) if self.config.contact_email else None
        contact_line = (
            f'Email: <a href="mailto:{html.escape(self.config.contact_email, quote=True)}">{email}</a>.'
            if self.config.contact_email
            else "A public contact email will be added before the production domain is launched."
        )
        return {
            "/about/": (
                "About",
                f"<p>{site} publishes original explainers, guides, and evidence-aware research for readers who "
                "want to understand how claims are tested rather than simply consume conclusions.</p>"
                "<h2>What makes this site different</h2>"
                "<p>We separate observation from interpretation, retain provenance when a piece relies on outside "
                "evidence, and disclose uncertainty instead of turning incomplete evidence into certainty.</p>"
                "<h2>What this site is not</h2>"
                "<p>This site is not a brokerage, exchange, investment adviser, signal service, or execution system. "
                "The public website has no authority to place or modify trades.</p>",
                "Who we are, what we publish, and the boundaries of this site.",
            ),
            "/editorial-policy/": (
                "Editorial Policy",
                "<p>We publish only material intended to add explanatory or research value. We do not create pages "
                "solely to capture search traffic, and we do not republish scraped articles as original work.</p>"
                "<h2>Automation and AI</h2>"
                "<p>Software and AI may assist topic discovery, drafting, classification, and quality checks. "
                "Generation is not publication: public content must pass a separate public-safety admission step. "
                "Automation does not excuse invented facts, unsupported sources, or misleading certainty.</p>"
                "<h2>Corrections</h2>"
                "<p>Material errors should be corrected in the published artifact and reflected in its updated date. "
                "Where a conclusion changes because evidence changed, we prefer to explain the change rather than "
                "silently rewrite history.</p>"
                "<h2>Conflicts and incentives</h2>"
                "<p>Advertising does not determine editorial conclusions. Sponsored or affiliate material, if ever "
                "introduced, must be clearly labeled and kept separate from independent research.</p>",
                "How content is selected, produced, corrected, and separated from advertising.",
            ),
            "/methodology/": (
                "Research Methodology",
                "<p>Our baseline workflow is: define the question, identify the evidence, separate measurements from "
                "interpretation, state what would falsify the claim, and preserve limitations.</p>"
                "<h2>Evidence levels</h2>"
                "<p>Historical backtests, paper observations, prospective shadow results, and live outcomes answer "
                "different questions. A stronger label is not substituted for missing evidence.</p>"
                "<h2>Reproducibility</h2>"
                "<p>When a research artifact has machine-verifiable provenance or version references, the website "
                "retains them. Missing data is treated as unknown rather than silently converted into a pass.</p>"
                "<h2>Market content</h2>"
                "<p>Markets are stochastic and non-stationary. Examples are educational unless explicitly stated "
                "otherwise, and past observations do not guarantee future outcomes.</p>",
                "The evidence standards used for research, explainers, and market-related content.",
            ),
            "/privacy/": (
                "Privacy Policy",
                f"<p>{publisher} aims to minimize data collection. This static site can be read without creating an "
                "account.</p><h2>Server and analytics data</h2><p>Hosting, security, analytics, or content-delivery "
                "providers may process technical data such as IP address, browser information, timestamps, and "
                "requested URLs for security and measurement.</p>"
                "<h2>Advertising</h2><p>If Google AdSense is enabled, Google and its partners may use cookies or "
                "similar technologies to serve, measure, and personalize advertising where permitted. Consent "
                "controls required by applicable law will be configured before personalized advertising is enabled "
                "for affected visitors.</p><h2>Your choices</h2><p>You can use browser controls to limit cookies and "
                f"tracking. {contact_line}</p>",
                "How this site handles technical data, analytics, cookies, and advertising.",
            ),
            "/terms/": (
                "Terms of Use",
                "<p>By using this site, you agree to use its content for lawful informational purposes. Content may "
                "change as evidence, software, or editorial judgments change.</p>"
                "<h2>No warranty</h2><p>We aim for accuracy but do not warrant that every page is complete, current, "
                "or suitable for a particular purpose.</p>"
                "<h2>External links</h2><p>Links to third-party sites are provided for context or provenance. We do "
                "not control those sites and are not responsible for their content or availability.</p>"
                "<h2>Intellectual property</h2><p>Original site text and presentation are protected by applicable "
                "law. Third-party names, marks, and source material remain the property of their respective owners.</p>",
                "Terms governing use of the site and its content.",
            ),
            "/disclaimer/": (
                "Financial Disclaimer",
                "<p>Market-related content on this site is for education and research. It is not individualized "
                "investment, legal, tax, or financial advice and is not a recommendation to buy, sell, or hold any "
                "asset.</p><h2>Risk</h2><p>Trading and investing can result in substantial losses. Backtests, paper "
                "trading, simulations, model outputs, and historical examples can differ materially from live "
                "results.</p><h2>Independence from execution</h2><p>The public Content Agent website is separated "
                "from trading execution authority. Publishing a research result does not authorize a trade.</p>",
                "Important limitations for market, trading, and investment-related content.",
            ),
            "/contact/": (
                "Contact",
                f"<p>Questions, corrections, privacy requests, and source issues are welcome.</p><p>{contact_line}</p>"
                "<p>Please include the page title or URL when reporting a correction so the issue can be traced to "
                "the exact published artifact.</p>",
                "How to reach the editorial team for corrections, privacy, and general questions.",
            ),
        }

    def _not_found(self) -> str:
        body = (
            '<section class="not-found"><p class="eyebrow">404</p><h1>That page is not here.</h1>'
            "<p>The URL may have changed, or the content may have been removed rather than left as a thin placeholder.</p>"
            '<a class="button" href="/">Return home</a></section>'
        )
        return self._layout("Page not found", "The requested page could not be found.", "/404.html", body, noindex=True)

    def _cards(self, artifacts: list[ContentArtifact]) -> str:
        return "".join(
            f'<article class="card"><p class="eyebrow">{html.escape(item.kind.value)}</p>'
            f'<h3><a href="{html.escape(_route_for(item), quote=True)}">{html.escape(item.title)}</a></h3>'
            f"<p>{html.escape(item.summary)}</p>"
            f'<div class="card-meta"><span>{item.created_at.strftime("%b %d, %Y")}</span>'
            f"<span>{max(1, round(_word_count(item) / 220))} min</span></div></article>"
            for item in artifacts
        )

    def _layout(
        self,
        title: str,
        description: str,
        route: str,
        body: str,
        *,
        og_type: str = "website",
        structured_data: dict[str, object] | None = None,
        noindex: bool = False,
    ) -> str:
        canonical_url = f"{self.config.base_url}{route}"
        canonical = html.escape(canonical_url, quote=True)
        full_title = html.escape(
            self.config.site_name if route == "/" else f"{title} · {self.config.site_name}"
        )
        meta_description = html.escape(description, quote=True)
        ads = ""
        if self.config.adsense_client:
            client = html.escape(self.config.adsense_client, quote=True)
            ads = (
                '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
                f'?client={client}" crossorigin="anonymous"></script>'
            )
        analytics = ""
        if self.config.analytics_measurement_id:
            measurement = html.escape(self.config.analytics_measurement_id, quote=True)
            analytics = (
                f'<script async src="https://www.googletagmanager.com/gtag/js?id={measurement}"></script>'
                '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
                f"gtag('js',new Date());gtag('config','{measurement}');</script>"
            )
        verification = ""
        if self.config.google_site_verification:
            verification = (
                '<meta name="google-site-verification" content="'
                f'{html.escape(self.config.google_site_verification, quote=True)}">'
            )
        robots = ""
        if noindex or not self.config.indexable:
            robots = '<meta name="robots" content="noindex,nofollow">'
        schema = ""
        if structured_data:
            payload = json.dumps(structured_data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
            schema = f'<script type="application/ld+json">{payload}</script>'
        css = self._css()
        return (
            f'<!doctype html><html lang="{html.escape(self.config.language, quote=True)}"><head>'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{full_title}</title><meta name=\"description\" content=\"{meta_description}\">"
            f'<link rel="canonical" href="{canonical}"><link rel="alternate" type="application/rss+xml" '
            f'title="{html.escape(self.config.site_name, quote=True)}" href="{html.escape(self.config.base_url + "/feed.xml", quote=True)}">'
            f'<meta property="og:type" content="{html.escape(og_type, quote=True)}">'
            f'<meta property="og:title" content="{full_title}"><meta property="og:description" content="{meta_description}">'
            f'<meta property="og:url" content="{canonical}"><meta property="og:site_name" content="{html.escape(self.config.site_name, quote=True)}">'
            f'<meta property="og:locale" content="{html.escape(self.config.locale, quote=True)}">'
            '<meta name="twitter:card" content="summary">'
            f"{verification}{robots}{ads}{analytics}{schema}<style>{css}</style></head>"
            '<body><a class="skip-link" href="#content">Skip to content</a>'
            f"{self._header()}<main id=\"content\">{body}</main>{self._footer()}</body></html>"
        )

    def _header(self) -> str:
        site = html.escape(self.config.site_name)
        return (
            '<header class="site-header"><div class="shell nav-wrap">'
            f'<a class="brand" href="/">{site}</a><nav aria-label="Primary">'
            '<a href="/articles/">Articles</a><a href="/guides/">Guides</a>'
            '<a href="/research/">Research</a><a href="/notes/">Notes</a><a href="/topics/">Topics</a>'
            '<a href="/about/">About</a></nav></div></header>'
        )

    def _footer(self) -> str:
        year = datetime.now(timezone.utc).year
        site = html.escape(self.config.site_name)
        return (
            '<footer class="site-footer"><div class="shell footer-grid"><div>'
            f"<strong>{site}</strong><p>{html.escape(self.config.tagline)}</p></div>"
            '<nav aria-label="Footer"><a href="/editorial-policy/">Editorial policy</a>'
            '<a href="/methodology/">Methodology</a><a href="/privacy/">Privacy</a>'
            '<a href="/terms/">Terms</a><a href="/disclaimer/">Disclaimer</a>'
            '<a href="/contact/">Contact</a></nav></div>'
            f'<div class="shell copyright">© {year} {site}. Educational content, not investment advice.</div></footer>'
        )

    def _website_schema(self) -> dict[str, object]:
        return {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": self.config.site_name,
            "url": f"{self.config.base_url}/",
            "description": self.config.description,
            "publisher": {
                "@type": "Organization",
                "name": self.config.publisher_name or self.config.site_name,
            },
        }

    def _article_schema(
        self,
        item: ContentArtifact,
        route: str,
        published: datetime,
        modified: datetime,
    ) -> dict[str, object]:
        return {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": item.title,
            "description": item.summary,
            "datePublished": published.isoformat(),
            "dateModified": modified.isoformat(),
            "mainEntityOfPage": f"{self.config.base_url}{route}",
            "author": {
                "@type": "Organization",
                "name": self.config.publisher_name or self.config.site_name,
            },
            "publisher": {
                "@type": "Organization",
                "name": self.config.publisher_name or self.config.site_name,
            },
            "keywords": list(item.tags),
        }

    def _sitemap(self, routes: list[str], artifacts: list[ContentArtifact]) -> str:
        artifact_dates = {
            _route_for(item): (item.updated_at or item.created_at).date().isoformat()
            for item in artifacts
        }
        entries = "".join(
            "<url>"
            f"<loc>{html.escape(self.config.base_url + route)}</loc>"
            + (f"<lastmod>{artifact_dates[route]}</lastmod>" if route in artifact_dates else "")
            + "</url>"
            for route in sorted(set(routes))
            if route != "/404.html"
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>"
        )

    def _feed(self, artifacts: list[ContentArtifact]) -> str:
        items = []
        for item in artifacts[:20]:
            url = f"{self.config.base_url}{_route_for(item)}"
            updated = (item.updated_at or item.created_at).astimezone(timezone.utc)
            items.append(
                "<entry>"
                f"<title>{html.escape(item.title)}</title>"
                f'<link href="{html.escape(url, quote=True)}"/>'
                f"<id>{html.escape(url)}</id>"
                f"<updated>{updated.isoformat()}</updated>"
                f"<summary>{html.escape(item.summary)}</summary>"
                "</entry>"
            )
        updated = (
            (artifacts[0].updated_at or artifacts[0].created_at).astimezone(timezone.utc)
            if artifacts
            else datetime.now(timezone.utc)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<title>{html.escape(self.config.site_name)}</title>"
            f'<link href="{html.escape(self.config.base_url + "/feed.xml", quote=True)}" rel="self"/>'
            f'<link href="{html.escape(self.config.base_url + "/", quote=True)}"/>'
            f"<id>{html.escape(self.config.base_url + '/')}</id>"
            f"<updated>{updated.isoformat()}</updated>"
            + "".join(items)
            + "</feed>"
        )

    @staticmethod
    def _css() -> str:
        return (
            ":root{--ink:#15171a;--muted:#626a73;--line:#dfe3e7;--paper:#fff;--soft:#f5f7f8;"
            "--accent:#134e4a;--accent2:#0f766e;--max:1120px}"
            "*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);"
            "color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "font-size:17px;line-height:1.72}a{color:var(--accent);text-underline-offset:3px}"
            ".shell,main{width:min(calc(100% - 40px),var(--max));margin:auto}.skip-link{position:absolute;left:-9999px}"
            ".skip-link:focus{left:16px;top:16px;z-index:10;background:#fff;padding:10px;border:1px solid var(--line)}"
            ".site-header{border-bottom:1px solid var(--line);background:rgba(255,255,255,.96);position:sticky;top:0;z-index:5}"
            ".nav-wrap{display:flex;align-items:center;justify-content:space-between;min-height:68px;gap:28px}"
            ".brand{font-weight:800;color:var(--ink);text-decoration:none;letter-spacing:-.02em}.nav-wrap nav{display:flex;gap:20px;flex-wrap:wrap}"
            ".nav-wrap nav a,.site-footer nav a{text-decoration:none;color:var(--muted);font-size:.92rem}"
            "main{padding:52px 0 76px}.hero{max-width:850px;padding:36px 0 28px}.hero h1,.page-head h1,.article-head h1{"
            "font-size:clamp(2.4rem,6vw,5rem);line-height:1.02;letter-spacing:-.05em;margin:.2em 0}.hero p{font-size:1.18rem;color:var(--muted)}"
            ".eyebrow{text-transform:uppercase;letter-spacing:.13em;font-size:.74rem!important;font-weight:800;color:var(--accent2)!important}"
            ".hero-actions{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-top:28px}.button{display:inline-block;background:var(--ink);"
            "color:#fff;text-decoration:none;padding:12px 18px;border-radius:7px;font-weight:700}.button.secondary{background:var(--accent)}"
            ".text-link{font-weight:700;text-decoration:none}.trust-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);"
            "border:1px solid var(--line);margin:24px 0 60px}.trust-strip span{background:#fff;padding:16px;text-align:center;font-size:.88rem;font-weight:700}"
            ".section-head{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:58px 0 18px}.section-head h2{margin:.1em 0;font-size:2rem}"
            ".card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.card{border:1px solid var(--line);border-radius:10px;padding:22px;"
            "background:#fff}.card h3{font-size:1.28rem;line-height:1.3;margin:.35em 0}.card h3 a{color:var(--ink);text-decoration:none}.card p{color:var(--muted)}"
            ".card-meta{display:flex;gap:12px;color:var(--muted);font-size:.78rem;margin-top:18px}.compact .card{padding:18px}.category-grid{display:grid;"
            "grid-template-columns:repeat(3,1fr);gap:18px}.category{display:flex;justify-content:space-between;align-items:end;border-top:3px solid var(--ink);"
            "padding:18px 0;gap:12px}.category h2{margin:0}.method-card{margin:70px 0 10px;background:var(--soft);padding:34px;border-radius:12px;"
            "display:flex;justify-content:space-between;align-items:center;gap:30px}.method-card>div{max-width:700px}.page-head{max-width:780px;margin-bottom:40px}"
            ".page-head h1{font-size:clamp(2.4rem,5vw,4.2rem)}.page-head>p:last-child,.summary{font-size:1.15rem;color:var(--muted)}"
            ".article-page{max-width:820px;margin:auto}.article-head{margin-bottom:42px}.article-head h1{font-size:clamp(2.5rem,5vw,4.5rem)}"
            ".byline,.tag-row{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:.86rem;margin-top:18px}.tag-row a{text-decoration:none}"
            ".prose{font-family:Georgia,'Times New Roman',serif;font-size:1.08rem}.prose h2{font-family:Inter,ui-sans-serif,system-ui,sans-serif;"
            "font-size:1.75rem;line-height:1.2;margin:2.2em 0 .7em}.prose p{margin:1em 0}.provenance,.disclosure{border:1px solid var(--line);"
            "background:var(--soft);padding:22px;margin-top:42px;border-radius:8px}.provenance ul{padding-left:20px}.related{margin-top:60px}"
            ".archive-list{list-style:none;padding:0}.archive-list li{display:flex;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding:12px 0}"
            ".archive-list li span{color:var(--muted);font-size:.84rem}.topic-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}"
            ".topic-card{display:flex;justify-content:space-between;border:1px solid var(--line);padding:18px;text-decoration:none;border-radius:8px}.topic-card span{color:var(--muted)}"
            ".policy-page{max-width:820px;margin:auto}.not-found{max-width:680px;text-align:center;margin:70px auto}.site-footer{border-top:1px solid var(--line);"
            "padding:42px 0 20px;background:var(--soft)}.footer-grid{display:grid;grid-template-columns:1fr 2fr;gap:40px}.site-footer nav{display:flex;"
            "gap:18px;flex-wrap:wrap;justify-content:flex-end}.copyright{margin-top:28px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:.8rem}"
            "@media(max-width:820px){.nav-wrap{align-items:flex-start;padding:16px 0}.nav-wrap nav{gap:12px}.trust-strip,.card-grid,.category-grid,.topic-grid{"
            "grid-template-columns:1fr 1fr}.method-card{align-items:flex-start;flex-direction:column}.footer-grid{grid-template-columns:1fr}.site-footer nav{justify-content:flex-start}}"
            "@media(max-width:560px){body{font-size:16px}.shell,main{width:min(calc(100% - 28px),var(--max))}.nav-wrap{display:block}.nav-wrap nav{margin-top:10px}"
            ".trust-strip,.card-grid,.category-grid,.topic-grid{grid-template-columns:1fr}.section-head{align-items:flex-start;flex-direction:column}.hero{padding-top:16px}}"
        )

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def assess_adsense_readiness(
    config: StaticWebsiteConfig,
    artifacts: list[ContentArtifact],
) -> SiteReadinessReport:
    """Internal launch gate; this is intentionally stricter than a claim of Google approval."""
    safe = all(item.public_safe for item in artifacts)
    substantive = [item for item in artifacts if item.kind is not ContentKind.NOTE and _word_count(item) >= 350]
    kinds = {item.kind for item in artifacts}
    unique_routes = len({_route_for(item) for item in artifacts}) == len(artifacts)
    checks = {
        "all_content_public_safe": safe,
        "at_least_10_published_items": len(artifacts) >= 10,
        "at_least_8_substantive_items": len(substantive) >= 8,
        "articles_guides_and_research_present": {
            ContentKind.ARTICLE,
            ContentKind.GUIDE,
            ContentKind.RESEARCH,
        }.issubset(kinds),
        "unique_public_routes": unique_routes,
        "clear_static_navigation_and_policy_pages": True,
        "sitemap_feed_robots_and_ads_txt_generated": True,
        "structured_metadata_generated": True,
    }
    parsed = urlparse(config.base_url)
    host = (parsed.hostname or "").lower()
    placeholder_host = (
        host in {"example.com", "www.example.com", "localhost", "127.0.0.1"}
        or host.endswith(".sslip.io")
        or host.endswith(".nip.io")
    )
    external_dependencies: list[str] = []
    if parsed.scheme != "https" or placeholder_host:
        external_dependencies.append("production_https_domain")
    if not config.contact_email:
        external_dependencies.append("public_contact_email")
    if not config.adsense_client:
        external_dependencies.append("adsense_publisher_id")
    if not config.indexable:
        external_dependencies.append("enable_indexing_for_production")
    warnings = []
    if config.analytics_measurement_id is None:
        warnings.append("analytics_not_configured")
    if config.google_site_verification is None:
        warnings.append("search_console_verification_not_configured")
    code_ready = all(checks.values())
    return SiteReadinessReport(
        code_ready=code_ready,
        ready_for_adsense_connection=code_ready and not external_dependencies,
        checks=checks,
        external_dependencies=tuple(external_dependencies),
        warnings=tuple(warnings),
    )


def _route_for(item: ContentArtifact) -> str:
    prefix = {
        ContentKind.RESEARCH: "research",
        ContentKind.NOTE: "notes",
        ContentKind.GUIDE: "guides",
        ContentKind.ARTICLE: "articles",
    }[item.kind]
    return f"/{prefix}/{item.slug}/"


def _tag_slug(tag: str) -> str:
    cleaned = _TAG_SLUG_RE.sub("-", tag.strip().lower()).strip("-")
    return cleaned[:80]


def _word_count(item: ContentArtifact) -> int:
    text = " ".join(
        [item.title, item.summary]
        + [section.heading for section in item.sections]
        + [paragraph for section in item.sections for paragraph in section.paragraphs]
    )
    return len(re.findall(r"\b[\w'-]+\b", text))
