from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable
import zipfile

from .memory_system import CanonicalMemoryStore, MEMORY_SCHEMA_VERSION


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class MemoryContinuityError(RuntimeError):
    pass


class MemoryContinuityService:
    """M10 canonical-memory continuity with an authenticated hash-linked checkpoint chain.

    The continuity key is a small critical secret that must travel through the trusted identity
    continuity path. Retrieval indexes are deliberately excluded from continuity authority and are
    rebuilt after restore.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        store: CanonicalMemoryStore | None = None,
        signing_key: bytes | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.store = store or CanonicalMemoryStore(self.state_root)
        self.root = self.store.root
        self.chain_path = self.root / "continuity_chain.jsonl"
        self.key_path = self.root / "continuity.key"
        self._key = signing_key or self._load_or_create_key()
        if len(self._key) < 32:
            raise ValueError("memory continuity key must be at least 32 bytes")

    @property
    def verification_key(self) -> bytes:
        """Return key material for an already trusted replication channel, never public output."""
        return bytes(self._key)

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes()
            if len(key) < 32:
                raise MemoryContinuityError("invalid continuity key")
            return key
        key = os.urandom(32)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        return key

    def canonical_digest(self) -> dict[str, Any]:
        with self.store._connect() as connection:
            meta = [dict(row) for row in connection.execute(
                "SELECT key, value FROM schema_meta ORDER BY key"
            )]
            memories = [dict(row) for row in connection.execute(
                "SELECT * FROM memories ORDER BY memory_id"
            )]
            entities = [dict(row) for row in connection.execute(
                "SELECT * FROM memory_entities ORDER BY memory_id, entity"
            )]
            topics = [dict(row) for row in connection.execute(
                "SELECT * FROM memory_topics ORDER BY memory_id, topic"
            )]
            events = [dict(row) for row in connection.execute(
                "SELECT * FROM memory_events ORDER BY event_seq"
            )]
        canonical = {
            "schema_meta": meta,
            "memories": memories,
            "memory_entities": entities,
            "memory_topics": topics,
            "memory_events": events,
        }
        encoded = _json(canonical).encode("utf-8")
        return {
            "state_hash": _sha256_bytes(encoded),
            "memory_count": len(memories),
            "event_count": len(events),
        }

    def _chain(self) -> list[dict[str, Any]]:
        if not self.chain_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.chain_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise MemoryContinuityError("invalid continuity record")
                rows.append(value)
        return rows

    def append_checkpoint(
        self,
        *,
        reason: str = "memory-checkpoint",
        critical_refs: Iterable[str] = (),
    ) -> dict[str, Any]:
        chain = self._chain()
        digest = self.canonical_digest()
        body = {
            "sequence": len(chain) + 1,
            "previous_hash": str(chain[-1]["record_hash"]) if chain else "GENESIS",
            "created_at": _utc_iso(),
            "schema_version": MEMORY_SCHEMA_VERSION,
            "state_hash": digest["state_hash"],
            "memory_count": digest["memory_count"],
            "event_count": digest["event_count"],
            "reason": str(reason)[:300],
            "critical_refs": list(dict.fromkeys(
                str(item).strip() for item in critical_refs if str(item).strip()
            )),
        }
        record_hash = _sha256_bytes(_json(body).encode("utf-8"))
        signature = hmac.new(self._key, record_hash.encode("ascii"), hashlib.sha256).hexdigest()
        row = {**body, "record_hash": record_hash, "signature": signature}
        with self.chain_path.open("a", encoding="utf-8") as handle:
            handle.write(_json(row) + "\n")
        return row

    def verify_chain(self, *, require_current_state: bool = False) -> dict[str, Any]:
        chain = self._chain()
        previous = "GENESIS"
        for index, row in enumerate(chain, start=1):
            if int(row.get("sequence") or 0) != index:
                raise MemoryContinuityError("continuity sequence mismatch")
            if str(row.get("previous_hash") or "") != previous:
                raise MemoryContinuityError("continuity previous hash mismatch")
            body = {
                key: row[key]
                for key in (
                    "sequence", "previous_hash", "created_at", "schema_version",
                    "state_hash", "memory_count", "event_count", "reason", "critical_refs",
                )
            }
            record_hash = _sha256_bytes(_json(body).encode("utf-8"))
            if not hmac.compare_digest(record_hash, str(row.get("record_hash") or "")):
                raise MemoryContinuityError("continuity record hash mismatch")
            expected = hmac.new(self._key, record_hash.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, str(row.get("signature") or "")):
                raise MemoryContinuityError("continuity signature mismatch")
            previous = record_hash
        if require_current_state and chain:
            current = self.canonical_digest()
            if current["state_hash"] != str(chain[-1]["state_hash"]):
                raise MemoryContinuityError("canonical memory changed after latest checkpoint")
        return {
            "ok": True,
            "records": len(chain),
            "latest_hash": previous if chain else "GENESIS",
        }

    def _canonical_db_backup(self, target: Path) -> None:
        source = sqlite3.connect(self.store.db_path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        connection = sqlite3.connect(target)
        try:
            # Derived indexes never participate in continuity authority.
            connection.execute("DROP TABLE IF EXISTS memory_fts")
            connection.execute("DROP TABLE IF EXISTS memory_vector_index")
            connection.commit()
        finally:
            connection.close()

    def export_bundle(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = self.append_checkpoint(reason="continuity-export")
        with tempfile.TemporaryDirectory(prefix="ferro-memory-continuity-") as temp_dir:
            temp = Path(temp_dir)
            database = temp / "memory.db"
            chain = temp / "continuity_chain.jsonl"
            self._canonical_db_backup(database)
            shutil.copy2(self.chain_path, chain)
            files = {
                "memory.db": _sha256_bytes(database.read_bytes()),
                "continuity_chain.jsonl": _sha256_bytes(chain.read_bytes()),
            }
            manifest_body = {
                "bundle_schema": 1,
                "created_at": _utc_iso(),
                "state_hash": checkpoint["state_hash"],
                "checkpoint_hash": checkpoint["record_hash"],
                "files": files,
            }
            manifest_hash = _sha256_bytes(_json(manifest_body).encode("utf-8"))
            signature = hmac.new(
                self._key, manifest_hash.encode("ascii"), hashlib.sha256
            ).hexdigest()
            manifest = {
                **manifest_body,
                "manifest_hash": manifest_hash,
                "signature": signature,
            }
            (temp / "manifest.json").write_text(_json(manifest) + "\n", encoding="utf-8")
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(database, "memory.db")
                archive.write(chain, "continuity_chain.jsonl")
                archive.write(temp / "manifest.json", "manifest.json")
        return destination

    @classmethod
    def restore_bundle(
        cls,
        bundle: Path,
        destination_state_root: Path,
        *,
        verification_key: bytes,
    ) -> "MemoryContinuityService":
        destination_state_root = Path(destination_state_root)
        memory_root = destination_state_root / "application" / "memory_system"
        if (memory_root / "memory.db").exists():
            raise MemoryContinuityError("destination already contains canonical memory")
        with tempfile.TemporaryDirectory(prefix="ferro-memory-restore-") as temp_dir:
            temp = Path(temp_dir)
            with zipfile.ZipFile(Path(bundle), "r") as archive:
                names = set(archive.namelist())
                required = {"memory.db", "continuity_chain.jsonl", "manifest.json"}
                if not required.issubset(names):
                    raise MemoryContinuityError("continuity bundle is incomplete")
                for name in required:
                    archive.extract(name, temp)
            manifest = json.loads((temp / "manifest.json").read_text(encoding="utf-8"))
            body = {
                key: manifest[key]
                for key in ("bundle_schema", "created_at", "state_hash", "checkpoint_hash", "files")
            }
            manifest_hash = _sha256_bytes(_json(body).encode("utf-8"))
            if manifest_hash != str(manifest.get("manifest_hash") or ""):
                raise MemoryContinuityError("continuity manifest hash mismatch")
            expected_signature = hmac.new(
                verification_key, manifest_hash.encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, str(manifest.get("signature") or "")):
                raise MemoryContinuityError("continuity manifest signature mismatch")
            for name, expected_hash in dict(manifest.get("files") or {}).items():
                path = temp / name
                if not path.exists() or _sha256_bytes(path.read_bytes()) != str(expected_hash):
                    raise MemoryContinuityError(f"continuity file hash mismatch: {name}")
            memory_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(temp / "memory.db", memory_root / "memory.db")
            shutil.copy2(temp / "continuity_chain.jsonl", memory_root / "continuity_chain.jsonl")
            key_path = memory_root / "continuity.key"
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(verification_key)
        restored = cls(destination_state_root, signing_key=verification_key)
        restored.verify_chain(require_current_state=True)
        if restored.canonical_digest()["state_hash"] != str(manifest["state_hash"]):
            raise MemoryContinuityError("restored canonical state hash mismatch")
        # Rebuildable indexes are recreated locally after canonical restore.
        from .memory_retrieval import MemorySearchIndex
        from .memory_vector import SemanticVectorIndex

        MemorySearchIndex(restored.store).rebuild()
        SemanticVectorIndex(restored.store).rebuild()
        return restored


__all__ = ["MemoryContinuityError", "MemoryContinuityService"]
