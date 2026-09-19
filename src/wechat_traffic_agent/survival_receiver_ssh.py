"""Provision and use the bounded Agent Network survival receiver over managed SSH.

The remote host never receives GitHub credentials. Ferro creates a bundle from a
locally trusted Agent Network mirror at a pinned commit, uploads it through the
already-established per-host SSH key, and invokes the same signed workload
receiver used by the HTTPS path.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Any

from .survival_ssh import SSHHostBootstrapper


DEFAULT_RECEIVER_SHA = "a56d2c9bbe0569d1c83695c71833e7a7193f6f17"
DEFAULT_AGENT_NETWORK_MIRROR = Path("/home/ubuntu/deploy/agent-network-bare.git")


def desired_receiver_sha() -> str:
    return str(
        os.environ.get("CONTENT_SURVIVAL_AGENT_NETWORK_RECEIVER_SHA")
        or DEFAULT_RECEIVER_SHA
    ).strip().lower()


@dataclass(frozen=True)
class ReceiverProvisionResult:
    release_sha: str
    receiver_command: str
    receiver_root: str
    agent_id: str
    key_id: str
    public_key: str
    host_instance_id: str
    deployment_profiles: tuple[str, ...]
    capacity: dict[str, Any]
    capabilities: tuple[str, ...] = ()


class SSHWorkloadTransport:
    def __init__(
        self, *, host: str, port: int, username: str, key_path: Path,
        expected_host_key_sha256: str, receiver_command: str,
    ) -> None:
        self.host = SSHHostBootstrapper._validate_host(host)
        self.port = int(port)
        self.username = str(username)
        self.key_path = Path(key_path)
        self.expected_host_key_sha256 = str(expected_host_key_sha256)
        self.receiver_command = str(receiver_command)

    def health(self) -> dict[str, Any]:
        return self._json_operation("health")

    def deploy(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        return self._json_operation("deploy", stdin=body, timeout=90)

    def task(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        return self._json_operation("task", stdin=body, timeout=45)

    def static_index_sha256(self, deployment_path: str) -> str:
        path = str(deployment_path or "").strip()
        if not path.startswith("/") or "\n" in path or "\r" in path:
            raise ValueError("remote deployment path is invalid")
        script = (
            "import hashlib,pathlib,sys;"
            "p=(pathlib.Path(sys.argv[1]).resolve(strict=True)/'index.html');"
            "print('sha256:'+hashlib.sha256(p.read_bytes()).hexdigest())"
        )
        output = self._command(
            "python3 -c " + shlex.quote(script) + " " + shlex.quote(path),
            timeout=20,
        ).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", output):
            raise RuntimeError("remote static index hash response is invalid")
        return output

    def _json_operation(
        self, operation: str, *, stdin: str = "", timeout: int = 25,
    ) -> dict[str, Any]:
        if operation not in {"health", "deploy", "task"}:
            raise ValueError("unsupported SSH workload operation")
        raw = self._command(
            self.receiver_command + " " + operation,
            stdin=stdin,
            timeout=timeout,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SSH workload receiver returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("SSH workload receiver returned invalid payload")
        if payload.get("error"):
            raise RuntimeError("SSH workload receiver rejected the request")
        return payload

    def _command(
        self, command: str, *, stdin: str = "", timeout: int = 25,
    ) -> str:
        client = self._connect()
        try:
            input_stream, stdout, stderr = client.exec_command(command, timeout=timeout)
            if stdin:
                input_stream.write(stdin)
                input_stream.flush()
            try:
                input_stream.channel.shutdown_write()
            except Exception:
                pass
            raw = stdout.read().decode("utf-8", "replace").strip()
            error = stderr.read().decode("utf-8", "replace").strip()
            code = stdout.channel.recv_exit_status()
            if code != 0:
                raise RuntimeError(
                    "SSH workload receiver command failed"
                    + (": " + error[:160] if error else "")
                )
            return raw
        finally:
            client.close()

    def _connect(self):
        paramiko = SSHHostBootstrapper._paramiko()
        key = paramiko.Ed25519Key.from_private_key_file(str(self.key_path))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            pkey=key,
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            client.close()
            raise RuntimeError("SSH workload transport did not become active")
        actual = SSHHostBootstrapper._host_key_fingerprint(
            transport.get_remote_server_key().asbytes()
        )
        if actual != self.expected_host_key_sha256:
            client.close()
            raise RuntimeError("SSH host key no longer matches managed host")
        return client


class SSHReceiverProvisioner:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)

    def provision(
        self, *, host: str, port: int, username: str, key_path: Path,
        expected_host_key_sha256: str, expected_host_instance_id: str,
        controller: dict[str, str],
    ) -> ReceiverProvisionResult:
        release_sha = desired_receiver_sha()
        if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
            raise ValueError("receiver release SHA must be a full git SHA")
        mirror = Path(
            os.environ.get("CONTENT_SURVIVAL_AGENT_NETWORK_REPO")
            or DEFAULT_AGENT_NETWORK_MIRROR
        )
        self._verify_local_commit(mirror, release_sha)

        with tempfile.TemporaryDirectory(prefix="ferro-receiver-") as temp_dir:
            bundle = Path(temp_dir) / ("agent-network-" + release_sha[:12] + ".bundle")
            self._create_bundle(mirror, release_sha, bundle)
            client = self._connect(
                host=host,
                port=port,
                username=username,
                key_path=key_path,
                expected_host_key_sha256=expected_host_key_sha256,
            )
            try:
                home = self._remote_home(client)
                base = Path(home) / ".ferro-survival-receiver"
                release = base / "releases" / release_sha[:12]
                venv = base / "venvs" / release_sha[:12]
                state = base / "state"
                remote_bundle = base / "bundles" / bundle.name
                trusted = state / "trusted-controller.json"
                env_file = state / "receiver.env"
                self._prepare_directories(client, base)
                base_python = self._select_python(client)
                self._upload_file(client, bundle, remote_bundle, 0o600)
                self._upload_json(
                    client,
                    trusted,
                    {
                        str(controller["agent_id"]): {
                            "key_id": str(controller["key_id"]),
                            "public_key": str(controller["public_key"]),
                        }
                    },
                )
                agent_id = (
                    "survival."
                    + str(expected_host_instance_id).split(":", 1)[-1][:16]
                    + ".network.cai"
                )
                env_text = "\n".join(
                    [
                        "NETWORK_SURVIVAL_WORKLOAD_IDENTITY=" + str(state / "identity.json"),
                        "NETWORK_SURVIVAL_WORKLOAD_ROOT=" + str(state / "workloads"),
                        "NETWORK_SURVIVAL_WORKLOAD_TRUSTED_KEYS_FILE=" + str(trusted),
                        "NETWORK_SURVIVAL_WORKLOAD_AGENT_ID=" + agent_id,
                        "NETWORK_SURVIVAL_WORKLOAD_PROFILES=content.website-static.v1,content.resource-research.v1",
                        "",
                    ]
                )
                self._upload_bytes(client, env_text.encode("utf-8"), env_file, 0o600)
                self._install_release(
                    client=client,
                    remote_bundle=remote_bundle,
                    release=release,
                    venv=venv,
                    release_sha=release_sha,
                    base_python=base_python,
                )
                receiver_command = (
                    "set -a; . "
                    + shlex.quote(str(env_file))
                    + "; set +a; "
                    + shlex.quote(str(venv / "bin" / "network-survival-workload"))
                )
            finally:
                client.close()

        transport = SSHWorkloadTransport(
            host=host,
            port=port,
            username=username,
            key_path=Path(key_path),
            expected_host_key_sha256=expected_host_key_sha256,
            receiver_command=receiver_command,
        )
        health = transport.health()
        if str(health.get("status") or "").upper() != "HEALTHY":
            raise RuntimeError("provisioned SSH receiver did not report healthy")
        if str(health.get("host_instance_id") or "") != expected_host_instance_id:
            raise RuntimeError("provisioned SSH receiver host identity mismatch")
        capabilities = tuple(
            str(item)
            for item in health.get("capabilities") or ()
            if str(item).strip()
        )
        profiles = tuple(
            str(item)
            for item in health.get("deployment_profiles") or ()
            if str(item).strip()
        )
        required_profiles = {
            "content.website-static.v1",
            "content.resource-research.v1",
        }
        if not required_profiles.issubset(set(profiles)):
            raise RuntimeError("provisioned SSH receiver omitted required profiles")
        return ReceiverProvisionResult(
            release_sha=release_sha,
            receiver_command=receiver_command,
            receiver_root=str(state / "workloads"),
            agent_id=str(health.get("agent_id") or ""),
            key_id=str(health.get("key_id") or ""),
            public_key=str(health.get("public_key") or ""),
            host_instance_id=str(health.get("host_instance_id") or ""),
            capabilities=capabilities,
            deployment_profiles=profiles,
            capacity=dict(health.get("capacity") or {}),
        )

    @staticmethod
    def _verify_local_commit(mirror: Path, release_sha: str) -> None:
        if not mirror.exists():
            raise RuntimeError("local Agent Network mirror is unavailable")
        check = ["git", "--git-dir=" + str(mirror), "cat-file", "-e", release_sha + "^{commit}"]
        result = subprocess.run(
            check, capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "--git-dir=" + str(mirror), "fetch", "--quiet", "origin", "master"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            result = subprocess.run(
                check, capture_output=True, text=True, timeout=20,
            )
        if result.returncode != 0:
            raise RuntimeError("pinned Agent Network receiver commit is unavailable")

    @staticmethod
    def _create_bundle(mirror: Path, release_sha: str, output: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="ferro-agent-network-") as temp_dir:
            clone = Path(temp_dir) / "repo"
            subprocess.run(
                ["git", "clone", "--quiet", "--no-checkout", "--local", str(mirror), str(clone)],
                check=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "-C", str(clone), "update-ref", "refs/heads/ferro-receiver", release_sha],
                check=True,
                timeout=20,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(clone),
                    "bundle",
                    "create",
                    str(output),
                    "refs/heads/ferro-receiver",
                ],
                check=True,
                timeout=60,
            )
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("failed to create Agent Network receiver bundle")

    @staticmethod
    def _select_python(client: Any) -> str:
        candidates = [
            "python3.13",
            "python3.12",
            "python3.11",
            "/opt/agent-network-venv-current/bin/python",
            "/opt/agent-network-venv-416aa3ed/bin/python",
            "/opt/agent-network-venv-29cc3d20/bin/python",
        ]
        script = (
            "import os,shutil,subprocess,sys;"
            "candidates=sys.argv[1:];"
            "found='';"
            "\nfor item in candidates:"
            "\n p=shutil.which(item) if not item.startswith('/') else item;"
            "\n if p and os.path.exists(p):"
            "\n  r=subprocess.run([p,'-c','import sys;raise SystemExit(0 if sys.version_info >= (3,11) else 1)']);"
            "\n  if r.returncode==0: found=p; break;"
            "\nprint(found);raise SystemExit(0 if found else 2)"
        )
        command = "python3 -c " + shlex.quote(script) + " " + " ".join(
            shlex.quote(item) for item in candidates
        )
        return SSHReceiverProvisioner._run(client, command, timeout=20).strip()

    @staticmethod
    def _remote_home(client: Any) -> str:
        script = "import pathlib; print(pathlib.Path.home())"
        _, stdout, stderr = client.exec_command(
            "python3 -c " + shlex.quote(script), timeout=10
        )
        raw = stdout.read().decode("utf-8", "replace").strip()
        error = stderr.read().decode("utf-8", "replace").strip()
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(
                "remote Python is required for receiver provisioning"
                + (": " + error[:120] if error else "")
            )
        if not raw.startswith("/") or "\n" in raw or "\r" in raw:
            raise RuntimeError("remote home path is invalid")
        return raw

    @staticmethod
    def _prepare_directories(client: Any, base: Path) -> None:
        paths = (
            base,
            base / "bundles",
            base / "releases",
            base / "venvs",
            base / "state",
        )
        command = "umask 077; mkdir -p " + " ".join(
            shlex.quote(str(path)) for path in paths
        )
        SSHReceiverProvisioner._run(client, command, timeout=15)

    @staticmethod
    def _upload_file(client: Any, local: Path, remote: Path, mode: int) -> None:
        sftp = client.open_sftp()
        try:
            sftp.put(str(local), str(remote))
            sftp.chmod(str(remote), mode)
        finally:
            sftp.close()

    @staticmethod
    def _upload_bytes(client: Any, data: bytes, remote: Path, mode: int) -> None:
        sftp = client.open_sftp()
        try:
            with sftp.file(str(remote), "wb") as handle:
                handle.write(data)
            sftp.chmod(str(remote), mode)
        finally:
            sftp.close()

    def _upload_json(self, client: Any, remote: Path, payload: Any) -> None:
        self._upload_bytes(
            client,
            (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
            remote,
            0o600,
        )

    @staticmethod
    def _install_release(
        *, client: Any, remote_bundle: Path, release: Path, venv: Path,
        release_sha: str, base_python: str,
    ) -> None:
        python = venv / "bin" / "python"
        pip = venv / "bin" / "pip"
        commands = [
            "command -v git >/dev/null && command -v python3 >/dev/null",
            (
                "test -d "
                + shlex.quote(str(release))
                + " || git clone --quiet --branch ferro-receiver "
                + shlex.quote(str(remote_bundle))
                + " "
                + shlex.quote(str(release))
            ),
            (
                "if [ -x "
                + shlex.quote(str(python))
                + " ] && "
                + shlex.quote(str(python))
                + " -c "
                + shlex.quote("import sys;raise SystemExit(0 if sys.version_info >= (3,11) else 1)")
                + "; then :; else rm -rf "
                + shlex.quote(str(venv))
                + " && "
                + shlex.quote(base_python)
                + " -m venv --system-site-packages "
                + shlex.quote(str(venv))
                + "; fi"
            ),
            (
                shlex.quote(str(python))
                + " -c "
                + shlex.quote("import setuptools")
                + " || "
                + shlex.quote(str(pip))
                + " install --disable-pip-version-check --quiet 'setuptools>=68'"
            ),
            (
                shlex.quote(str(pip))
                + " install --disable-pip-version-check --quiet "
                + shlex.quote("cryptography>=42")
                + " "
                + shlex.quote("httpx>=0.27")
                + " "
                + shlex.quote("starlette>=0.37")
                + " "
                + shlex.quote("uvicorn>=0.27")
                + " "
                + shlex.quote("a2a-sdk[http-server]>=1.0.1")
            ),
            (
                shlex.quote(str(pip))
                + " install --disable-pip-version-check --quiet --no-deps --no-build-isolation "
                + shlex.quote(str(release / "agent-network-sdk-python"))
                + " "
                + shlex.quote(str(release / "network-agent"))
            ),
            (
                "test $("
                + "git -C "
                + shlex.quote(str(release))
                + " rev-parse HEAD"
                + ") = "
                + shlex.quote(release_sha)
            ),
            "rm -f " + shlex.quote(str(remote_bundle)),
        ]
        for command in commands:
            SSHReceiverProvisioner._run(client, command, timeout=150)

    @staticmethod
    def _run(client: Any, command: str, *, timeout: int) -> str:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        raw = stdout.read().decode("utf-8", "replace").strip()
        error = stderr.read().decode("utf-8", "replace").strip()
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(
                "remote receiver install failed"
                + (": " + error[:180] if error else "")
            )
        return raw

    @staticmethod
    def _connect(
        *, host: str, port: int, username: str, key_path: Path,
        expected_host_key_sha256: str,
    ):
        return SSHWorkloadTransport(
            host=host,
            port=port,
            username=username,
            key_path=key_path,
            expected_host_key_sha256=expected_host_key_sha256,
            receiver_command=":",
        )._connect()


__all__ = [
    "DEFAULT_RECEIVER_SHA",
    "desired_receiver_sha",
    "ReceiverProvisionResult",
    "SSHReceiverProvisioner",
    "SSHWorkloadTransport",
]
