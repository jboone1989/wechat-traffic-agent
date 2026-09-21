from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.survival_resources import SurvivalResourceManager


NOW = datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc)


def _normal_compute(monkeypatch):
    monkeypatch.setattr(
        SurvivalResourceManager, "_memory",
        staticmethod(lambda: (8 * 1024**3, 6 * 1024**3)),
    )
    monkeypatch.setattr(SurvivalResourceManager, "_load", staticmethod(lambda: 0.2))


def test_55_percent_starts_early_storage_reclaim(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=55 * 1024**3, free=45 * 1024**3),
    )
    _normal_compute(monkeypatch)
    manager = SurvivalResourceManager(tmp_path / "state")
    observed = manager.observe(now=NOW)

    assert observed["pressure"] == "WATCH"
    storage = next(row for row in observed["resources"] if row["domain"] == "STORAGE")
    assert storage["maintenance_mode"] == "INITIAL"
    needs = {row["action"]: row for row in observed["needs"]}
    assert needs["RECLAIM_TRANSIENT_STORAGE"]["status"] == "APPROVED"
    assert "DEEP_RECLAIM_STORAGE" not in needs


def test_85_percent_starts_deep_storage_reclaim(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=85 * 1024**3, free=15 * 1024**3),
    )
    _normal_compute(monkeypatch)
    manager = SurvivalResourceManager(tmp_path / "state")
    observed = manager.observe(now=NOW)

    assert observed["pressure"] == "CRITICAL"
    storage = next(row for row in observed["resources"] if row["domain"] == "STORAGE")
    assert storage["maintenance_mode"] == "DEEP"
    needs = {row["action"]: row for row in observed["needs"]}
    assert needs["DEEP_RECLAIM_STORAGE"]["status"] == "APPROVED"


def test_reserved_filesystem_space_counts_toward_survival_pressure(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(tmp_path))
    # Linux filesystems can reserve blocks: used + available need not equal total.
    # Survival pressure follows operator-visible available headroom, not raw used blocks.
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.shutil.disk_usage",
        lambda _: SimpleNamespace(total=100 * 1024**3, used=80 * 1024**3, free=10 * 1024**3),
    )
    _normal_compute(monkeypatch)
    manager = SurvivalResourceManager(tmp_path / "state")
    observed = manager.observe(now=NOW)
    storage = next(row for row in observed["resources"] if row["domain"] == "STORAGE")
    assert storage["used_ratio"] == 0.9
    assert storage["maintenance_mode"] == "DEEP"
    assert observed["pressure"] == "CRITICAL"
