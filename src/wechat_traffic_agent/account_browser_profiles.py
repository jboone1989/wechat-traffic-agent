"""Account-scoped persistent Chromium profiles.

Each account owns a separate user-data-dir. Chrome processes are disposable;
cookies/session state persist in the profile directory across process restarts.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import glob
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from typing import Any
import urllib.request

from .account_registry import AccountRegistry


class AccountBrowserProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountBrowserRuntime:
    account_id: str
    profile_id: str
    port: int
    pid: int
    cdp_url: str
    running: bool
class AccountBrowserProfileManager:
    PORT_START = 9400
    PORT_END = 9499

    def __init__(self, state_root: Path, *, popen_factory=None) -> None:
        self.state_root = Path(state_root)
        self.registry = AccountRegistry(self.state_root)
        self.root = self.state_root / "accounts" / "browser-profiles"
        self.popen_factory = popen_factory or subprocess.Popen

    def _folder(self, account_id: str) -> Path:
        self.registry.get(account_id)
        return self.root / account_id

    def _runtime_path(self, account_id: str) -> Path:
        return self._folder(account_id) / "runtime.json"

    def _profile_dir(self, account_id: str) -> Path:
        return self._folder(account_id) / "chrome-profile"

    def _read_runtime(self, account_id: str) -> dict[str, Any]:
        path = self._runtime_path(account_id)
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

    def _write_runtime(self, account_id: str, body: dict[str, Any]) -> None:
        folder = self._folder(account_id)
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        path = self._runtime_path(account_id)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.2):
                return True
        except OSError:
            return False

    @staticmethod
    def _cdp_ready(port: int) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{int(port)}/json/version", timeout=0.5
            ) as response:
                body = json.load(response)
            return isinstance(body, dict) and bool(body.get("webSocketDebuggerUrl"))
        except Exception:
            return False
    def _allocate_port(self, account_id: str) -> int:
        existing = self._read_runtime(account_id)
        saved = existing.get("port")
        if isinstance(saved, int) and self.PORT_START <= saved <= self.PORT_END:
            if not self._port_open(saved) or self._cdp_ready(saved):
                return saved
        for port in range(self.PORT_START, self.PORT_END + 1):
            if not self._port_open(port):
                return port
        raise AccountBrowserProfileError("no free account browser debug port")

    @staticmethod
    def _chrome_binary() -> str:
        candidates = sorted(
            glob.glob(
                "/home/ubuntu/.cache/ms-playwright/**/chrome-linux64/chrome",
                recursive=True,
            )
        )
        executable = [path for path in candidates if os.access(path, os.X_OK)]
        if not executable:
            raise AccountBrowserProfileError("Chromium binary is unavailable")
        return executable[-1]

    def status(self, account_id: str) -> AccountBrowserRuntime:
        record = self.registry.get(account_id)
        body = self._read_runtime(account_id)
        port = int(body.get("port") or 0)
        pid = int(body.get("pid") or 0)
        running = bool(port and self._cdp_ready(port))
        profile_id = record.browser_profile_id or f"browser_{record.account_id}"
        return AccountBrowserRuntime(
            account_id=record.account_id,
            profile_id=profile_id,
            port=port,
            pid=pid,
            cdp_url=f"http://127.0.0.1:{port}" if port else "",
            running=running,
        )

    def ensure(self, account_id: str) -> AccountBrowserRuntime:
        record = self.registry.get(account_id)
        current = self.status(account_id)
        if current.running:
            return current
        profile_id = record.browser_profile_id or f"browser_{record.account_id}"
        folder = self._folder(account_id)
        profile = self._profile_dir(account_id)
        folder.mkdir(parents=True, exist_ok=True)
        profile.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        os.chmod(profile, 0o700)
        port = self._allocate_port(account_id)
        command = [
            self._chrome_binary(),
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ]
        process = self.popen_factory(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AccountBrowserProfileError("account Chromium exited during startup")
            if self._cdp_ready(port):
                self._write_runtime(
                    account_id,
                    {
                        "schema_version": 1,
                        "account_id": account_id,
                        "profile_id": profile_id,
                        "port": port,
                        "pid": int(process.pid),
                    },
                )
                self.registry.set_browser_profile(account_id, profile_id)
                return self.status(account_id)
            time.sleep(0.1)
        try:
            process.terminate()
        except Exception:
            pass
        raise AccountBrowserProfileError("account Chromium did not become ready")

    def stop(self, account_id: str) -> AccountBrowserRuntime:
        current = self.status(account_id)
        if current.running and current.pid > 1:
            try:
                os.kill(current.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and self._cdp_ready(current.port):
                time.sleep(0.1)
            if self._cdp_ready(current.port):
                try:
                    os.kill(current.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        path = self._runtime_path(account_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        record = self.registry.get(account_id)
        return AccountBrowserRuntime(
            account_id=record.account_id,
            profile_id=record.browser_profile_id or f"browser_{record.account_id}",
            port=0,
            pid=0,
            cdp_url="",
            running=False,
        )


__all__ = [
    "AccountBrowserProfileError",
    "AccountBrowserProfileManager",
    "AccountBrowserRuntime",
]
