"""Persistent ChatGPT browser control for Ferro Cortex.

ChatGPT owns its own persistent Chromium profile.  It does not depend on an X
account or X cookies.  The existing native Chromium/profile manager is reused,
and Browser Use/Browser Harness can attach through the resulting localhost CDP.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from .account_browser_login import (
    AccountBrowserLoginControl,
    AccountBrowserLoginUnavailable,
)
from .account_browser_profiles import AccountBrowserProfileManager


_DEFAULT_URL = "https://chatgpt.com/"
_ALLOWED_HOSTS = {"chatgpt.com", "www.chatgpt.com"}
_CORTEX_ACCOUNT_ID = "cortex_chatgpt"
_CORTEX_PROFILE_ID = "browser_cortex_chatgpt"

ChatGPTCortexBrowserUnavailable = AccountBrowserLoginUnavailable


@dataclass(frozen=True)
class _CortexRecord:
    account_id: str = _CORTEX_ACCOUNT_ID
    browser_profile_id: str = _CORTEX_PROFILE_ID


class _CortexRegistry:
    """Minimal registry contract required by AccountBrowserProfileManager."""

    def __init__(self) -> None:
        self.record = _CortexRecord()

    def get(self, account_id: str) -> _CortexRecord:
        if str(account_id) != self.record.account_id:
            raise KeyError(str(account_id))
        return self.record

    def set_browser_profile(self, account_id: str, profile_id: str):
        if str(account_id) != self.record.account_id:
            raise KeyError(str(account_id))
        return self.record


class _ChatGPTProfileControl(AccountBrowserLoginControl):
    def __init__(
        self,
        *,
        state_root: Path,
        profile_manager=None,
    ) -> None:
        if profile_manager is None:
            profile_manager = AccountBrowserProfileManager(
                state_root,
                registry=_CortexRegistry(),
            )
        super().__init__(
            state_root=state_root,
            account_id=_CORTEX_ACCOUNT_ID,
            profile_manager=profile_manager,
        )
        self.state_path = self.folder / "chatgpt-cortex-target.json"

    @staticmethod
    def _validate_url(url: str) -> str:
        raw = str(url or _DEFAULT_URL).strip()
        parsed = urlparse(raw)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS
        ):
            raise ValueError("ChatGPT cortex browser is restricted to chatgpt.com")
        return raw

    def start(self) -> dict[str, Any]:
        return super().start(_DEFAULT_URL)

    def status(self) -> dict[str, Any]:
        with self._lock:
            target = self._read_target()
            if not target:
                raise ChatGPTCortexBrowserUnavailable(
                    "cortex login browser session is not running"
                )
            page = self._target_info(target)
            details: dict[str, Any] = {}
            try:
                value = self._evaluate(
                    page,
                    """JSON.stringify({
                      url: location.href,
                      title: document.title,
                      composer: Boolean(
                        document.querySelector('#prompt-textarea') ||
                        document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
                        document.querySelector('main [contenteditable="true"]')
                      ),
                      loginRequired: [...document.querySelectorAll('button,a,[role="button"]')].some(el => {
                        const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                        return text === 'log in' && Boolean(el.getClientRects().length);
                      })
                    })""",
                )
                parsed = json.loads(value) if isinstance(value, str) else {}
                if isinstance(parsed, dict):
                    details = parsed
            except (ChatGPTCortexBrowserUnavailable, json.JSONDecodeError):
                # A newly created ChatGPT target can briefly reject Runtime.evaluate
                # while navigation initializes. Keep the live session available so
                # the operator stream can render it instead of turning startup into
                # a false login failure.
                pass
            authenticated = (
                bool(details.get("composer"))
                and not bool(details.get("loginRequired"))
            )
            return {
                "running": True,
                "url": str(details.get("url") or page.get("url") or ""),
                "title": str(details.get("title") or page.get("title") or ""),
                "authenticated": authenticated,
                "identity": "ChatGPT" if authenticated else "",
                "account_id": self.account_id,
                "profile": "chatgpt-cortex",
            }


class ChatGPTCortexBrowserControl:
    """Lazy adapter for Ferro's dedicated persistent ChatGPT browser."""

    def __init__(
        self,
        *,
        state_root: Path,
        account_id: str = "",
        profile_manager=None,
    ) -> None:
        if account_id and str(account_id) != _CORTEX_ACCOUNT_ID:
            raise ValueError("ChatGPT Cortex uses its dedicated persistent profile")
        self.state_root = Path(state_root)
        self.profile_manager = profile_manager
        self._delegate: _ChatGPTProfileControl | None = None
        self._lock = threading.RLock()

    def _control(self) -> _ChatGPTProfileControl:
        with self._lock:
            if self._delegate is None:
                self._delegate = _ChatGPTProfileControl(
                    state_root=self.state_root,
                    profile_manager=self.profile_manager,
                )
            return self._delegate

    @property
    def account_id(self) -> str:
        return self._control().account_id

    def cdp_url(self) -> str:
        return self._control().cdp_url()

    def start(self) -> dict[str, Any]:
        return self._control().start()

    def target_id(self) -> str:
        return self._control().target_id()

    def status(self) -> dict[str, Any]:
        return self._control().status()

    def stop(self) -> dict[str, Any]:
        return self._control().stop()


__all__ = [
    "ChatGPTCortexBrowserControl",
    "ChatGPTCortexBrowserUnavailable",
]
