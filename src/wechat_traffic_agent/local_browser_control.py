"""Authenticated operator control for Ferro's persistent local browser.

The controller deliberately never persists typed text. It only owns a short-lived
CDP tab; Chrome's normal profile storage retains the resulting site session.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any
from urllib.parse import urlparse


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

    def __init__(self, *, browser_factory=None) -> None:
        self.browser_factory = browser_factory
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

    def start(self, url: str = DEFAULT_LOGIN_URL) -> dict[str, Any]:
        target = self._validate_url(url)
        with self._lock:
            self._close_unlocked()
            try:
                self._browser = self._factory()(target)
            except Exception as exc:
                self._browser = None
                raise LocalBrowserControlUnavailable(type(exc).__name__) from exc
            return self.status()

    def _require(self):
        if self._browser is None:
            raise LocalBrowserControlUnavailable("local login browser session is not running")
        return self._browser

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
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


__all__ = [
    "DEFAULT_LOGIN_URL",
    "LocalBrowserControl",
    "LocalBrowserControlUnavailable",
    "LocalBrowserSnapshot",
]
