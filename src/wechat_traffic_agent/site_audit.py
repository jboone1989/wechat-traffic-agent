"""Deterministic post-build audit for the static Website channel."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict


RUNTIME_INTERNAL_ROUTES = frozenset({
    "/live", "/live/",
    "/neural", "/neural/",
    "/brain", "/brain/",
    "/screeps", "/screeps/",
    "/agents/ferro/live", "/agents/ferro/live/",
    "/agents/ferro/neural", "/agents/ferro/neural/",
    "/agents/ferro/brain", "/agents/ferro/brain/",
    "/agents/ferro/screeps", "/agents/ferro/screeps/",
})


class StaticSiteAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks: dict[str, bool]
    html_pages: int
    internal_links_checked: int
    broken_links: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.has_title = False
        self.has_description = False
        self.has_canonical = False
        self.noindex = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self.has_title = True
        elif tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])
        elif tag == "link" and values.get("rel", "").lower() == "canonical":
            self.has_canonical = bool(values.get("href"))
        elif tag == "meta":
            name = values.get("name", "").lower()
            if name == "description":
                self.has_description = bool(values.get("content"))
            elif name == "robots" and "noindex" in values.get("content", "").lower():
                self.noindex = True


def audit_static_site(root: Path, *, expect_indexable: bool) -> StaticSiteAudit:
    """Audit files and internal links without network access."""
    required = {
        "index.html",
        "404.html",
        "robots.txt",
        "sitemap.xml",
        "feed.xml",
        "ads.txt",
        "about/index.html",
        "editorial-policy/index.html",
        "methodology/index.html",
        "privacy/index.html",
        "terms/index.html",
        "disclaimer/index.html",
        "contact/index.html",
    }
    required_files = all((root / relative).is_file() for relative in required)
    html_files = sorted(root.rglob("*.html")) if root.is_dir() else []
    metadata_ok = True
    index_policy_ok = True
    broken: set[str] = set()
    links_checked = 0

    for page in html_files:
        parser = _PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        metadata_ok = metadata_ok and parser.has_title and parser.has_description and parser.has_canonical
        is_404 = page == root / "404.html"
        if expect_indexable and not is_404:
            index_policy_ok = index_policy_ok and not parser.noindex
        elif not expect_indexable:
            index_policy_ok = index_policy_ok and parser.noindex
        for href in parser.hrefs:
            target = _internal_target(root, href)
            if target is None:
                continue
            links_checked += 1
            if not target.exists():
                relative_page = page.relative_to(root).as_posix()
                broken.add(f"{relative_page} -> {href}")

    robots_text = (root / "robots.txt").read_text(encoding="utf-8") if (root / "robots.txt").is_file() else ""
    robots_ok = "Disallow: /" not in robots_text if expect_indexable else "Disallow: /" in robots_text
    sitemap_ok = (root / "sitemap.xml").is_file() and "<urlset" in (root / "sitemap.xml").read_text(encoding="utf-8")
    feed_ok = (root / "feed.xml").is_file() and "<feed" in (root / "feed.xml").read_text(encoding="utf-8")
    checks = {
        "required_public_files_present": required_files,
        "html_metadata_present": bool(html_files) and metadata_ok,
        "index_policy_consistent": bool(html_files) and index_policy_ok and robots_ok,
        "sitemap_valid_shape": sitemap_ok,
        "feed_valid_shape": feed_ok,
        "no_broken_internal_links": not broken,
    }
    warnings: list[str] = []
    if links_checked == 0:
        warnings.append("no_internal_links_checked")
    return StaticSiteAudit(
        passed=all(checks.values()),
        checks=checks,
        html_pages=len(html_files),
        internal_links_checked=links_checked,
        broken_links=tuple(sorted(broken)),
        warnings=tuple(warnings),
    )


def _internal_target(root: Path, href: str) -> Path | None:
    if not href.startswith("/") or href.startswith("//"):
        return None
    path = urlsplit(href).path
    if path in RUNTIME_INTERNAL_ROUTES:
        return None
    if not path:
        return root / "index.html"
    if path == "/":
        return root / "index.html"
    relative = path.lstrip("/")
    if path.endswith("/"):
        return root / relative / "index.html"
    return root / relative
