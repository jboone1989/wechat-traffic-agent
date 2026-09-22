from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import tempfile
from typing import Any, Iterable
import zipfile

from .memory_continuity import MemoryContinuityService


SCHEMA_VERSION = "ferro-organism-continuity.v1"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024

# Only durable identity, cognition, neural substrate, learned policy, and active
# world/mission state belong to organism continuity. Logs, browser/account
# sessions, provider credentials, caches, generated website output, and audit
# exhaust are deliberately excluded.
STATE_ALLOWLIST = (
    "application/cortex_continuity.json",
    "application/longitudinal_individuality.json",
    "application/fly_connectome_state.npz",
    "application/synthetic_neural_development.json",
    "application/developmental_architecture.json",
    "application/neural_organ_handoff.json",
    "application/neural_dreams.json",
    "application/neural_motor.json",
    "application/neural_personality.json",
    "application/neural_life.json",
    "application/neural_taste.json",
    "application/psychodynamics.json",
    "application/neural_extensions.json",
    "application/brain_cortex_competition.json",
    "application/brain_lab.json",
    "application/developmental_agency.json",
    "application/developmental_transfer.json",
    "application/neural_transfer.json",
    "application/open_ended_evolution.json",
    "application/wakeful.json",
    "application/content_mind",
    "application/subject",
    "application/runtime",
    "application/domain_runtime",
    "application/evolution_bridge",
    "application/capability_evolution",
    "application/model_reputation",
    # Network identity is required to resume the same logical public agent.
    # Core standby is therefore restricted to OPERATOR_MANAGED hosts.
    "application/network/identity.json",
    "application/network/identity.recovery.json",
    "content/x",
    "content/world",
    "content/predictions",
    "content/persona",
    "content/editorial_plans",
    "content/website_experiment_evaluations",
    "content/website_engagement",
    "content/channel_evidence",
)

EXCLUDED_SECURITY_CLASSES = (
    "state_root/secrets/**",
    "state_root/accounts/**",
    "state_root/browser/**",
    "state_root/content-agent.env*",
    "application/runtime_authority.key",
    "application/llm_audit/**",
    "application/llm_resource_pool/**",
    "operator tokens and provider credentials",
)


class OrganismContinuityError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _zip_info(name: str, *, mode: int = 0o600) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    return info


class OrganismContinuityService:
    """Create and restore a bounded durable snapshot of the same Ferro organism."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)

    def _read_json(self, relative: str) -> dict[str, Any]:
        path = self.state_root / relative
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise OrganismContinuityError(f"continuity state unavailable: {relative}") from exc
        if not isinstance(value, dict):
            raise OrganismContinuityError(f"continuity state is invalid: {relative}")
        return value

    def organism_id(self) -> str:
        value = self._read_json("application/cortex_continuity.json")
        organism_id = str(value.get("organism_id") or "").strip()
        if not organism_id:
            raise OrganismContinuityError("cortex continuity has no organism_id")
        return organism_id

    def network_identity_summary(self) -> dict[str, str]:
        value = self._read_json("application/network/identity.json")
        agent_id = str(value.get("agent_id") or "").strip()
        key_id = str(value.get("key_id") or "").strip()
        public_key = str(value.get("public_key") or "").strip()
        if not agent_id or not key_id or not public_key:
            raise OrganismContinuityError("network identity is incomplete")
        return {
            "agent_id": agent_id,
            "key_id": key_id,
            "public_key_sha256": _sha256_bytes(public_key.encode("utf-8")),
        }

    @staticmethod
    def _host_instance_id() -> str:
        try:
            raw = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
        except OSError:
            raw = socket.gethostname()
        return "host-instance:" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _iter_state_files(self) -> Iterable[tuple[str, Path]]:
        seen: set[str] = set()
        for relative in STATE_ALLOWLIST:
            source = self.state_root / relative
            if not source.exists():
                continue
            if source.is_symlink():
                raise OrganismContinuityError(f"continuity source is a symlink: {relative}")
            if source.is_file():
                key = Path(relative).as_posix()
                if key not in seen:
                    seen.add(key)
                    yield key, source
                continue
            if not source.is_dir():
                raise OrganismContinuityError(f"continuity source is not a regular path: {relative}")
            for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise OrganismContinuityError(
                        "continuity source contains a symlink: "
                        + path.relative_to(self.state_root).as_posix()
                    )
                if not path.is_file():
                    continue
                key = path.relative_to(self.state_root).as_posix()
                if key in seen:
                    continue
                seen.add(key)
                yield key, path

    def export_bundle(self, destination: Path) -> dict[str, Any]:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        organism_id = self.organism_id()
        identity = self.network_identity_summary()
        memory = MemoryContinuityService(self.state_root)

        with tempfile.TemporaryDirectory(prefix="ferro-organism-continuity-") as temp_dir:
            temp = Path(temp_dir)
            memory_bundle = memory.export_bundle(temp / "memory-continuity.zip")
            memory_digest = memory.canonical_digest()
            continuity_key = memory.verification_key

            files: dict[str, dict[str, Any]] = {}
            tmp_bundle = temp / "organism-continuity.zip"
            with zipfile.ZipFile(
                tmp_bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                for relative, source in self._iter_state_files():
                    data = source.read_bytes()
                    name = "state/" + relative
                    files[name] = {
                        "sha256": _sha256_bytes(data),
                        "bytes": len(data),
                    }
                    archive.writestr(_zip_info(name), data)

                memory_bytes = memory_bundle.read_bytes()
                files["memory/memory-continuity.zip"] = {
                    "sha256": _sha256_bytes(memory_bytes),
                    "bytes": len(memory_bytes),
                }
                archive.writestr(
                    _zip_info("memory/memory-continuity.zip"), memory_bytes
                )
                files["memory/continuity.key"] = {
                    "sha256": _sha256_bytes(continuity_key),
                    "bytes": len(continuity_key),
                }
                archive.writestr(_zip_info("memory/continuity.key"), continuity_key)

                manifest_body = {
                    "schema_version": SCHEMA_VERSION,
                    "organism_id": organism_id,
                    "network_identity": identity,
                    "source_host_instance_id": self._host_instance_id(),
                    "memory_state_hash": str(memory_digest["state_hash"]),
                    "memory_count": int(memory_digest["memory_count"]),
                    "memory_event_count": int(memory_digest["event_count"]),
                    "state_allowlist": list(STATE_ALLOWLIST),
                    "excluded_security_classes": list(EXCLUDED_SECURITY_CLASSES),
                    "files": files,
                }
                manifest_hash = _sha256_bytes(_json(manifest_body).encode("utf-8"))
                manifest = {**manifest_body, "manifest_hash": manifest_hash}
                archive.writestr(
                    _zip_info("manifest.json"),
                    (_json(manifest) + "\n").encode("utf-8"),
                )

            size = tmp_bundle.stat().st_size
            if size <= 0 or size > MAX_BUNDLE_BYTES:
                raise OrganismContinuityError("organism continuity bundle exceeds receiver limit")
            os.replace(tmp_bundle, destination)

        raw = destination.read_bytes()
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(destination),
            "sha256": "sha256:" + _sha256_bytes(raw),
            "bytes": len(raw),
            "organism_id": organism_id,
            "network_identity": identity,
            "memory_state_hash": str(memory_digest["state_hash"]),
            "member_count": len(files),
            "requires_operator_managed_host": True,
        }

    @staticmethod
    def _safe_name(name: str) -> PurePosixPath:
        path = PurePosixPath(str(name))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise OrganismContinuityError("continuity bundle contains an unsafe path")
        return path

    @classmethod
    def inspect_bundle(
        cls, bundle: Path, *, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        bundle = Path(bundle)
        raw = bundle.read_bytes()
        actual = "sha256:" + _sha256_bytes(raw)
        if expected_sha256 and actual != str(expected_sha256):
            raise OrganismContinuityError("organism continuity bundle hash mismatch")
        with zipfile.ZipFile(bundle, "r") as archive:
            names = archive.namelist()
            if "manifest.json" not in names:
                raise OrganismContinuityError("organism continuity bundle has no manifest")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                raise OrganismContinuityError("organism continuity manifest is invalid")
            if str(manifest.get("schema_version") or "") != SCHEMA_VERSION:
                raise OrganismContinuityError("organism continuity schema mismatch")
            manifest_hash = str(manifest.get("manifest_hash") or "")
            body = {key: value for key, value in manifest.items() if key != "manifest_hash"}
            if _sha256_bytes(_json(body).encode("utf-8")) != manifest_hash:
                raise OrganismContinuityError("organism continuity manifest hash mismatch")
            files = manifest.get("files")
            if not isinstance(files, dict):
                raise OrganismContinuityError("organism continuity manifest has no files")
            expected_names = set(files) | {"manifest.json"}
            if set(names) != expected_names:
                raise OrganismContinuityError("organism continuity bundle member set mismatch")
            for name, record in files.items():
                cls._safe_name(name)
                if not isinstance(record, dict):
                    raise OrganismContinuityError("organism continuity file record is invalid")
                data = archive.read(name)
                if int(record.get("bytes") or -1) != len(data):
                    raise OrganismContinuityError(
                        f"organism continuity file size mismatch: {name}"
                    )
                if str(record.get("sha256") or "") != _sha256_bytes(data):
                    raise OrganismContinuityError(
                        f"organism continuity file hash mismatch: {name}"
                    )
        return {
            "sha256": actual,
            "bytes": len(raw),
            "manifest": manifest,
        }

    @classmethod
    def restore_bundle(
        cls,
        bundle: Path,
        destination_state_root: Path,
        *,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        destination_state_root = Path(destination_state_root)
        if destination_state_root.exists() and any(destination_state_root.iterdir()):
            raise OrganismContinuityError("destination state root is not empty")
        destination_state_root.mkdir(parents=True, exist_ok=True)

        inspected = cls.inspect_bundle(bundle, expected_sha256=expected_sha256)
        manifest = dict(inspected["manifest"])
        with tempfile.TemporaryDirectory(prefix="ferro-organism-restore-") as temp_dir:
            temp = Path(temp_dir)
            with zipfile.ZipFile(Path(bundle), "r") as archive:
                memory_bundle = temp / "memory-continuity.zip"
                memory_bundle.write_bytes(archive.read("memory/memory-continuity.zip"))
                continuity_key = archive.read("memory/continuity.key")
                if len(continuity_key) < 32:
                    raise OrganismContinuityError("memory continuity key is invalid")

                for name in sorted(manifest["files"]):
                    if not str(name).startswith("state/"):
                        continue
                    relative = cls._safe_name(str(name)[len("state/"):])
                    target = destination_state_root.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = archive.read(name)
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)

            restored_memory = MemoryContinuityService.restore_bundle(
                memory_bundle,
                destination_state_root,
                verification_key=continuity_key,
            )

        restored = cls(destination_state_root)
        organism_id = restored.organism_id()
        if organism_id != str(manifest.get("organism_id") or ""):
            raise OrganismContinuityError("restored organism_id mismatch")
        identity = restored.network_identity_summary()
        expected_identity = manifest.get("network_identity") or {}
        if identity != expected_identity:
            raise OrganismContinuityError("restored network identity mismatch")
        memory_digest = restored_memory.canonical_digest()
        if memory_digest["state_hash"] != str(manifest.get("memory_state_hash") or ""):
            raise OrganismContinuityError("restored canonical memory hash mismatch")
        return {
            "status": "RESTORED",
            "schema_version": SCHEMA_VERSION,
            "bundle_sha256": inspected["sha256"],
            "organism_id": organism_id,
            "network_identity": identity,
            "memory_state_hash": memory_digest["state_hash"],
            "member_count": len(manifest.get("files") or {}),
        }


__all__ = [
    "EXCLUDED_SECURITY_CLASSES",
    "MAX_BUNDLE_BYTES",
    "OrganismContinuityError",
    "OrganismContinuityService",
    "SCHEMA_VERSION",
    "STATE_ALLOWLIST",
]
