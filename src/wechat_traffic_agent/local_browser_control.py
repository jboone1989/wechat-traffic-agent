"""Operator control for Agent Runtime's persistent X browser profile."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from typing import Any

from agent_runtime.integrations import (
    BROWSER_NAVIGATE,
    BROWSER_SCREENSHOT,
    BrowserHarnessProvider,
    BrowserHarnessRuntime,
    BrowserPolicy,
)


DEFAULT_LOGIN_URL = "https://x.com/i/flow/login"
_VIEWPORT_WIDTH = 1280
_VIEWPORT_HEIGHT = 900
_SESSION_ID = "ferro-operator-x"


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
    """Thin operator adapter over Agent Runtime; never starts its own Chrome."""

    def __init__(self, *, state_root: Path | None = None) -> None:
        self.state_root = Path(state_root or (Path.home() / 'content-agent-state'))
        self._accounts: dict[str, LocalBrowserControl] = {}
        self._account_id = str(os.environ.get('AGENT_RUNTIME_X_ACCOUNT_ID') or 'default').strip()
        self._registry_account_id = ''
        self._expected_identity = ''
        self._lock = threading.RLock()
        self._provider: BrowserHarnessProvider | None = None
        self._target_id = ""
        self._running = False

    def for_account(self, account_id: str) -> "LocalBrowserControl":
        from .browser_accounts import x_account_binding
        record, context = x_account_binding(self.state_root, account_id)
        with self._lock:
            if account_id not in self._accounts:
                child = type(self)(state_root=self.state_root)
                child._account_id = context
                child._registry_account_id = record.account_id
                child._expected_identity = record.identity
                self._accounts[account_id] = child
            return self._accounts[account_id]

    @staticmethod
    def _runtime_state_path() -> Path:
        return Path(
            os.environ.get(
                "AGENT_RUNTIME_X_PROFILE_STATE",
                "~/.local/state/agent-runtime/browser/profiles/x/runtime.json",
            )
        ).expanduser()

    @classmethod
    def _cdp_url(cls) -> str:
        try:
            state = json.loads(cls._runtime_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalBrowserControlUnavailable(
                "Agent Runtime X browser profile is unavailable"
            ) from exc
        port = state.get("cdp_port") if isinstance(state, dict) else None
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise LocalBrowserControlUnavailable(
                "Agent Runtime X browser profile has no valid CDP port"
            )
        return f"http://127.0.0.1:{port}"

    @staticmethod
    def _harness_executable() -> str:
        explicit = str(os.environ.get("AGENT_RUNTIME_BROWSER_HARNESS") or "").strip()
        if explicit:
            return explicit
        found = shutil.which("browser-harness")
        if found:
            return found
        candidate = Path(sys.executable).resolve().with_name("browser-harness")
        if candidate.is_file():
            return str(candidate)
        candidate = Path.home() / "browser-use-venv" / "bin" / "browser-harness"
        if candidate.is_file():
            return str(candidate)
        raise LocalBrowserControlUnavailable("browser-harness executable is unavailable")

    def _browser(self) -> BrowserHarnessProvider:
        if self._provider is None:
            self._provider = BrowserHarnessProvider(
                policy=BrowserPolicy(
                    max_sessions=2,
                    timeout_seconds=30.0,
                    max_text_chars=100_000,
                    max_elements=200,
                    max_input_chars=100_000,
                    allowed_hosts=frozenset({"x.com", "www.x.com"}),
                ),
                runtime=BrowserHarnessRuntime(
                    executable=self._harness_executable(),
                    state_dir=Path(
                        os.environ.get(
                            "AGENT_RUNTIME_BROWSER_STATE",
                            "~/.local/state/agent-runtime/browser-harness",
                        )
                    ).expanduser(),
                    cdp_url=self._cdp_url(),
                    account_id=self._account_id,
                ),
            )
        return self._provider

    @staticmethod
    def _validate_url(url: str) -> str:
        raw = str(url or DEFAULT_LOGIN_URL).strip()
        if not raw.startswith(("https://x.com/", "https://www.x.com/")):
            raise ValueError("local login browser is restricted to x.com")
        return raw

    def cdp_url(self) -> str:
        return self._cdp_url()

    def start(self, url: str = DEFAULT_LOGIN_URL) -> dict[str, Any]:
        target = self._validate_url(url)
        with self._lock:
            browser = self._browser()
            browser.invoke(
                BROWSER_NAVIGATE,
                {"session_id": _SESSION_ID, "url": target},
            )
            adopted = browser.adopt_existing_tab(_SESSION_ID, "https://x.com/")
            self._target_id = str(adopted.get("target_id") or "")
            if not self._target_id:
                raise LocalBrowserControlUnavailable("Agent Runtime X target is unavailable")
            self._running = True
            return self.status()

    def open(self) -> dict[str, Any]:
        """Reattach and focus; retain an in-progress login or existing session."""
        with self._lock:
            self._target_id = ""
            self.target_id()
            self._browser().run_session_script(
                _SESSION_ID,
                "cdp('Page.bringToFront')\nresult = {'ok': True}",
                timeout_seconds=30,
            )
            return self.status()

    def _require(self) -> BrowserHarnessProvider:
        if not self._running:
            raise LocalBrowserControlUnavailable("Agent Runtime X browser session is not active")
        return self._browser()

    def target_id(self) -> str:
        with self._lock:
            if self._target_id:
                return self._target_id
            adopted = self._browser().adopt_existing_tab(_SESSION_ID, "https://x.com/", **({"open_if_missing": True} if self._account_id != "default" else {}))
            self._target_id = str(adopted.get("target_id") or "")
            self._running = bool(self._target_id)
            if not self._target_id:
                raise LocalBrowserControlUnavailable("Agent Runtime X target is unavailable")
            return self._target_id

    def status(self) -> dict[str, Any]:
        with self._lock:
            browser = self._require()
            value = browser.evaluate(
                _SESSION_ID,
                """(() => {
                  const profile=document.querySelector('[data-testid="AppTabBar_Profile_Link"]')
                    ?.getAttribute('href') || '';
                  return {
                    url: location.href,
                    title: document.title,
                    profile
                  };
                })()""",
            )
            row = value if isinstance(value, dict) else {}
            profile = str(row.get("profile") or "")
            identity = profile.strip("/").split("/", 1)[0] if profile else ""
            if identity and self._expected_identity and identity.casefold() != self._expected_identity.casefold():
                raise LocalBrowserControlUnavailable("X browser identity does not match selected account")
            if self._registry_account_id:
                from .x_browser import _write_json
                _write_json(self.state_root / "accounts/browser-verifications" / (self._registry_account_id + ".json"),
                            {"account_id": self._registry_account_id, "identity": identity,
                             "context": self._account_id, "status": "CONNECTED" if identity else "NEEDS_LOGIN"})
            return {
                "browser_account_id": self._account_id,
                "running": True,
                "url": str(row.get("url") or ""),
                "title": str(row.get("title") or ""),
                "x_authenticated": bool(profile),
                "account_identity": profile.strip("/").split("/", 1)[0] if profile else "",
            }

    def snapshot(self) -> LocalBrowserSnapshot:
        with self._lock:
            browser = self._require()
            shot = browser.invoke(
                BROWSER_SCREENSHOT,
                {"session_id": _SESSION_ID, "full_page": False},
            )
            status = self.status()
            return LocalBrowserSnapshot(
                url=str(status.get("url") or ""),
                title=str(status.get("title") or ""),
                screenshot_b64=str(shot.get("content") or ""),
                x_authenticated=bool(status.get("x_authenticated")),
                account_identity=str(status.get("account_identity") or ""),
            )

    def click(self, *, x_ratio: float, y_ratio: float) -> dict[str, Any]:
        x = float(x_ratio)
        y = float(y_ratio)
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("click coordinates must be normalized to 0..1")
        with self._lock:
            browser = self._require()
            browser.run_session_script(
                _SESSION_ID,
                (
                    f"click_at_xy({x * _VIEWPORT_WIDTH!r}, {y * _VIEWPORT_HEIGHT!r})\n"
                    "info = page_info()\n"
                    "result = {'url': info.get('url', '')}"
                ),
                timeout_seconds=30,
            )
            return {"ok": True}

    def type_text(self, text: str) -> dict[str, Any]:
        value = str(text)
        if not value or len(value) > 1024 or "\r" in value or "\n" in value:
            raise ValueError("text must be 1-1024 characters without newlines")
        with self._lock:
            browser = self._require()
            browser.run_session_script(
                _SESSION_ID,
                (
                    f"type_text({json.dumps(value)})\n"
                    "info = page_info()\n"
                    "result = {'url': info.get('url', '')}"
                ),
                timeout_seconds=30,
            )
            return {"ok": True, "length": len(value)}

    def key(self, key: str) -> dict[str, Any]:
        raw = str(key or "")
        if raw not in {"Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown"}:
            raise ValueError("unsupported key")
        with self._lock:
            browser = self._require()
            browser.run_session_script(
                _SESSION_ID,
                (
                    f"press_key({json.dumps(raw)})\n"
                    "info = page_info()\n"
                    "result = {'url': info.get('url', '')}"
                ),
                timeout_seconds=30,
            )
            return {"ok": True}

    def stop(self) -> dict[str, Any]:
        # Closing the operator view never destroys the persistent Agent Runtime
        # profile or login state.
        with self._lock:
            self._running = False
            self._target_id = ""
            return {"ok": True, "running": False}


__all__ = [
    "DEFAULT_LOGIN_URL",
    "LocalBrowserControl",
    "LocalBrowserControlUnavailable",
    "LocalBrowserSnapshot",
]
