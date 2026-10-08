"""SQLite is the sole authoritative Ferro article inventory."""
import json
from pathlib import Path
import runpy

import pytest

from test_content_loop import setup
from wechat_traffic_agent.article_repository import ArticleRepository
from wechat_traffic_agent.content_publication_store import ContentPublicationStore
from wechat_traffic_agent.site_cli import _load_artifacts
from wechat_traffic_agent.channels.web import _route_for


def test_migrate_all_articles_with_exact_contents_and_topic_index(tmp_path):
    _, _, publisher = setup(tmp_path)
    legacy = _load_artifacts(publisher.starter)
    store = ContentPublicationStore(
        state_root=tmp_path / "state", public_root=publisher.public,
        config=publisher.config,
    )
    before = (publisher.public / "current").resolve()
    report = store.bootstrap(
        site_root=publisher.public / "current", legacy_inventory=publisher.starter
    )
    assert report["status"] == "READY"
    assert store.repo.initialized()
    assert [a.model_dump(mode="json") for a in store.repo.list_artifacts()] == [
        a.model_dump(mode="json") for a in legacy
    ]
    assert _load_artifacts(store.inventory) == legacy
    for a in legacy:
        assert store.repo.get(a.kind.value, a.slug) == a
        for topic in a.tags:
            assert a.artifact_id in store.repo.topics(topic)
    assert (publisher.public / "current").resolve() == before
    assert not (tmp_path / "state/content/automatic_website/articles.json").exists()


def test_sqlite_has_idempotent_identity_and_rejects_route_conflicts(tmp_path):
    _, _, publisher = setup(tmp_path)
    a = _load_artifacts(publisher.starter)
    repo = ArticleRepository(tmp_path / "persist" / "articles.sqlite3")
    assert repo.initialize(a) == len(a)
    with pytest.raises(FileExistsError):
        repo.initialize(a)
    assert repo.insert(a[0]) is False
    with pytest.raises(ValueError, match="identity conflict"):
        repo.insert(a[0].model_copy(update={"title": "A changed public headline"}))
    with pytest.raises(Exception):
        repo.insert(a[0].model_copy(update={"artifact_id": "another-artifact-id"}))
    assert repo.count() == len(a)


def test_idempotent_migration_verifies_public_pages(tmp_path):
    _, _, publisher = setup(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_articles_to_sqlite.py"
    migrate = runpy.run_path(str(script))["migrate"]
    db = ContentPublicationStore(
        state_root=tmp_path / "state", public_root=publisher.public,
        config=publisher.config,
    )
    db.bootstrap(site_root=publisher.public / "current", legacy_inventory=publisher.starter)
    # The live migration tool also supports old private JSON backups.
    source = tmp_path / "state/content/automatic_website/articles.json"
    source.write_text(publisher.starter.read_text(encoding="utf-8"))
    first = migrate(tmp_path / "state", publisher.public)
    second = migrate(tmp_path / "state", publisher.public)
    assert first["status"] == second["status"] == "VERIFIED_SQLITE"
    assert first["article_count"] == second["article_count"] == len(_load_artifacts(publisher.starter))
    assert first["content_hash"] == second["content_hash"]
    assert first["database"].endswith("articles.sqlite3")


def test_live_public_catalog_reads_from_sqlite_not_a_stale_json(tmp_path):
    from starlette.testclient import TestClient
    from wechat_traffic_agent.live_agent import LiveAgentService
    _, _, publisher = setup(tmp_path)
    store = ContentPublicationStore(
        state_root=tmp_path / "state", public_root=publisher.public,
        config=publisher.config,
    )
    store.bootstrap(site_root=publisher.public / "current", legacy_inventory=publisher.starter)
    api = TestClient(LiveAgentService(
        state_root=tmp_path / "state", application=object(), backend=None
    )._app())
    response = api.get("/api/agent/status?content=articles")
    assert response.status_code == 200
    assert response.json()["source"] == "sqlite"
    assert len(response.json()["articles"]) == store.repo.count()
    # Old legacy export may exist during migration but cannot affect the API.
    old_json = store.private_root / "articles.json"
    old_json.write_text("[]")
    same = api.get("/api/agent/status?content=articles")
    assert same.status_code == 200
    assert same.json()["articles"] == response.json()["articles"]


def test_public_catalog_reports_missing_database_instead_of_serving_stale_cache(tmp_path):
    from starlette.testclient import TestClient
    from wechat_traffic_agent.live_agent import LiveAgentService
    api = TestClient(LiveAgentService(
        state_root=tmp_path / "state", application=object(), backend=None
    )._app())
    response = api.get("/api/agent/status?content=articles")
    assert response.status_code == 503
    assert response.json()["error"] == "article_database_unavailable"
