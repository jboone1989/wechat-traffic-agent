"""Public article catalog: SQLite records, not generated HTML or articles.json."""
from __future__ import annotations

from pathlib import Path
from wechat_traffic_agent.editorial.article_repository import ArticleRepository
from wechat_traffic_agent.editorial.routing import _route_for


def article_catalog(state_root: Path) -> dict:
    repo = ArticleRepository(
        Path(state_root) / "content" / "automatic_website" / "articles.sqlite3",
    )
    artifacts = repo.list_artifacts()
    public = sorted(
        (a for a in artifacts if a.public_safe),
        key=lambda a: a.updated_at or a.created_at,
        reverse=True,
    )
    return {
        "schema_version": "ferro-content-catalog.v1",
        "source": "sqlite",
        "articles": [
            {
                "artifact_id": a.artifact_id,
                "title": a.title,
                "summary": a.summary,
                "kind": a.kind.value,
                "tags": list(a.tags),
                "url": _route_for(a),
                "updated_at": (a.updated_at or a.created_at).isoformat(),
            }
            for a in public
        ],
    }
