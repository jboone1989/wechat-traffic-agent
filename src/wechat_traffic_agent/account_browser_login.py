"""Operator login control for one Steel-backed persistent account profile."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from websockets.sync.client import connect as ws_connect

from .account_browser_profiles import AccountBrowserProfileManager


DEFAULT_LOGIN_URL = "https://x.com/i/flow/login"
_ALLOWED_HOSTS = {"x.com", "www.x.com"}


class AccountBrowserLoginUnavailable(RuntimeError):
    pass


class AccountBrowserLoginControl:
    """Short-lived operator tab inside one account's persistent Steel browser."""

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
                {"schema_version": 2, "target_id": str(target_id)},
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
        if not runtime.cdp_ws:
            raise AccountBrowserLoginUnavailable(
                "Steel account browser websocket unavailable"
            )
        return runtime.cdp_ws

    def _validated_ws_url(self) -> str:
        value = self.cdp_url()
        parsed = urlparse(value)
        api = urlparse(str(getattr(self.profile_manager, "api_base", "")))
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise AccountBrowserLoginUnavailable("invalid Steel CDP websocket")
        if api.hostname and parsed.hostname != api.hostname:
            raise AccountBrowserLoginUnavailable("unexpected Steel CDP host")
        api_port = api.port or (443 if api.scheme == "https" else 80)
        ws_port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        if api.hostname and api_port != ws_port:
            raise AccountBrowserLoginUnavailable("unexpected Steel CDP port")
        return value

    @staticmethod
    def _call(
        cdp,
        command_id: int,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        command: dict[str, Any] = {
            "id": int(command_id),
            "method": method,
            "params": params or {},
        }
        if session_id:
            command["sessionId"] = session_id
        cdp.send(json.dumps(command))
        for _ in range(120):
            message = json.loads(cdp.recv(timeout=3))
            if message.get("id") != command_id:
                continue
            if message.get("error"):
                raise AccountBrowserLoginUnavailable(
                    f"Steel CDP command failed: {method}"
                )
            result = message.get("result") or {}
            return result if isinstance(result, dict) else {}
        raise AccountBrowserLoginUnavailable(
            f"Steel CDP response missing: {method}"
        )

    def _target_info(self, target_id: str) -> dict[str, Any]:
        try:
            with ws_connect(
                self._validated_ws_url(),
                origin=None,
                open_timeout=3,
                close_timeout=1,
                max_size=4 * 1024 * 1024,
            ) as cdp:
                result = self._call(
                    cdp,
                    1,
                    "Target.getTargetInfo",
                    {"targetId": str(target_id)},
                )
        except AccountBrowserLoginUnavailable:
            raise
        except Exception as exc:
            raise AccountBrowserLoginUnavailable(
                "Steel browser target unavailable"
            ) from exc
        info = result.get("targetInfo")
        if not isinstance(info, dict):
            raise AccountBrowserLoginUnavailable(
                "Steel browser target no longer exists"
            )
        return {
            "id": str(info.get("targetId") or target_id),
            "targetId": str(info.get("targetId") or target_id),
            "url": str(info.get("url") or ""),
            "title": str(info.get("title") or ""),
            "type": str(info.get("type") or ""),
        }

    def _evaluate(self, page: dict[str, Any], expression: str) -> Any:
        target_id = str(page.get("targetId") or page.get("id") or "")
        if not target_id:
            raise AccountBrowserLoginUnavailable("browser target id missing")
        try:
            with ws_connect(
                self._validated_ws_url(),
                origin=None,
                open_timeout=3,
                close_timeout=1,
                max_size=4 * 1024 * 1024,
            ) as cdp:
                attached = self._call(
                    cdp,
                    1,
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
                session_id = str(attached.get("sessionId") or "")
                if not session_id:
                    raise AccountBrowserLoginUnavailable(
                        "Steel target attach did not return session id"
                    )
                evaluated = self._call(
                    cdp,
                    2,
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "returnByValue": True,
                    },
                    session_id=session_id,
                )
                result = evaluated.get("result") or {}
                return result.get("value") if isinstance(result, dict) else None
        except AccountBrowserLoginUnavailable:
            raise
        except Exception as exc:
            raise AccountBrowserLoginUnavailable(
                "Steel browser evaluation unavailable"
            ) from exc

    def start(self, url: str = DEFAULT_LOGIN_URL) -> dict[str, Any]:
        target = self._validate_url(url)
        with self._lock:
            self._close_target()
            try:
                with ws_connect(
                    self._validated_ws_url(),
                    origin=None,
                    open_timeout=3,
                    close_timeout=1,
                    max_size=4 * 1024 * 1024,
                ) as cdp:
                    result = self._call(
                        cdp,
                        1,
                        "Target.createTarget",
                        {"url": target},
                    )
            except AccountBrowserLoginUnavailable:
                raise
            except Exception as exc:
                raise AccountBrowserLoginUnavailable(
                    "could not create Steel account login target"
                ) from exc
            target_id = str(result.get("targetId") or "")
            if not target_id:
                raise AccountBrowserLoginUnavailable(
                    "Steel account login target id missing"
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
                parsed = json.loads(value) if isinstance(value, str) else {}
                if isinstance(parsed, dict):
                    details = parsed
            except AccountBrowserLoginUnavailable:
                pass
            profile = str(details.get("profile") or "")
            return {
                "running": True,
                "url": str(details.get("url") or page.get("url") or ""),
                "title": str(details.get("title") or page.get("title") or ""),
                "x_authenticated": bool(profile),
                "account_identity": (
                    profile.strip("/").split("/", 1)[0] if profile else ""
                ),
                "runtime_provider": "STEEL_SELF_HOSTED",
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            status = None
            try:
                status = self.status()
            except AccountBrowserLoginUnavailable:
                pass
            self._close_target()
            self.profile_manager.stop(self.account_id)
            return {
                "ok": True,
                "running": False,
                "authenticated": bool((status or {}).get("x_authenticated")),
                "identity": str((status or {}).get("account_identity") or ""),
                "runtime_provider": "STEEL_SELF_HOSTED",
            }

    def _close_target(self) -> None:
        target = self._read_target()
        if not target:
            return
        runtime = self.profile_manager.status(self.account_id)
        if not runtime.running:
            self._clear_target()
            return
        with ws_connect(
            self._validated_ws_url(),
            origin=None,
            open_timeout=3,
            close_timeout=1,
            max_size=4 * 1024 * 1024,
        ) as cdp:
            result = self._call(
                cdp,
                1,
                "Target.closeTarget",
                {"targetId": target},
            )
        if result.get("success") is False:
            raise AccountBrowserLoginUnavailable(
                "Steel refused to close account login target"
            )
        self._clear_target()


__all__ = [
    "AccountBrowserLoginControl",
    "AccountBrowserLoginUnavailable",
    "DEFAULT_LOGIN_URL",
]
