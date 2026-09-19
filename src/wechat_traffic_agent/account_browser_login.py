"""Operator login control for one account-scoped persistent Chromium profile."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import quote, urlparse
import urllib.request

from websockets.sync.client import connect as ws_connect

from .account_browser_profiles import AccountBrowserProfileManager


DEFAULT_LOGIN_URL = "https://x.com/i/flow/login"
_ALLOWED_HOSTS = {"x.com", "www.x.com"}


class AccountBrowserLoginUnavailable(RuntimeError):
    pass


class AccountBrowserLoginControl:
    """Short-lived operator tab inside one account's persistent Chrome body."""

    def __init__(
        self,
        *,
        state_root: Path,
        account_id: str,
        profile_manager=None,
    ) -> None:
        self.state_root = Path(state_root)
        self.account_id = str(account_id)
        self.profile_manager = (
            profile_manager
            if profile_manager is not None
            else AccountBrowserProfileManager(self.state_root)
        )
        self.folder = (
            self.state_root
            / "accounts"
            / "browser-profiles"
            / self.account_id
        )
        self.state_path = self.folder / "login-target.json"
        self._lock = threading.RLock()

    @staticmethod
    def _validate_url(url: str) -> str:
        raw = str(url or DEFAULT_LOGIN_URL).strip()
        parsed = urlparse(raw)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS
        ):
            raise ValueError("account login browser is restricted to x.com")
        return raw

    def _read_target(self) -> str:
        try:
            body = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ""
        return str(body.get("target_id") or "") if isinstance(body, dict) else ""

    def _write_target(self, target_id: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"schema_version": 1, "target_id": str(target_id)},
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)
        os.chmod(self.state_path, 0o600)

    def _clear_target(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    def cdp_url(self) -> str:
        runtime = self.profile_manager.status(self.account_id)
        if not runtime.running:
            runtime = self.profile_manager.ensure(self.account_id)
        if not runtime.cdp_url:
            raise AccountBrowserLoginUnavailable(
                "account browser runtime unavailable"
            )
        return runtime.cdp_url

    def _pages(self) -> list[dict[str, Any]]:
        try:
            with urllib.request.urlopen(
                self.cdp_url() + "/json/list", timeout=2
            ) as response:
                body = json.load(response)
        except Exception as exc:
            raise AccountBrowserLoginUnavailable(
                "account CDP endpoint unavailable"
            ) from exc
        return body if isinstance(body, list) else []

    def _target_info(self, target_id: str) -> dict[str, Any]:
        for page in self._pages():
            if str(page.get("id") or "") == str(target_id):
                return page
        raise AccountBrowserLoginUnavailable(
            "account login target no longer exists"
        )

    def _safe_ws_url(self, page: dict[str, Any]) -> str:
        value = str(page.get("webSocketDebuggerUrl") or "")
        parsed = urlparse(value)
        base = urlparse(self.cdp_url())
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port != base.port
        ):
            raise AccountBrowserLoginUnavailable(
                "unsafe account CDP websocket"
            )
        return value

    def _evaluate(self, page: dict[str, Any], expression: str) -> Any:
        ws_url = self._safe_ws_url(page)
        try:
            with ws_connect(
                ws_url,
                origin=None,
                open_timeout=2,
                close_timeout=1,
                max_size=2 * 1024 * 1024,
            ) as cdp:
                cdp.send(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                    },
                }))
                for _ in range(40):
                    message = json.loads(cdp.recv(timeout=2))
                    if message.get("id") != 1:
                        continue
                    if message.get("error"):
                        raise AccountBrowserLoginUnavailable(
                            "CDP evaluation failed"
                        )
                    result = (
                        (message.get("result") or {}).get("result") or {}
                    )
                    return result.get("value")
        except AccountBrowserLoginUnavailable:
            raise
        except Exception as exc:
            raise AccountBrowserLoginUnavailable(
                "account browser evaluation unavailable"
            ) from exc
        raise AccountBrowserLoginUnavailable(
            "CDP evaluation response missing"
        )

    def start(self, url: str = DEFAULT_LOGIN_URL) -> dict[str, Any]:
        target = self._validate_url(url)
        with self._lock:
            self._close_target()
            base = self.cdp_url()
            request = urllib.request.Request(
                base + "/json/new?" + quote(target, safe=""),
                method="PUT",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=3
                ) as response:
                    page = json.load(response)
            except Exception as exc:
                raise AccountBrowserLoginUnavailable(
                    "could not create account login target"
                ) from exc
            target_id = str(page.get("id") or "")
            if not target_id:
                raise AccountBrowserLoginUnavailable(
                    "account login target id missing"
                )
            self._write_target(target_id)
            return self.status()
    def target_id(self) -> str:
        with self._lock:
            target = self._read_target()
            if not target:
                raise AccountBrowserLoginUnavailable(
                    "account login browser session is not running"
                )
            self._target_info(target)
            return target

    def status(self) -> dict[str, Any]:
        with self._lock:
            target = self._read_target()
            if not target:
                raise AccountBrowserLoginUnavailable(
                    "account login browser session is not running"
                )
            page = self._target_info(target)
            expression = """JSON.stringify({
              url:location.href,
              title:document.title,
              profile:(document.querySelector(
                '[data-testid="AppTabBar_Profile_Link"]'
              )?.getAttribute('href') || '')
            })"""
            details: dict[str, Any] = {}
            try:
                value = self._evaluate(page, expression)
                parsed = (
                    json.loads(value)
                    if isinstance(value, str)
                    else {}
                )
                if isinstance(parsed, dict):
                    details = parsed
            except AccountBrowserLoginUnavailable:
                pass
            profile = str(details.get("profile") or "")
            return {
                "running": True,
                "url": str(
                    details.get("url") or page.get("url") or ""
                ),
                "title": str(
                    details.get("title") or page.get("title") or ""
                ),
                "x_authenticated": bool(profile),
                "account_identity": (
                    profile.strip("/").split("/", 1)[0]
                    if profile else ""
                ),
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            status = None
            try:
                status = self.status()
            except AccountBrowserLoginUnavailable:
                pass
            self._close_target()
            return {
                "ok": True,
                "running": False,
                "authenticated": bool(
                    (status or {}).get("x_authenticated")
                ),
                "identity": str(
                    (status or {}).get("account_identity") or ""
                ),
            }

    def _close_target(self) -> None:
        target = self._read_target()
        if not target:
            return
        try:
            with urllib.request.urlopen(
                self.cdp_url()
                + "/json/close/"
                + quote(target, safe=""),
                timeout=2,
            ):
                pass
        except Exception:
            pass
        self._clear_target()


__all__ = [
    "AccountBrowserLoginControl",
    "AccountBrowserLoginUnavailable",
    "DEFAULT_LOGIN_URL",
]
