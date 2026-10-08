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
    assert len(json.loads(data.inventory.read_text())) == 5
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
    assert len(json.loads(store.inventory.read_text())) == 3


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
    assert "/data/articles.json" in page
    assert (publisher.public / "content-current" / "articles.json").is_file()
    for mounted in ("articles", "guides", "research", "notes", "archive",
                    "topics", "sitemap.xml", "feed.xml", "data"):
        assert (publisher.public / "current" / mounted).is_symlink()
        assert (publisher.public / "current" / mounted).exists()
