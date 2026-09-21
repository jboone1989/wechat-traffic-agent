from datetime import datetime, timedelta, timezone
import os

from wechat_traffic_agent.storage_lifecycle import StorageLifecycle


NOW = datetime(2026, 9, 21, 2, 30, tzinfo=timezone.utc)


def _old(path, *, days=3):
    ts = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


def test_storage_modes_start_early_and_go_deep():
    assert StorageLifecycle.mode(0.54) == "NORMAL"
    assert StorageLifecycle.mode(0.55) == "INITIAL"
    assert StorageLifecycle.mode(0.8499) == "INITIAL"
    assert StorageLifecycle.mode(0.85) == "DEEP"


def test_initial_reclaim_scans_broadly_but_protects_unknown_and_code(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    cache = storage / ".cache"
    cache.mkdir()
    (cache / "blob").write_bytes(b"x" * 8192)
    _old(cache)

    old_venv = storage / "tool-venv-aaaaaaaa"
    old_venv.mkdir()
    (old_venv / "pyvenv.cfg").write_text("home = /usr/bin")
    (old_venv / "payload").write_bytes(b"x" * 4096)
    _old(old_venv, days=5)

    new_venv = storage / "tool-venv-bbbbbbbb"
    new_venv.mkdir()
    (new_venv / "pyvenv.cfg").write_text("home = /usr/bin")
    (new_venv / "payload").write_bytes(b"x" * 4096)
    _old(new_venv, days=2)

    unknown = storage / "important-notes"
    unknown.mkdir()
    (unknown / "notes.txt").write_text("keep me")
    _old(unknown, days=100)

    code = storage / "feature-worktree"
    code.mkdir()
    (code / ".git").write_text("gitdir: /tmp/fake")
    (code / "code.py").write_text("print('keep')")
    _old(code, days=100)

    lifecycle = StorageLifecycle(state)
    result = lifecycle.reclaim(now=NOW, deep=False)

    assert result["mode"] == "INITIAL"
    assert not cache.exists()
    assert not old_venv.exists()
    assert new_venv.exists()
    assert unknown.exists()
    assert code.exists()


def test_deep_reclaim_removes_old_duplicate_generated_artifact(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    old = storage / "widget-deploy-aaaaaaaa"
    old.mkdir()
    (old / "artifact.bin").write_bytes(b"x" * 4096)
    _old(old, days=45)

    newest = storage / "widget-deploy-bbbbbbbb"
    newest.mkdir()
    (newest / "artifact.bin").write_bytes(b"x" * 4096)
    _old(newest, days=10)

    lifecycle = StorageLifecycle(state)
    candidates = lifecycle.inventory(now=NOW, deep=True)
    by_name = {row["name"]: row for row in candidates}

    assert by_name[old.name]["recommended_action"] == "RECLAIM"
    assert by_name[newest.name]["recommended_action"] == "KEEP_OR_LEARN"

    result = lifecycle.reclaim(now=NOW, deep=True)
    assert result["deleted_count"] == 1
    assert not old.exists()
    assert newest.exists()


def test_unknown_objects_are_inventory_learning_candidates_not_blindly_deleted(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    mystery = storage / "mystery-data"
    mystery.mkdir()
    (mystery / "blob").write_bytes(b"x" * 2048)
    _old(mystery, days=200)

    lifecycle = StorageLifecycle(state)
    observed = lifecycle.observe(now=NOW, used_ratio=0.86)
    row = next(x for x in observed["candidates"] if x["name"] == "mystery-data")

    assert row["kind"] == "UNKNOWN"
    assert row["recommended_action"] == "KEEP_OR_LEARN"
    assert observed["unknown_candidate_count"] == 1
    assert mystery.exists()


def test_successful_reclaim_outcome_becomes_storage_knowledge(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    cache = storage / ".cache"
    cache.mkdir()
    (cache / "blob").write_bytes(b"x" * 1024)
    _old(cache)

    lifecycle = StorageLifecycle(state)
    lifecycle.reclaim(now=NOW, deep=False)
    lifecycle.observe(now=NOW + timedelta(minutes=11), used_ratio=0.60)

    status = lifecycle.status()
    profiles = status["knowledge"]["profiles"]
    assert profiles
    assert max(int(row.get("confirmed_survival") or 0) for row in profiles.values()) >= 1


def test_deep_reclaim_uses_git_lifecycle_for_old_detached_worktree(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed")
    subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "seed"],
        check=True, capture_output=True,
    )

    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    worktrees = []
    for index, name in enumerate(("release-aaaaaaaa", "release-bbbbbbbb", "release-cccccccc")):
        path = storage / name
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(path), "HEAD"],
            check=True, capture_output=True,
        )
        _old(path, days=20 - index)
        worktrees.append(path)

    lifecycle = StorageLifecycle(state)
    candidates = lifecycle.inventory(now=NOW, deep=True)
    by_name = {row["name"]: row for row in candidates}

    assert by_name["release-aaaaaaaa"]["kind"] == "CODE_WORKTREE"
    assert by_name["release-aaaaaaaa"]["worktree_reclaimable"] is True
    assert by_name["release-aaaaaaaa"]["recommended_action"] == "RECLAIM"
    assert by_name["release-bbbbbbbb"]["recommended_action"] == "KEEP_OR_LEARN"
    assert by_name["release-cccccccc"]["recommended_action"] == "KEEP_OR_LEARN"

    result = lifecycle.reclaim(now=NOW, deep=True)
    assert result["deleted_count"] == 1
    assert not worktrees[0].exists()
    assert worktrees[1].exists() and worktrees[2].exists()


def test_kind_level_learning_generalizes_reclaim_confidence(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    lifecycle = StorageLifecycle(state)
    for index in range(12):
        lifecycle._record_learning(
            signature=f"storage-profile:env-{index}",
            kind="ENVIRONMENT",
            outcome="SURVIVED_RECLAIM",
            now=NOW,
        )

    venv = storage / "one-off-test-venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin")
    (venv / "payload").write_bytes(b"x" * 1024)
    _old(venv, days=5)

    candidates = lifecycle.inventory(now=NOW, deep=False)
    row = next(item for item in candidates if item["name"] == venv.name)
    assert row["reclaim_confidence"] >= 0.92
    assert row["recommended_action"] == "RECLAIM"


def test_top_level_symlink_target_is_protected_when_idle(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    current = storage / "service-venv-aaaaaaaa"
    current.mkdir()
    (current / "pyvenv.cfg").write_text("home = /usr/bin")
    _old(current, days=10)
    (storage / "service-venv-current").symlink_to(current, target_is_directory=True)

    lifecycle = StorageLifecycle(state)
    candidates = lifecycle.inventory(now=NOW, deep=False)
    row = next(item for item in candidates if item["name"] == current.name)
    assert row["protected"] is True
    assert row["protected_reason"] == "ACTIVE_PROCESS_REFERENCE"
    assert row["recommended_action"] == "KEEP_OR_LEARN"


def test_initial_archive_reclaims_older_duplicate_but_keeps_newest(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    older = storage / "release-aaaaaaaa.zip"
    newer = storage / "release-bbbbbbbb.zip"
    older.write_bytes(b"x" * 1024)
    newer.write_bytes(b"x" * 1024)
    _old(older, days=10)
    _old(newer, days=5)

    lifecycle = StorageLifecycle(state)
    for index in range(12):
        lifecycle._record_learning(
            signature=f"storage-profile:archive-{index}",
            kind="ARCHIVE",
            outcome="SURVIVED_RECLAIM",
            now=NOW,
        )
    candidates = lifecycle.inventory(now=NOW, deep=False)
    by_name = {row["name"]: row for row in candidates}
    assert by_name[older.name]["recommended_action"] == "RECLAIM"
    assert by_name[newer.name]["recommended_action"] == "KEEP_OR_LEARN"


def test_initial_discovers_cache_children_instead_of_protecting_whole_cache(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    cache = storage / ".cache"
    pip_cache = cache / "pip"
    browser_cache = cache / "browser"
    pip_cache.mkdir(parents=True)
    browser_cache.mkdir()
    (pip_cache / "blob").write_bytes(b"x" * 4096)
    (browser_cache / "blob").write_bytes(b"x" * 4096)
    _old(pip_cache, days=4)
    _old(browser_cache, days=4)

    lifecycle = StorageLifecycle(state)
    candidates = lifecycle.inventory(now=NOW, deep=False)
    by_name = {row["name"]: row for row in candidates}
    assert by_name["pip"]["kind"] == "CACHE"
    assert by_name["browser"]["kind"] == "CACHE"
    assert by_name["pip"]["recommended_action"] == "RECLAIM"


def test_backup_family_groups_before_snapshots_and_keeps_two_in_initial(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    backups = storage / "app" / "shared" / "backups"
    live = storage / "app" / "shared" / "data" / "live"
    backups.mkdir(parents=True)
    live.mkdir(parents=True)
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    snapshots = []
    for index, label in enumerate(("alpha", "beta", "gamma")):
        path = backups / f"app-before-{label}-2026091{index}T000000Z.db"
        path.write_bytes(b"x" * 4096)
        _old(path, days=12-index)
        snapshots.append(path)
    current = live / "app.db"
    current.write_bytes(b"x" * 8192)
    os.utime(current, (NOW.timestamp(), NOW.timestamp()))

    lifecycle = StorageLifecycle(state)
    # Seed kind evidence to isolate retention behavior from learning threshold.
    for index in range(120):
        lifecycle._record_learning(
            signature=f"storage-profile:backup-{index}",
            kind="BACKUP",
            outcome="SURVIVED_RECLAIM",
            now=NOW,
        )
    candidates = lifecycle.inventory(now=NOW, deep=False)
    rows = [row for row in candidates if row["kind"] == "BACKUP"]
    assert len(rows) == 3
    assert len({row["family"] for row in rows}) == 1
    reclaim = [row for row in rows if row["recommended_action"] == "RECLAIM"]
    assert len(reclaim) == 1
    assert reclaim[0]["family_rank"] == 2
    assert all(row["backup_live_successor"] for row in rows)


def test_initial_can_reclaim_old_detached_clean_worktree_beyond_five_rollbacks(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed")
    subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "seed"],
        check=True, capture_output=True,
    )

    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    releases = storage / "project" / "releases"
    releases.mkdir(parents=True)
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))

    worktrees = []
    for index in range(7):
        path = releases / f"{index+1:08x}"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(path), "HEAD"],
            check=True, capture_output=True,
        )
        _old(path, days=20-index)
        worktrees.append(path)

    lifecycle = StorageLifecycle(state)
    candidates = lifecycle.inventory(now=NOW, deep=False)
    rows = [row for row in candidates if row["kind"] == "CODE_WORKTREE"]
    reclaim = [row for row in rows if row["recommended_action"] == "RECLAIM"]
    assert len(reclaim) == 2
    assert {row["family_rank"] for row in reclaim} == {5, 6}


def test_declared_references_include_current_editable_python_source(tmp_path, monkeypatch):
    import json
    import sys

    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    source = storage / "runtime" / "releases" / "deadbeef"
    source.mkdir(parents=True)
    site = storage / "venv" / "lib" / "python3.12" / "site-packages"
    dist = site / "agent_runtime-0.3.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "direct_url.json").write_text(json.dumps({
        "url": source.as_uri(),
        "dir_info": {"editable": True},
    }))
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(sys, "path", [str(site)])

    lifecycle = StorageLifecycle(state)
    refs = lifecycle._declared_references()

    assert source.resolve() in refs


def test_worktree_batch_cursor_rotates_instead_of_starving_later_candidates(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))
    lifecycle = StorageLifecycle(state)

    rows = [
        {"path": str(storage / f"release-{index}")}
        for index in range(5)
    ]
    first = lifecycle._select_worktree_batch(
        rows, budget=2, deep=False, now=NOW
    )
    second = lifecycle._select_worktree_batch(
        rows, budget=2, deep=False, now=NOW + timedelta(minutes=1)
    )

    assert first == {str(storage / "release-0"), str(storage / "release-1")}
    assert second == {str(storage / "release-2"), str(storage / "release-3")}


def test_missing_editable_source_becomes_dependency_issue(tmp_path, monkeypatch):
    import json
    import sys

    state = tmp_path / "state"
    state.mkdir()
    storage = tmp_path / "home"
    storage.mkdir()
    missing = storage / "runtime" / "releases" / "missing123"
    site = storage / "venv" / "lib" / "python3.12" / "site-packages"
    dist = site / "agent_runtime-0.3.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "direct_url.json").write_text(json.dumps({
        "url": missing.as_uri(),
        "dir_info": {"editable": True},
    }))
    monkeypatch.setenv("CONTENT_SURVIVAL_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(sys, "path", [str(site)])

    lifecycle = StorageLifecycle(state)
    refs = lifecycle._declared_references()
    issues = lifecycle.status()["declared_dependency_issues"]["issues"]

    assert missing.resolve() not in refs
    assert any(
        row["kind"] == "MISSING_EDITABLE_SOURCE"
        and row["source"] == str(missing.resolve())
        for row in issues
    )
