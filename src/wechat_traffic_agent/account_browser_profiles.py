"""Steel-backed persistent browser profiles for Ferro accounts.

Steel owns Chromium lifecycle. Ferro owns only identity-to-profile binding and
persists each account's user-data-dir under the existing state root.
Self-hosted Steel 0.5.x exposes one active session, so acquisition is explicit
and serialized rather than silently stealing another account's browser.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Iterator
import urllib.error
from urllib.parse import urlparse
import urllib.request

from .account_registry import AccountRegistry


class AccountBrowserProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountBrowserRuntime:
    account_id: str
    profile_id: str
    session_id: str
    cdp_ws: str
    viewer_url: str
    running: bool
    provider: str = "STEEL_SELF_HOSTED"


class AccountBrowserProfileManager:
    """Bind Ferro account identities to persistent Steel browser sessions."""

    def __init__(
        self,
        state_root: Path,
        *,
        api_base: str | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.registry = AccountRegistry(self.state_root)
        self.root = self.state_root / "accounts" / "browser-profiles"
        self.runtime_root = self.state_root / "browser"
        self.api_base = (
            str(api_base or os.environ.get("CONTENT_STEEL_BROWSER_URL") or "")
            .strip()
            .rstrip("/")
            or "http://127.0.0.1:3000/v1"
        )
        self.steel_home = Path(
            str(
                os.environ.get("CONTENT_STEEL_BROWSER_HOME")
                or "/home/ubuntu/src/steel-browser"
            ).strip()
        )
        self.steel_node = str(
            os.environ.get("CONTENT_STEEL_NODE") or "/usr/local/bin/node"
        ).strip()

    def _folder(self, account_id: str) -> Path:
        self.registry.get(account_id)
        return self.root / account_id

    def _runtime_path(self, account_id: str) -> Path:
        return self._folder(account_id) / "runtime.json"

    def _profile_dir(self, account_id: str) -> Path:
        # Keep the historical location so existing authenticated cookies migrate
        # into Steel without copying or exporting browser credentials.
        return self._folder(account_id) / "chrome-profile"

    def _active_path(self) -> Path:
        return self.runtime_root / "steel-active.json"

    @contextmanager
    def _runtime_lock(self) -> Iterator[None]:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.runtime_root, 0o700)
        path = self.runtime_root / "steel-runtime.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with os.fdopen(fd, "r+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            raise

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _write_private_json(path: Path, body: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        payload = None
        headers = {"Accept": "application/json"}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.api_base + "/" + path.lstrip("/"),
            data=payload,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            raise AccountBrowserProfileError(
                f"Steel HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AccountBrowserProfileError(
                f"Steel browser unavailable: {type(exc).__name__}"
            ) from exc
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            raise AccountBrowserProfileError("Steel returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise AccountBrowserProfileError("Steel returned an invalid response")
        return parsed

    def _service_log_path(self) -> Path:
        return self.runtime_root / "steel-service.log"

    def _service_state_path(self) -> Path:
        return self.runtime_root / "steel-service.json"

    def _health_ready(self) -> bool:
        try:
            body = self._request_json("GET", "health", timeout=1.0)
        except AccountBrowserProfileError:
            return False
        return str(body.get("status") or "").lower() == "ok"

    def _ensure_service(self) -> None:
        if self._health_ready():
            return

        entry = self.steel_home / "api" / "build" / "index.js"
        if not entry.is_file():
            raise AccountBrowserProfileError(
                f"Steel browser build is unavailable: {entry}"
            )
        if not Path(self.steel_node).is_file():
            raise AccountBrowserProfileError(
                f"Steel Node runtime is unavailable: {self.steel_node}"
            )

        parsed = urlparse(self.api_base)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        domain = f"{host}:{port}"
        chrome = str(
            os.environ.get("CONTENT_STEEL_CHROME_EXECUTABLE")
            or "/home/ubuntu/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
        ).strip()
        if not Path(chrome).is_file():
            raise AccountBrowserProfileError(
                f"Steel Chromium executable is unavailable: {chrome}"
            )

        self.runtime_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.runtime_root, 0o700)
        log_path = self._service_log_path()
        log_handle = log_path.open("ab", buffering=0)
        env = dict(os.environ)
        env.update({
            "HOST": host,
            "PORT": str(port),
            "DOMAIN": domain,
            "CDP_DOMAIN": domain,
            "CHROME_EXECUTABLE_PATH": chrome,
            "CHROME_HEADLESS": "true",
            "DISABLE_CHROME_SANDBOX": "true",
        })
        try:
            process = subprocess.Popen(
                [self.steel_node, str(entry)],
                cwd=str(self.steel_home / "api"),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_handle.close()

        self._write_private_json(
            self._service_state_path(),
            {
                "schema_version": 1,
                "provider": "STEEL_SELF_HOSTED",
                "pid": int(process.pid),
                "api_base": self.api_base,
            },
        )

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                raise AccountBrowserProfileError(
                    f"Steel browser exited during startup with code {code}"
                )
            if self._health_ready():
                return
            time.sleep(0.2)
        raise AccountBrowserProfileError(
            "Steel browser did not become healthy within 20 seconds"
        )

    def health(self) -> dict[str, Any]:
        self._ensure_service()
        body = self._request_json("GET", "health", timeout=2.0)
        if str(body.get("status") or "").lower() != "ok":
            raise AccountBrowserProfileError("Steel browser health check failed")
        return {
            "provider": "STEEL_SELF_HOSTED",
            "status": "READY",
            "api_base": self.api_base,
        }

    def _read_runtime(self, account_id: str) -> dict[str, Any]:
        return self._read_json_file(self._runtime_path(account_id))

    def _read_active(self) -> dict[str, Any]:
        return self._read_json_file(self._active_path())

    def _session_details(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            return {}
        return self._request_json("GET", f"sessions/{session_id}", timeout=3.0)

    @staticmethod
    def _is_live(body: dict[str, Any], session_id: str) -> bool:
        return (
            str(body.get("id") or "") == str(session_id)
            and str(body.get("status") or "").lower() == "live"
        )

    def _runtime_from(
        self,
        account_id: str,
        body: dict[str, Any],
        *,
        running: bool,
    ) -> AccountBrowserRuntime:
        record = self.registry.get(account_id)
        return AccountBrowserRuntime(
            account_id=record.account_id,
            profile_id=(
                str(body.get("profile_id") or "")
                or record.browser_profile_id
                or f"steel_{record.account_id}"
            ),
            session_id=str(body.get("session_id") or ""),
            cdp_ws=str(body.get("cdp_ws") or ""),
            viewer_url=str(body.get("viewer_url") or ""),
            running=bool(running),
        )

    def status(self, account_id: str) -> AccountBrowserRuntime:
        self.registry.get(account_id)
        runtime = self._read_runtime(account_id)
        session_id = str(runtime.get("session_id") or "")
        if not session_id:
            return self._runtime_from(account_id, runtime, running=False)
        details = self._session_details(session_id)
        live = self._is_live(details, session_id)
        if live:
            runtime = {
                **runtime,
                "cdp_ws": str(details.get("websocketUrl") or runtime.get("cdp_ws") or ""),
                "viewer_url": str(
                    details.get("debugUrl")
                    or details.get("sessionViewerUrl")
                    or runtime.get("viewer_url")
                    or ""
                ),
            }
        return self._runtime_from(account_id, runtime, running=live)

    def ensure(self, account_id: str) -> AccountBrowserRuntime:
        record = self.registry.get(account_id)
        self._ensure_service()
        with self._runtime_lock():
            current = self.status(account_id)
            if current.running:
                return current

            active = self._read_active()
            active_account = str(active.get("account_id") or "")
            active_session = str(active.get("session_id") or "")
            if active_account and active_session:
                details = self._session_details(active_session)
                if self._is_live(details, active_session):
                    if active_account == record.account_id:
                        recovered = {
                            "schema_version": 2,
                            "provider": "STEEL_SELF_HOSTED",
                            "account_id": record.account_id,
                            "profile_id": (
                                record.browser_profile_id
                                or f"steel_{record.account_id}"
                            ),
                            "session_id": active_session,
                            "cdp_ws": str(details.get("websocketUrl") or ""),
                            "viewer_url": str(
                                details.get("debugUrl")
                                or details.get("sessionViewerUrl")
                                or ""
                            ),
                        }
                        self._write_private_json(
                            self._runtime_path(record.account_id),
                            recovered,
                        )
                        return self._runtime_from(
                            record.account_id,
                            recovered,
                            running=True,
                        )
                    else:
                        raise AccountBrowserProfileError(
                            "Steel browser busy with account "
                            f"{active_account}; release it before acquiring "
                            f"{record.account_id}"
                        )
                else:
                    self._unlink(self._active_path())

            folder = self._folder(record.account_id)
            profile = self._profile_dir(record.account_id)
            folder.mkdir(parents=True, exist_ok=True)
            profile.mkdir(parents=True, exist_ok=True)
            os.chmod(folder, 0o700)
            os.chmod(profile, 0o700)

            result = self._request_json(
                "POST",
                "sessions",
                {
                    "userDataDir": str(profile),
                    "persist": True,
                    "headless": True,
                    "dimensions": {"width": 1120, "height": 780},
                },
                timeout=20.0,
            )
            session_id = str(result.get("id") or "")
            cdp_ws = str(result.get("websocketUrl") or "")
            if (
                not session_id
                or str(result.get("status") or "").lower() != "live"
                or not cdp_ws.startswith(("ws://", "wss://"))
            ):
                raise AccountBrowserProfileError(
                    "Steel did not return a live CDP websocket session"
                )

            profile_id = record.browser_profile_id or f"steel_{record.account_id}"
            runtime = {
                "schema_version": 2,
                "provider": "STEEL_SELF_HOSTED",
                "account_id": record.account_id,
                "profile_id": profile_id,
                "session_id": session_id,
                "cdp_ws": cdp_ws,
                "viewer_url": str(
                    result.get("debugUrl")
                    or result.get("sessionViewerUrl")
                    or ""
                ),
            }
            self._write_private_json(self._runtime_path(record.account_id), runtime)
            self._write_private_json(
                self._active_path(),
                {
                    "schema_version": 1,
                    "account_id": record.account_id,
                    "session_id": session_id,
                },
            )
            self.registry.set_browser_profile(record.account_id, profile_id)
            return self._runtime_from(record.account_id, runtime, running=True)

    def stop(self, account_id: str) -> AccountBrowserRuntime:
        record = self.registry.get(account_id)
        with self._runtime_lock():
            runtime = self._read_runtime(record.account_id)
            session_id = str(runtime.get("session_id") or "")
            active = self._read_active()
            owns_active = (
                str(active.get("account_id") or "") == record.account_id
                and str(active.get("session_id") or "") == session_id
                and bool(session_id)
            )
            if owns_active:
                details = self._session_details(session_id)
                if self._is_live(details, session_id):
                    released = self._request_json(
                        "POST",
                        f"sessions/{session_id}/release",
                        {},
                        timeout=12.0,
                    )
                    if not released.get("success"):
                        raise AccountBrowserProfileError(
                            "Steel did not confirm browser session release"
                        )
                self._unlink(self._active_path())
            self._unlink(self._runtime_path(record.account_id))
            return self._runtime_from(record.account_id, runtime, running=False)

    def delete_profile(self, account_id: str) -> None:
        self.registry.get(account_id)
        self.stop(account_id)
        folder = self._folder(account_id)
        if folder.exists():
            shutil.rmtree(folder)


__all__ = [
    "AccountBrowserProfileError",
    "AccountBrowserProfileManager",
    "AccountBrowserRuntime",
]
