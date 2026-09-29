"""Operator login control for Ferro's dedicated ChatGPT cortex browser profile."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import quote, urlparse
import urllib.request

from websockets.sync.client import connect as ws_connect

from .chatgpt_cortex_activator import _ensure_cortex_chromium


class ChatGPTCortexBrowserUnavailable(RuntimeError):
    pass


class ChatGPTCortexBrowserControl:
    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "cortex" / "chatgpt-web"
        self.state_path = self.root / "login-target.json"
        self._lock = threading.RLock()

    def cdp_url(self) -> str:
        return _ensure_cortex_chromium(self.state_root)

    def _read_target(self) -> str:
        try:
            body = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ""
        return str(body.get("target_id") or "") if isinstance(body, dict) else ""

    def _write_target(self, target_id: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "target_id": str(target_id)}, handle)
            handle.write("\n")
        os.replace(tmp, self.state_path)
        os.chmod(self.state_path, 0o600)

    def _clear_target(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    def _pages(self) -> list[dict[str, Any]]:
        try:
            with urllib.request.urlopen(self.cdp_url() + "/json/list", timeout=2) as response:
                body = json.load(response)
        except Exception as exc:
            raise ChatGPTCortexBrowserUnavailable("cortex CDP endpoint unavailable") from exc
        return body if isinstance(body, list) else []

    def _target_info(self, target_id: str) -> dict[str, Any]:
        for page in self._pages():
            if str(page.get("id") or "") == str(target_id):
                return page
        raise ChatGPTCortexBrowserUnavailable("cortex browser target no longer exists")

    def _safe_ws_url(self, page: dict[str, Any]) -> str:
        value = str(page.get("webSocketDebuggerUrl") or "")
        parsed = urlparse(value)
        base = urlparse(self.cdp_url())
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port != base.port
        ):
            raise ChatGPTCortexBrowserUnavailable("unsafe cortex CDP websocket")
        return value

    def _evaluate(self, page: dict[str, Any], expression: str) -> Any:
        try:
            with ws_connect(
                self._safe_ws_url(page),
                origin=None,
                open_timeout=2,
                close_timeout=1,
                max_size=2 * 1024 * 1024,
            ) as cdp:
                cdp.send(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }))
                for _ in range(40):
                    message = json.loads(cdp.recv(timeout=2))
                    if message.get("id") != 1:
                        continue
                    if message.get("error"):
                        raise ChatGPTCortexBrowserUnavailable("cortex CDP evaluation failed")
                    return (((message.get("result") or {}).get("result") or {}).get("value"))
        except ChatGPTCortexBrowserUnavailable:
            raise
        except Exception as exc:
            raise ChatGPTCortexBrowserUnavailable("cortex browser evaluation unavailable") from exc
        raise ChatGPTCortexBrowserUnavailable("cortex CDP evaluation response missing")

    def start(self) -> dict[str, Any]:
        with self._lock:
            self._close_target()
            request = urllib.request.Request(
                self.cdp_url() + "/json/new?" + quote("https://chatgpt.com/", safe=""),
                method="PUT",
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    page = json.load(response)
            except Exception as exc:
                raise ChatGPTCortexBrowserUnavailable(
                    "could not create ChatGPT cortex login target"
                ) from exc
            target_id = str(page.get("id") or "")
            if not target_id:
                raise ChatGPTCortexBrowserUnavailable("cortex target id missing")
            self._write_target(target_id)
            return self.status()

    def target_id(self) -> str:
        with self._lock:
            target = self._read_target()
            if not target:
                raise ChatGPTCortexBrowserUnavailable(
                    "cortex login browser session is not running"
                )
            self._target_info(target)
            return target

    def status(self) -> dict[str, Any]:
        with self._lock:
            target = self._read_target()
            if not target:
                raise ChatGPTCortexBrowserUnavailable(
                    "cortex login browser session is not running"
                )
            page = self._target_info(target)
            value = self._evaluate(
                page,
                """JSON.stringify({
                  url: location.href,
                  title: document.title,
                  authenticated: Boolean(
                    document.querySelector('#prompt-textarea') ||
                    document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
                    document.querySelector('main [contenteditable="true"]')
                  )
                })""",
            )
            try:
                details = json.loads(value) if isinstance(value, str) else {}
            except json.JSONDecodeError:
                details = {}
            authenticated = bool(details.get("authenticated"))
            return {
                "running": True,
                "url": str(details.get("url") or page.get("url") or ""),
                "title": str(details.get("title") or page.get("title") or ""),
                "authenticated": authenticated,
                "identity": "ChatGPT" if authenticated else "",
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            status = None
            try:
                status = self.status()
            except ChatGPTCortexBrowserUnavailable:
                pass
            self._close_target()
            return {
                "ok": True,
                "running": False,
                "authenticated": bool((status or {}).get("authenticated")),
                "identity": str((status or {}).get("identity") or ""),
            }

    def _close_target(self) -> None:
        target = self._read_target()
        if not target:
            return
        try:
            with urllib.request.urlopen(
                self.cdp_url() + "/json/close/" + quote(target, safe=""),
                timeout=2,
            ):
                pass
        except Exception:
            pass
        self._clear_target()


__all__ = [
    "ChatGPTCortexBrowserControl",
    "ChatGPTCortexBrowserUnavailable",
]
