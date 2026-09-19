"""Authenticated operator control for Ferro's persistent local browser.

The controller deliberately never persists typed text. It only owns a short-lived
CDP tab; Chrome's normal profile storage retains the resulting site session.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import quote, urlparse
import urllib.request


DEFAULT_LOGIN_URL = "https://x.com/i/flow/login"
_ALLOWED_HOSTS = {"x.com", "www.x.com"}
_VIEWPORT_WIDTH = 1120
_VIEWPORT_HEIGHT = 780


class LocalBrowserControlUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalBrowserSnapshot:
    url: str
    title: str
    screenshot_b64: str
    width: int = _VIEWPORT_WIDTH
    height: int = _VIEWPORT_HEIGHT
    x_authenticated: bool = False
    account_identity: str = ""


class LocalBrowserControl:
    """One operator-owned browser tab protected by the Operator Console session."""

    def __init__(self, *, browser_factory=None, state_root: Path | None = None) -> None:
        self.browser_factory = browser_factory
        self.state_path = (
            Path(state_root) / "operator" / "browser-session-target.json"
            if state_root is not None else None
        )
        self._browser: Any | None = None
        self._lock = threading.RLock()

    def _factory(self):
        if self.browser_factory is not None:
            return self.browser_factory
        try:
            from jev_ultrafast.browser import Browser
        except ImportError as exc:
            raise LocalBrowserControlUnavailable("local browser runtime is not installed") from exc
        return Browser

    @staticmethod
    def _validate_url(url: str) -> str:
        raw = str(url or DEFAULT_LOGIN_URL).strip()
        parsed = urlparse(raw)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
            raise ValueError("local login browser is restricted to x.com")
        return raw

    def _read_persisted_target(self) -> str:
        if self.state_path is None:
            return ""
        try:
            body = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ""
        return str(body.get("target_id") or "") if isinstance(body, dict) else ""

    def _persist_target(self, target_id: str) -> None:
        if self.state_path is None:
            return
        target = str(target_id or "")
        if not target:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_path.parent, 0o700)
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"schema_version": 1, "target_id": target},
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)
        os.chmod(self.state_path, 0o600)

    def _clear_persisted_target(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _close_target(target_id: str) -> None:
        target = str(target_id or "")
        if not target:
            return
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9333/json/close/" + quote(target, safe=""),
                timeout=2,
            ):
                pass
        except Exception:
            pass

    def start(self, url: str = DEFAULT_LOGIN_URL) -> dict[str, Any]:
        target = self._validate_url(url)
        with self._lock:
            self._close_unlocked()
            try:
                self._browser = self._factory()(target)
            except Exception as exc:
                self._browser = None
                raise LocalBrowserControlUnavailable(type(exc).__name__) from exc
            self._persist_target(str(getattr(self._browser, "target", "") or ""))
            return self.status()

    def _require(self):
        if self._browser is None:
            raise LocalBrowserControlUnavailable("local login browser session is not running")
        return self._browser

    def target_id(self) -> str:
        with self._lock:
            if self._browser is not None:
                return str(getattr(self._browser, "target", "") or "")
            target = self._read_persisted_target()
            if target:
                return target
            raise LocalBrowserControlUnavailable("local login browser session is not running")

    def status(self) -> dict[str, Any]:
        with self._lock:
            browser = self._require()
            url = str(browser.evaluate("location.href") or "")
            title = str(browser.evaluate("document.title") or "")
            profile = str(browser.evaluate(
                "document.querySelector('[data-testid=\"AppTabBar_Profile_Link\"]')"
                "?.getAttribute('href') || ''"
            ) or "")
            x_authenticated = bool(profile)
            return {
                "running": True,
                "url": url,
                "title": title,
                "x_authenticated": x_authenticated,
                "account_identity": profile.strip("/").split("/", 1)[0] if profile else "",
            }

    def snapshot(self) -> LocalBrowserSnapshot:
        with self._lock:
            browser = self._require()
            last_error: Exception | None = None
            for attempt in range(6):
                try:
                    state = browser.observe(screenshot=True)
                    shot = str(state.get("screenshot") or "")
                    if not shot:
                        raise LocalBrowserControlUnavailable("browser screenshot unavailable")
                    url = str(
                        (state.get("page") or {}).get("url")
                        or state.get("url")
                        or browser.evaluate("location.href")
                        or ""
                    )
                    title = str(
                        (state.get("page") or {}).get("title")
                        or state.get("title")
                        or browser.evaluate("document.title")
                        or ""
                    )
                    profile = str(browser.evaluate(
                        "document.querySelector('[data-testid=\"AppTabBar_Profile_Link\"]')"
                        "?.getAttribute('href') || ''"
                    ) or "")
                    x_authenticated = bool(profile)
                    return LocalBrowserSnapshot(
                        url=url,
                        title=title,
                        screenshot_b64=shot,
                        x_authenticated=x_authenticated,
                        account_identity=profile.strip("/").split("/", 1)[0] if profile else "",
                    )
                except LocalBrowserControlUnavailable:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt == 5:
                        break
                    time.sleep(0.15)
            raise LocalBrowserControlUnavailable(
                type(last_error).__name__ if last_error else "browser snapshot unavailable"
            ) from last_error

    def click(self, *, x_ratio: float, y_ratio: float) -> dict[str, Any]:
        x = float(x_ratio)
        y = float(y_ratio)
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("click coordinates must be normalized to 0..1")
        with self._lock:
            browser = self._require()
            px = x * _VIEWPORT_WIDTH
            py = y * _VIEWPORT_HEIGHT
            for event in ("mousePressed", "mouseReleased"):
                browser.call(
                    "Input.dispatchMouseEvent",
                    type=event,
                    x=px,
                    y=py,
                    button="left",
                    clickCount=1,
                )
            return {"ok": True}

    def type_text(self, text: str) -> dict[str, Any]:
        value = str(text)
        if not value or len(value) > 1024 or "\r" in value or "\n" in value:
            raise ValueError("text must be 1-1024 characters without newlines")
        with self._lock:
            browser = self._require()
            browser.call("Input.insertText", text=value)
            return {"ok": True, "length": len(value)}

    def key(self, key: str) -> dict[str, Any]:
        raw = str(key or "")
        mapping = {
            "Enter": ("Enter", "Enter"),
            "Tab": ("Tab", "Tab"),
            "Backspace": ("Backspace", "Backspace"),
            "Escape": ("Escape", "Escape"),
            "ArrowUp": ("ArrowUp", "ArrowUp"),
            "ArrowDown": ("ArrowDown", "ArrowDown"),
        }
        if raw not in mapping:
            raise ValueError("unsupported key")
        key_name, code = mapping[raw]
        with self._lock:
            browser = self._require()
            for kind in ("keyDown", "keyUp"):
                browser.call("Input.dispatchKeyEvent", type=kind, key=key_name, code=code)
            return {"ok": True}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._close_unlocked()
            return {"ok": True, "running": False}

    def _close_unlocked(self) -> None:
        browser, self._browser = self._browser, None
        persisted = self._read_persisted_target()
        current = str(getattr(browser, "target", "") or "") if browser is not None else ""
        if browser is not None:
            try:
                browser.close()
            except Exception:
                self._close_target(current)
            if persisted and persisted != current:
                self._close_target(persisted)
        elif persisted:
            self._close_target(persisted)
        self._clear_persisted_target()


__all__ = [
    "DEFAULT_LOGIN_URL",
    "LocalBrowserControl",
    "LocalBrowserControlUnavailable",
    "LocalBrowserSnapshot",
]
