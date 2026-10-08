"""Stable public routes owned by editorial content, not the website renderer."""
from __future__ import annotations

from .content_artifact import ContentArtifact, ContentKind


def _route_for(item: ContentArtifact) -> str:
    prefix = {
        ContentKind.RESEARCH: "research",
        ContentKind.NOTE: "notes",
        ContentKind.GUIDE: "guides",
        ContentKind.ARTICLE: "articles",
    }[item.kind]
    return f"/{prefix}/{item.slug}/"
