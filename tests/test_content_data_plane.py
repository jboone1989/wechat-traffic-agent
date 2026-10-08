"""Publishing articles updates the data layer, never rebuilds or swaps the site."""
import asyncio
import json
from pathlib import Path
from datetime import timedelta

import pytest

from test_content_loop import NOW, setup, Runtime
from wechat_traffic_agent.content_publication_store import ContentPublicationStore
from wechat_traffic_agent.web_editorial import website_snapshot


def _bootstrap(tmp_path, publisher):
    data = ContentPublicationStore(
        state_root=tmp_path / "state", public_root=publisher.public,
        config=publisher.config,
    )
    site = publisher.public / "current"
    original = site.resolve(strict=True)
    result = data.bootstrap(site_root=site, legacy_inventory=publisher.inventory_path())
    assert result["status"] == "READY"
    assert data.ready()
    assert data.bootstrap(site_root=site, legacy_inventory=data.inventory)["status"] == "ALREADY_READY"
    assert site.resolve(strict=True) == original
    return data


def test_incremental_publishing_does_not_rebuild_or_redeploy_website(tmp_path, monkeypatch):
    runtime, loop, publisher = setup(tmp_path)
    data = _bootstrap(tmp_path, publisher)
    site = publisher.public / "current"
    original = site.resolve(strict=True)
    old_home = (site / "index.html").read_bytes()
    old_home_mtime = (site / "index.html").stat().st_mtime_ns
    (publisher.public / "releases").mkdir(exist_ok=True)
    original_site_releases = set((publisher.public / "releases").iterdir())
    def rebuild_disabled(*args, **kwargs):
        raise AssertionError("whole-site build must not run for an article")
    monkeypatch.setattr("wechat_traffic_agent.automatic_website.AutomaticAgentWebChannel.build", rebuild_disabled)

    first = asyncio.run(loop.run_once(now=NOW))
    assert first["status"] == "PUBLISHED", first
    assert first["publication"]["content_revision"]
    assert first["publication"]["revision"] == original.name
    assert site.resolve(strict=True) == original
    assert (site / "index.html").read_bytes() == old_home
    assert (site / "index.html").stat().st_mtime_ns == old_home_mtime
    assert set((publisher.public / "releases").iterdir()) == original_site_releases

    url = first["publication"]["outcome_attribution"]["public_route"]
    assert (data.current / url.lstrip("/") / "index.html").is_file()
    catalog = json.loads((data.current / "data" / "articles.json").read_text())
    assert any(row["url"] == url for row in catalog["articles"])
    snap = website_snapshot(publisher.inventory_path(), site, publisher.config.base_url)
    assert len(snap["articles"]) == 4

    old_content = data.current.resolve(strict=True)
    loop.runtime = Runtime(2)
    old_fetch = loop.fetcher
    async def fresh(feeds, now):
        row = await old_fetch(feeds, now)
        row["items"][0]["news_id"] = "news-2"
        return row
    loop.fetcher = fresh
    second = asyncio.run(loop.run_once(now=NOW+timedelta(hours=6)))
    assert second["status"] == "PUBLISHED"
    assert data.current.resolve(strict=True) != old_content
    assert site.resolve(strict=True) == original
    assert set((publisher.public / "releases").iterdir()) == original_site_releases
    assert (site / "index.html").read_bytes() == old_home
    assert data.repo.count() == 5
    assert not (data.current / "articles.json").exists()
    assert len(website_snapshot(publisher.inventory_path(), site, publisher.config.base_url)["articles"]) == 5


def test_failed_article_checks_do_not_switch_content_or_site(tmp_path):
    _, loop, publisher = setup(tmp_path, passes=False)
    store = _bootstrap(tmp_path, publisher)
    site = (publisher.public / "current").resolve()
    content = store.current.resolve()
    outcome = asyncio.run(loop.run_once(now=NOW))
    assert outcome["status"] == "HELD_BY_CHECKS"
    assert (publisher.public / "current").resolve() == site
    assert store.current.resolve() == content
    assert store.repo.count() == 3


def test_migrate_static_shell_to_independent_content_plane(tmp_path):
    import runpy
    _, _, publisher = setup(tmp_path)
    from wechat_traffic_agent.automatic_website import AutomaticAgentWebChannel
    from wechat_traffic_agent.site_cli import _load_artifacts, _curate_web_taxonomy
    old_link = publisher.public / "current"
    full_design = tmp_path / "complete-site-design"
    AutomaticAgentWebChannel(publisher.config).build(
        _curate_web_taxonomy(_load_artifacts(publisher.inventory_path())), full_design
    )
    old_link.unlink()
    old_link.symlink_to(full_design)
    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_website_data_plane.py"
    migrate = runpy.run_path(str(script))["migrate"]
    old = (publisher.public / "current").resolve()
    result = migrate(
        state_root=tmp_path / "state",
        public_root=publisher.public,
        config=publisher.config,
        starter_path=publisher.starter,
    )
    assert result["status"] == "MIGRATED"
    assert result["previous_design_release"] == old.name
    assert result["site_design_release"] != old.name
    page = (publisher.public / "current" / "index.html").read_text()
    assert "data:image/webp;base64" in page
    assert "/api/agent/status?content=articles" in page
    migrated = ContentPublicationStore(state_root=tmp_path / "state", public_root=publisher.public, config=publisher.config)
    assert migrated.inventory.is_file()
    assert migrated.repo.count() == 3
    for mounted in ("articles", "guides", "research", "notes", "archive",
                    "topics", "sitemap.xml", "feed.xml", "data"):
        assert (publisher.public / "current" / mounted).is_symlink()
        assert (publisher.public / "current" / mounted).exists()


def test_sqlite_commit_survives_interrupted_cache_swap(tmp_path, monkeypatch):
    import os
    from wechat_traffic_agent.automatic_website import AutomaticAgentWebChannel
    _, loop, publisher = setup(tmp_path)
    store = _bootstrap(tmp_path, publisher)
    old_revision = store.current.resolve().name
    original_replace = os.replace
    fired = []
    def interrupted(source, destination):
        if Path(destination) == store.current and not fired:
            fired.append(True)
            raise RuntimeError("simulated process restart before cache pointer swap")
        return original_replace(source, destination)
    monkeypatch.setattr("wechat_traffic_agent.content_publication_store.os.replace", interrupted)
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "ERROR"
    assert store.repo.count() == 4
    assert store.current.resolve().name == old_revision
    monkeypatch.setattr("wechat_traffic_agent.content_publication_store.os.replace", original_replace)
    artifact = next(a for a in store.repo.list_artifacts() if a.artifact_id not in {
        "guide-backtest-reading-001", "article-risk-001", "research-model-001"
    })
    # Recover by recomputing the prebuilt immutable cache rather than writing
    # a second copy to the SQLite database.
    response = store.publish(artifact, website_channel=AutomaticAgentWebChannel(publisher.config))
    assert response["status"] == "ALREADY_PUBLISHED"
    assert store.repo.count() == 4
    assert (store.current / response["route"].lstrip("/") / "index.html").is_file()
