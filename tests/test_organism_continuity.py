from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.organism_continuity import (
    OrganismContinuityError,
    OrganismContinuityService,
)


def _seed_state(root: Path) -> str:
    app = root / "application"
    content = root / "content"
    (app / "network").mkdir(parents=True, exist_ok=True)
    (content / "x").mkdir(parents=True, exist_ok=True)
    (content / "world").mkdir(parents=True, exist_ok=True)
    organism_id = "ferro-organism:test-001"
    (app / "cortex_continuity.json").write_text(
        json.dumps({"organism_id": organism_id, "swap_count": 2}) + "\n",
        encoding="utf-8",
    )
    (app / "wakeful.json").write_text(
        json.dumps({"tick": 42, "goal": "survive"}) + "\n",
        encoding="utf-8",
    )
    (app / "fly_connectome_state.npz").write_bytes(b"connectome-state")
    (app / "synthetic_neural_development.json").write_text(
        json.dumps({"organs": {"organ_001": {"state": "EMERGENT_ORGAN"}}}) + "\n",
        encoding="utf-8",
    )
    (app / "network" / "identity.json").write_text(
        json.dumps(
            {
                "agent_id": "ferro.network.cai",
                "key_id": "key:test",
                "public_key": "public:test",
                "private_key": "private:test",
                "created_at": "2026-09-22T00:00:00Z",
                "revision": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (app / "network" / "identity.recovery.json").write_text(
        json.dumps(
            {
                "agent_id": "ferro.network.cai",
                "keys": [
                    {
                        "key_id": "recovery:test",
                        "public_key": "recovery-public",
                        "private_key": "recovery-private",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (content / "x" / "growth_mission.json").write_text(
        json.dumps({"status": "ACTIVE", "target": 5000}) + "\n",
        encoding="utf-8",
    )
    (content / "world" / "latest.json").write_text(
        json.dumps({"world_id": "WORLD_TEST", "decision": "ADOPT"}) + "\n",
        encoding="utf-8",
    )
    (root / "secrets").mkdir(parents=True, exist_ok=True)
    (root / "secrets" / "deepseek.key").write_text("must-not-leave-primary\n")
    (root / "content-agent.env").write_text("DEEPSEEK_API_KEY=must-not-leave-primary\n")
    (root / "browser").mkdir(parents=True, exist_ok=True)
    (root / "browser" / "cookies.json").write_text('{"cookie":"secret"}\n')

    store = CanonicalMemoryStore(root)
    store.append_memory(
        {
            "memory_id": "memory:l3:1",
            "memory_type": "SEMANTIC",
            "kind": "FACT",
            "text": "Ferro survives replacement nodes.",
            "created_at": "2026-09-22T00:00:00Z",
            "source_type": "OWNER_CONVERSATION",
            "source_ref": "owner:l3",
            "topics": ["continuity"],
        }
    )
    return organism_id


def test_bundle_restores_same_organism_memory_brain_and_mission(tmp_path):
    source = tmp_path / "source"
    organism_id = _seed_state(source)
    service = OrganismContinuityService(source)
    bundle = tmp_path / "ferro-standby.bin"
    exported = service.export_bundle(bundle)

    assert exported["organism_id"] == organism_id
    assert exported["bytes"] > 0
    assert exported["requires_operator_managed_host"] is True

    destination = tmp_path / "replacement"
    restored = OrganismContinuityService.restore_bundle(
        bundle,
        destination,
        expected_sha256=exported["sha256"],
    )
    assert restored["status"] == "RESTORED"
    assert restored["organism_id"] == organism_id
    assert restored["memory_state_hash"] == exported["memory_state_hash"]
    assert (
        destination / "application" / "fly_connectome_state.npz"
    ).read_bytes() == b"connectome-state"
    assert json.loads(
        (destination / "content" / "x" / "growth_mission.json").read_text()
    )["target"] == 5000
    assert json.loads(
        (destination / "application" / "network" / "identity.json").read_text()
    )["agent_id"] == "ferro.network.cai"


def test_bundle_excludes_provider_browser_and_account_secrets(tmp_path):
    source = tmp_path / "source"
    _seed_state(source)
    bundle = tmp_path / "ferro-standby.bin"
    OrganismContinuityService(source).export_bundle(bundle)

    with zipfile.ZipFile(bundle, "r") as archive:
        names = set(archive.namelist())
        joined = "\n".join(sorted(names))
        assert "secrets/" not in joined
        assert "browser/" not in joined
        assert "accounts/" not in joined
        assert "content-agent.env" not in joined
        assert "llm_audit" not in joined
        assert "llm_resource_pool" not in joined
        assert "state/application/network/identity.json" in names
        assert "memory/continuity.key" in names


def test_restore_rejects_wrong_outer_hash(tmp_path):
    source = tmp_path / "source"
    _seed_state(source)
    bundle = tmp_path / "ferro-standby.bin"
    OrganismContinuityService(source).export_bundle(bundle)
    with pytest.raises(OrganismContinuityError, match="bundle hash mismatch"):
        OrganismContinuityService.restore_bundle(
            bundle,
            tmp_path / "replacement",
            expected_sha256="sha256:" + "0" * 64,
        )


def test_restore_rejects_tampered_member(tmp_path):
    source = tmp_path / "source"
    _seed_state(source)
    bundle = tmp_path / "ferro-standby.bin"
    OrganismContinuityService(source).export_bundle(bundle)
    tampered = tmp_path / "tampered.bin"
    with zipfile.ZipFile(bundle, "r") as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "state/application/wakeful.json":
                data += b"tamper"
            dst.writestr(item, data)
    with pytest.raises(OrganismContinuityError, match="file (size|hash) mismatch"):
        OrganismContinuityService.inspect_bundle(tampered)


def test_restore_requires_empty_destination(tmp_path):
    source = tmp_path / "source"
    _seed_state(source)
    bundle = tmp_path / "ferro-standby.bin"
    OrganismContinuityService(source).export_bundle(bundle)
    destination = tmp_path / "replacement"
    destination.mkdir()
    (destination / "existing").write_text("occupied")
    with pytest.raises(OrganismContinuityError, match="not empty"):
        OrganismContinuityService.restore_bundle(bundle, destination)
