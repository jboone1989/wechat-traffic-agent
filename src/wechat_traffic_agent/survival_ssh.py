"""One-time SSH bootstrap for operator-supplied survival hosts.

Passwords exist only in request memory. A successful bootstrap installs a dedicated
per-host key, verifies key-only login, and persists only non-secret audit evidence
plus the local 0600 private key.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
from typing import Any


_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class BootstrapResult:
    host: str
    port: int
    username: str
    host_key_sha256: str
    host_instance_id: str
    audit: dict[str, Any]
    key_path: str
    key_verified: bool

    def public_payload(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "host_key_sha256": self.host_key_sha256,
            "host_instance_id": self.host_instance_id,
            "audit": dict(self.audit),
            "key_verified": self.key_verified,
        }


class SSHHostBootstrapper:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.keys_root = self.state_root / "secrets" / "survival-hosts"

    def bootstrap(
        self, *, host: str, port: int, username: str, password: str,
        label: str = "",
    ) -> BootstrapResult:
        host = self._validate_host(host)
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        username = str(username or "root").strip()
        if not _USER_RE.fullmatch(username):
            raise ValueError("invalid SSH username")
        secret = str(password or "")
        if not secret or "\n" in secret or "\r" in secret:
            raise ValueError("SSH password is required")

        paramiko = self._paramiko()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host, port=port, username=username, password=secret,
                look_for_keys=False, allow_agent=False, timeout=10,
                banner_timeout=10, auth_timeout=10,
            )
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RuntimeError("SSH transport did not become active")
            host_key = transport.get_remote_server_key()
            host_key_sha256 = self._host_key_fingerprint(host_key.asbytes())
            audit = self._audit(client)
            host_instance_id = str(audit.get("host_instance_id") or "").strip()
            if not host_instance_id:
                raise RuntimeError("remote host audit did not produce host identity")
            if host_instance_id == self._local_host_instance_id():
                raise RuntimeError("configured SSH target is this same physical host")

            key_path = self._key_path(host, port, username, host_instance_id)
            public_line = self._ensure_management_key(key_path, label=label or host)
            self._install_public_key(client, public_line)
        finally:
            try:
                client.close()
            except Exception:
                pass

        self._verify_key_login(
            host=host, port=port, username=username, key_path=key_path,
            expected_host_key_sha256=host_key_sha256,
        )
        return BootstrapResult(
            host=host, port=port, username=username,
            host_key_sha256=host_key_sha256,
            host_instance_id=host_instance_id,
            audit=audit,
            key_path=str(key_path),
            key_verified=True,
        )

    @staticmethod
    def _paramiko():
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise RuntimeError("paramiko is required for SSH host bootstrap") from exc
        return paramiko

    @staticmethod
    def _validate_host(raw: str) -> str:
        host = str(raw or "").strip().strip("[]")
        if not host or "://" in host or "/" in host or not _HOST_RE.fullmatch(host):
            try:
                ipaddress.ip_address(host)
            except ValueError as exc:
                raise ValueError("host must be an IP address or DNS hostname") from exc
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host.lower() in {"localhost", "localhost.localdomain"}:
                raise ValueError("local SSH targets are not remote survival hosts")
            return host.lower()
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("local/link-local/multicast SSH targets are not allowed")
        return str(address)

    @staticmethod
    def _host_key_fingerprint(raw: bytes) -> str:
        digest = base64.b64encode(sha256(raw).digest()).decode("ascii").rstrip("=")
        return "SHA256:" + digest

    def _audit(self, client: Any) -> dict[str, Any]:
        script = r'''import hashlib,json,os,pathlib,socket,shutil
mid=""
try: mid=pathlib.Path("/etc/machine-id").read_text().strip()
except OSError: mid=socket.gethostname()
mem={}
try:
  for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
    if ":" in line:
      k,v=line.split(":",1); token=v.strip().split()[0]
      if token.isdigit(): mem[k]=int(token)*1024
except OSError: pass
d=shutil.disk_usage("/")
print(json.dumps({
 "hostname":socket.gethostname(),
 "host_instance_id":"host-instance:"+hashlib.sha256(mid.encode()).hexdigest()[:24],
 "cpu_count":os.cpu_count() or 1,
 "memory_total_gib":round(mem.get("MemTotal",0)/1024**3,3),
 "memory_available_gib":round(mem.get("MemAvailable",0)/1024**3,3),
 "disk_total_gib":round(d.total/1024**3,3),
 "disk_free_gib":round(d.free/1024**3,3),
 "platform":os.uname().sysname+" "+os.uname().release,
 "machine":os.uname().machine,
},sort_keys=True))'''
        command = "python3 -c " + shlex.quote(script)
        _, stdout, stderr = client.exec_command(command, timeout=15)
        raw = stdout.read().decode("utf-8", "replace").strip()
        error = stderr.read().decode("utf-8", "replace").strip()
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError("remote Python audit failed" + (": " + error[:120] if error else ""))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("remote host audit returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("remote host audit returned invalid payload")
        return payload

    def _key_path(self, host: str, port: int, username: str, host_instance_id: str) -> Path:
        key_id = sha256(f"{host}|{port}|{username}|{host_instance_id}".encode()).hexdigest()[:24]
        root = self.keys_root / key_id
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        return root / "id_ed25519"

    def _ensure_management_key(self, path: Path, *, label: str) -> str:
        paramiko = self._paramiko()
        if not path.exists():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            key = Ed25519PrivateKey.generate()
            private = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            )
            public = key.public_key().public_bytes(
                serialization.Encoding.OpenSSH,
                serialization.PublicFormat.OpenSSH,
            ).decode("ascii")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(private)
            path.with_suffix(".pub").write_text(
                public + " ferro-survival-" + re.sub(r"[^A-Za-z0-9_.-]", "-", label)[:48] + "\n",
                encoding="utf-8",
            )
            os.chmod(path.with_suffix(".pub"), 0o600)
        # Loading here proves the private key is parseable before it is installed remotely.
        paramiko.Ed25519Key.from_private_key_file(str(path))
        return path.with_suffix(".pub").read_text(encoding="utf-8").strip()

    @staticmethod
    def _install_public_key(client: Any, public_line: str) -> None:
        quoted = shlex.quote(public_line)
        command = (
            "umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
            f"grep -qxF {quoted} ~/.ssh/authorized_keys || printf '%s\\n' {quoted} >> ~/.ssh/authorized_keys"
        )
        _, stdout, stderr = client.exec_command(command, timeout=15)
        error = stderr.read().decode("utf-8", "replace").strip()
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError("failed to install Ferro management key" + (": " + error[:120] if error else ""))

    def _verify_key_login(
        self, *, host: str, port: int, username: str, key_path: Path,
        expected_host_key_sha256: str,
    ) -> None:
        paramiko = self._paramiko()
        key = paramiko.Ed25519Key.from_private_key_file(str(key_path))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host, port=port, username=username, pkey=key,
                look_for_keys=False, allow_agent=False, timeout=10,
                banner_timeout=10, auth_timeout=10,
            )
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RuntimeError("key-only SSH transport did not become active")
            actual = self._host_key_fingerprint(transport.get_remote_server_key().asbytes())
            if actual != expected_host_key_sha256:
                raise RuntimeError("SSH host key changed during bootstrap")
            _, stdout, _ = client.exec_command("printf FERRO_SSH_KEY_OK", timeout=8)
            if stdout.read().decode("utf-8", "replace") != "FERRO_SSH_KEY_OK":
                raise RuntimeError("key-only SSH verification command failed")
        finally:
            client.close()

    @staticmethod
    def _local_host_instance_id() -> str:
        try:
            raw = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
        except OSError:
            raw = socket.gethostname()
        return "host-instance:" + sha256(raw.encode()).hexdigest()[:24]


__all__ = ["BootstrapResult", "SSHHostBootstrapper"]
