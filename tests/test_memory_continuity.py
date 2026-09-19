from __future__ import annotations

import json
import zipfile

import pytest

from wechat_traffic_agent.memory_continuity import (
    MemoryContinuityError,
    MemoryContinuityService,
)
from wechat_traffic_agent.memory_retrieval import MemorySearchIndex
from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.memory_vector import SemanticVectorIndex


def _seed(root):
    store = CanonicalMemoryStore(root)
    store.append_memory({
        "memory_id": "memory:continuity:1",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Canonical memory survives replacement nodes.",
        "created_at": "2026-09-19T12:00:00Z",
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:1",
        "topics": ["continuity"],
    })
    MemorySearchIndex(store).rebuild()
    SemanticVectorIndex(store).rebuild()
    return store


def test_continuity_chain_is_hash_linked_and_authenticated(tmp_path):
    store = _seed(tmp_path)
    service = MemoryContinuityService(tmp_path, store=store)
    first = service.append_checkpoint(reason="first")
    store.append_memory({
        "memory_id": "memory:continuity:2",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Second canonical fact.",
        "created_at": "2026-09-19T13:00:00Z",
        "source_type": "OWNER_CONVERSATION",
        "source_ref": "operator-chat:2",
    })
    second = service.append_checkpoint(reason="second")
    assert second["previous_hash"] == first["record_hash"]
    assert service.verify_chain(require_current_state=True)["records"] == 2


def test_continuity_chain_detects_tampering(tmp_path):
    store = _seed(tmp_path)
    service = MemoryContinuityService(tmp_path, store=store)
    service.append_checkpoint(reason="stable")
    rows = [json.loads(line) for line in service.chain_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["memory_count"] = 999
    service.chain_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(MemoryContinuityError, match="hash mismatch"):
        service.verify_chain()


def test_bundle_restore_preserves_canonical_state_and_rebuilds_indexes(tmp_path):
    source = tmp_path / "source"
    store = _seed(source)
    service = MemoryContinuityService(source, store=store)
    bundle = service.export_bundle(tmp_path / "memory-continuity.zip")
    expected = service.canonical_digest()

    destination = tmp_path / "replacement"
    restored = MemoryContinuityService.restore_bundle(
        bundle,
        destination,
        verification_key=service.verification_key,
    )
    assert restored.canonical_digest() == expected
    assert restored.verify_chain(require_current_state=True)["ok"] is True
    assert MemorySearchIndex(restored.store).search("replacement nodes")
    assert SemanticVectorIndex(restored.store).search("replacement machine")


def test_bundle_signature_rejects_untrusted_key(tmp_path):
    source = tmp_path / "source"
    service = MemoryContinuityService(source, store=_seed(source))
    bundle = service.export_bundle(tmp_path / "bundle.zip")
    with pytest.raises(MemoryContinuityError, match="signature mismatch"):
        MemoryContinuityService.restore_bundle(
            bundle,
            tmp_path / "replacement",
            verification_key=b"x" * 32,
        )


def test_bundle_file_tamper_is_detected(tmp_path):
    source = tmp_path / "source"
    service = MemoryContinuityService(source, store=_seed(source))
    bundle = service.export_bundle(tmp_path / "bundle.zip")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle, "r") as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "memory.db":
                data += b"tamper"
            dst.writestr(name, data)
    with pytest.raises(MemoryContinuityError, match="file hash mismatch"):
        MemoryContinuityService.restore_bundle(
            tampered,
            tmp_path / "replacement",
            verification_key=service.verification_key,
        )
