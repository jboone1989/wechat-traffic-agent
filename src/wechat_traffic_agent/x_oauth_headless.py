"""Operator-assisted headless X OAuth2 binding for additional accounts.

The authorization URL and callback code are ephemeral. Tokens remain owned by
xurl's credential store and are never copied into Ferro state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import re
import secrets
import select
import subprocess
import time
from typing import Any

from .account_registry import AccountRegistry

_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_AUTH_URL = re.compile(r"https://x\.com/i/oauth2/authorize\?[^\s]+")
_MAX_CALLBACK = 4096


class XOAuthSessionError(RuntimeError):
    pass


@dataclass
class _Pending:
    process: subprocess.Popen
    username: str
    started_at: datetime
    auth_url: str
class XOAuthHeadlessManager:
    def __init__(
        self,
        state_root,
        *,
        app_name: str = "content-agent",
        ttl: timedelta = timedelta(minutes=10),
        popen_factory=None,
    ) -> None:
        self.registry = AccountRegistry(state_root)
        self.app_name = str(app_name).strip() or "content-agent"
        self.ttl = ttl
        self.popen_factory = popen_factory or subprocess.Popen
        self._pending: dict[str, _Pending] = {}

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=0.5)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - self.ttl
        for session_id, pending in list(self._pending.items()):
            if pending.started_at >= cutoff and pending.process.poll() is None:
                continue
            self._stop_process(pending.process)
            self._pending.pop(session_id, None)

    @staticmethod
    def _read_until(process: subprocess.Popen, marker: str, timeout: float) -> str:
        if process.stdout is None:
            raise XOAuthSessionError("xurl stdout unavailable")
        fd = process.stdout.fileno()
        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        needle = marker.encode()
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
            joined = b"".join(chunks)
            if needle in joined:
                return joined.decode(errors="replace")
        text = b"".join(chunks).decode(errors="replace")
        raise XOAuthSessionError(
            "xurl did not produce an authorization prompt"
            + (f": {text[-120:]}" if text else "")
        )

    def start(self, username: str) -> dict[str, Any]:
        self._prune()
        handle = str(username).strip().lstrip("@")
        if not _USERNAME.fullmatch(handle):
            raise ValueError("X username must be 1-15 letters, digits, or underscores")
        command = [
            "xurl", "auth", "oauth2", handle,
            "--app", self.app_name, "--headless",
        ]
        process = self.popen_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output = self._read_until(process, "Paste the full redirected URL", 6.0)
        except Exception:
            self._stop_process(process)
            raise
        match = _AUTH_URL.search(output)
        if not match:
            self._stop_process(process)
            raise XOAuthSessionError("xurl authorization URL was not found")
        session_id = secrets.token_urlsafe(24)
        self._pending[session_id] = _Pending(
            process=process,
            username=handle,
            started_at=datetime.now(timezone.utc),
            auth_url=match.group(0),
        )
        return {
            "session_id": session_id,
            "username": handle,
            "authorization_url": match.group(0),
            "expires_in_seconds": int(self.ttl.total_seconds()),
            "callback_hint": "授权后复制地址栏里的 localhost callback URL，再粘贴回这里。",
        }

    def complete(self, session_id: str, callback: str) -> dict[str, Any]:
        self._prune()
        pending = self._pending.pop(str(session_id), None)
        if pending is None:
            raise XOAuthSessionError("OAuth session is missing or expired")
        value = str(callback).strip()
        if not value or len(value) > _MAX_CALLBACK or "\n" in value or "\r" in value:
            raise ValueError("invalid OAuth callback")
        process = pending.process
        if process.stdin is None:
            raise XOAuthSessionError("xurl stdin unavailable")
        try:
            process.stdin.write((value + "\n").encode())
            process.stdin.flush()
            stdout, _ = process.communicate(timeout=20)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise XOAuthSessionError("xurl OAuth completion timed out") from exc
        if process.returncode != 0:
            raise XOAuthSessionError("X authorization was not accepted")

        check = subprocess.run(
            [
                "xurl", "--app", self.app_name,
                "-u", pending.username, "whoami",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if check.returncode != 0:
            raise XOAuthSessionError("X account was authorized but verification failed")
        try:
            body = json.loads(check.stdout)
        except json.JSONDecodeError as exc:
            raise XOAuthSessionError("X whoami returned invalid JSON") from exc
        data = body.get("data") if isinstance(body, dict) else None
        actual = str((data or {}).get("username") or "").strip()
        if actual.lower() != pending.username.lower():
            raise XOAuthSessionError("authorized X identity did not match requested username")
        record = self.registry.ensure(
            provider_id="x",
            identity=actual,
            oauth_app=self.app_name,
            oauth_user=actual,
            oauth_status="CONNECTED",
        )
        return {
            "account_id": record.account_id,
            "provider_id": record.provider_id,
            "identity": record.identity,
            "status": record.oauth_status,
        }

    def cancel(self, session_id: str) -> None:
        pending = self._pending.pop(str(session_id), None)
        if pending is not None:
            self._stop_process(pending.process)


__all__ = [
    "XOAuthHeadlessManager",
    "XOAuthSessionError",
]
