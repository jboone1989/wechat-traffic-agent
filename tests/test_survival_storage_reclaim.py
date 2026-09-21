from datetime import datetime, timezone
import os

from wechat_traffic_agent.survival_resources import SurvivalResourceManager


NOW = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)


def test_reclaim_storage_deletes_only_stale_content_venvs(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    old = []
    for i in range(5):
        path = artifact_root / f"content-agent-prod-venv-old-{i}"
        path.mkdir()
        (path / "payload.bin").write_bytes(b"x" * 4096)
        os.utime(path, (NOW.timestamp() - 3 * 86400 - i, NOW.timestamp() - 3 * 86400 - i))
        old.append(path)
    foreign = artifact_root / "agent-network-prod-venv-old"
    foreign.mkdir()
    (foreign / "payload.bin").write_bytes(b"x" * 4096)
    os.utime(foreign, (NOW.timestamp() - 4 * 86400, NOW.timestamp() - 4 * 86400))

    monkeypatch.setenv("CONTENT_SURVIVAL_ARTIFACT_ROOT", str(artifact_root))
    manager = SurvivalResourceManager(tmp_path / "state")
    result = manager._reclaim_stale_content_venvs(now=NOW)

    assert result["deleted_venvs"] == 3
    assert len(list(artifact_root.glob("content-agent-prod-venv-*"))) == 2
    assert foreign.exists()
    assert result["reclaimed_bytes"] >= 3 * 4096


def test_reclaim_storage_ignores_recent_content_venv(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    recent = artifact_root / "content-agent-prod-venv-recent"
    recent.mkdir()
    (recent / "payload.bin").write_bytes(b"x" * 1024)
    os.utime(recent, (NOW.timestamp(), NOW.timestamp()))

    monkeypatch.setenv("CONTENT_SURVIVAL_ARTIFACT_ROOT", str(artifact_root))
    manager = SurvivalResourceManager(tmp_path / "state")
    result = manager._reclaim_stale_content_venvs(now=NOW)

    assert recent.exists()
    assert result["deleted_venvs"] == 0
