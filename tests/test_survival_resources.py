from datetime import datetime, timezone
from pathlib import Path
import os

from wechat_traffic_agent.survival_optimization import HostRecoveryPlanner, persist_runtime_footprint
from wechat_traffic_agent.survival_resources import SurvivalNode, SurvivalNodeRegistry

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def test_migration_waits_for_local_recovery(tmp_path):
    registry = SurvivalNodeRegistry(state_root=tmp_path)
    registry.configure(SurvivalNode(
        node_id="backup-a",
        provider="free-cloud",
        host="example.invalid",
        port=22,
        user="ubuntu",
        credential_ref="secret:backup-a",
        min_free_gib=20.0,
    ))

    watching = registry.migration_intent(
        resource_pressure="WATCH",
        recovery_status="NOT_NEEDED",
    )
    assert watching["status"] == "STANDBY_AVAILABLE"

    recoverable = registry.migration_intent(
        resource_pressure="CRITICAL",
        recovery_status="RECOVERED",
    )
    assert recoverable["status"] == "OPTIMIZE_FIRST"

    exhausted = registry.migration_intent(
        resource_pressure="CRITICAL",
        recovery_status="EXHAUSTED",
    )
    assert exhausted["status"] == "MIGRATION_CANDIDATE"
    assert exhausted["target_node_id"] == "backup-a"


def test_recovery_audit_separates_regenerable_and_rollback_material(tmp_path):
    home = tmp_path / "home"
    scratch = tmp_path / "tmp"
    state = tmp_path / "state"
    cache = home / ".cache" / "pip"
    cache.mkdir(parents=True)
    (cache / "wheel.bin").write_bytes(b"x" * 4096)

    old_browser = home / ".cache" / "ms-playwright" / "chromium-100"
    new_browser = home / ".cache" / "ms-playwright" / "chromium-200"
    old_browser.mkdir(parents=True)
    new_browser.mkdir(parents=True)
    (old_browser / "chrome").write_bytes(b"x" * 2048)
    (new_browser / "chrome").write_bytes(b"x" * 1024)

    old_venv = home / "content-agent-prod-venv-old"
    old_venv.mkdir(parents=True)
    (old_venv / "python").write_bytes(b"x" * 8192)

    scratch.mkdir()
    stale = scratch / "content-agent-test-old"
    stale.mkdir()
    (stale / "artifact").write_bytes(b"x" * 1024)
    old = NOW.timestamp() - 8 * 3600
    os.utime(stale, (old, old))

    planner = HostRecoveryPlanner(
        state_root=state,
        home_root=home,
        tmp_root=scratch,
        target_free_gib=9999,
    )
    audit = planner.audit(now=NOW)
    by_kind = {}
    for row in audit["candidates"]:
        by_kind.setdefault(row["kind"], []).append(row)

    assert any(row["auto_allowed"] for row in by_kind["PACKAGE_CACHE"])
    assert len(by_kind["OLD_BROWSER_CACHE"]) == 1
    assert by_kind["OLD_BROWSER_CACHE"][0]["path"].endswith("chromium-100")
    assert by_kind["STALE_TEST_SCRATCH"][0]["auto_allowed"] is True
    assert by_kind["DEPLOYMENT_RETENTION"][0]["auto_allowed"] is False


def test_recovery_optimizer_only_removes_auto_allowed_candidates(tmp_path):
    home = tmp_path / "home"
    scratch = tmp_path / "tmp"
    state = tmp_path / "state"
    cache = home / ".cache" / "pip"
    cache.mkdir(parents=True)
    (cache / "blob").write_bytes(b"x" * 1024)
    old_venv = home / "content-agent-prod-venv-old"
    old_venv.mkdir(parents=True)
    (old_venv / "keep").write_bytes(b"x" * 1024)
    scratch.mkdir()

    planner = HostRecoveryPlanner(
        state_root=state,
        home_root=home,
        tmp_root=scratch,
        target_free_gib=9999,
    )
    result = planner.optimize(now=NOW)

    assert not cache.exists()
    assert old_venv.exists()
    assert result["status"] == "MANAGED_RETENTION_REQUIRED"


def test_runtime_footprint_unlocks_only_stale_deployment_reclaim(tmp_path):
    home = tmp_path / "home"
    scratch = tmp_path / "tmp"
    state = tmp_path / "state"
    release_old = tmp_path / "release-old"
    release_new = tmp_path / "release-new"
    rollback = home / "content-agent-prod-venv-rollback"
    active = home / "content-agent-prod-venv-active"
    referenced = home / "content-agent-prod-venv-referenced"
    stale = home / "content-agent-prod-venv-stale"
    for path in (scratch, release_old, release_new, rollback, active, referenced, stale):
        path.mkdir(parents=True)
    (rollback / "x").write_bytes(b"x" * 1024)
    (active / "x").write_bytes(b"x" * 1024)
    (referenced / "x").write_bytes(b"x" * 1024)
    (stale / "x").write_bytes(b"x" * 1024)
    (active / "bin").mkdir()
    (active / "bin" / "pip").write_text(
        f"#!{referenced}/bin/python\n",
        encoding="utf-8",
    )

    persist_runtime_footprint(
        state_root=state, release_root=release_old, venv_root=rollback, now=NOW,
    )
    persist_runtime_footprint(
        state_root=state, release_root=release_new, venv_root=active, now=NOW,
    )
    planner = HostRecoveryPlanner(
        state_root=state, home_root=home, tmp_root=scratch, target_free_gib=9999,
    )
    audit = planner.audit(now=NOW)
    deployment = [row for row in audit["candidates"] if row["kind"] == "STALE_DEPLOYMENT_ENV"]

    assert len(deployment) == 1
    assert deployment[0]["path"] == str(stale)
    assert deployment[0]["auto_allowed"] is True
