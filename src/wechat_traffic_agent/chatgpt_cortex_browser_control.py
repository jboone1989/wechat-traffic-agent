"""ChatGPT Cortex control over Ferro's existing X persistent browser profile.

Cortex owns no Chromium launcher and no browser profile.  It opens a dedicated
ChatGPT tab inside the configured X account's persistent Chromium runtime, so
operator login, profile persistence, CDP streaming, and lifecycle all use the
same account-scoped browser path as X.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from .account_browser_login import (
    AccountBrowserLoginControl,
    AccountBrowserLoginUnavailable,
)
from .account_registry import AccountRegistry


_DEFAULT_URL = "https://chatgpt.com/"
_ALLOWED_HOSTS = {"chatgpt.com", "www.chatgpt.com"}

# The shared account control raises this type already.  Keep the Cortex-facing
# name so callers do not need a second error hierarchy for the same runtime.
ChatGPTCortexBrowserUnavailable = AccountBrowserLoginUnavailable


def _resolve_x_account_id(state_root: Path, requested: str = "") -> str:
    registry = AccountRegistry(Path(state_root))
    explicit = str(requested or os.environ.get("CONTENT_X_ACCOUNT_ID", "")).strip()
    if explicit:
        try:
            record = registry.get(explicit)
        except (KeyError, ValueError) as exc:
            raise ChatGPTCortexBrowserUnavailable(
                "configured X account instance does not exist"
            ) from exc
        if record.provider_id != "x":
            raise ChatGPTCortexBrowserUnavailable(
                "configured browser account is not an X account"
            )
        return record.account_id

    rows = registry.list(provider_id="x")
    if not rows:
        raise ChatGPTCortexBrowserUnavailable(
            "ChatGPT Cortex requires an existing X account browser profile"
        )
    if len(rows) != 1:
        raise ChatGPTCortexBrowserUnavailable(
            "multiple X accounts are registered; CONTENT_X_ACCOUNT_ID is required"
        )
    return rows[0].account_id


class _SharedProfileChatGPTControl(AccountBrowserLoginControl):
    """ChatGPT tab hosted by an account-scoped persistent browser runtime."""

    def __init__(
        self,
        *,
        state_root: Path,
        account_id: str,
        profile_manager=None,
    ) -> None:
        super().__init__(
            state_root=state_root,
            account_id=account_id,
            profile_manager=profile_manager,
        )
        # Keep X's own login tab independent while sharing the same Chrome
        # process/profile/cookies and the same BrowserSessionStream transport.
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
            try:
                details = json.loads(value) if isinstance(value, str) else {}
            except json.JSONDecodeError:
                details = {}
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
                "shared_profile": "x",
            }


class ChatGPTCortexBrowserControl:
    """Lazy Cortex adapter onto the configured X persistent browser profile."""

    def __init__(
        self,
        *,
        state_root: Path,
        account_id: str = "",
        profile_manager=None,
    ) -> None:
        self.state_root = Path(state_root)
        self.requested_account_id = str(account_id or "")
        self.profile_manager = profile_manager
        self._delegate: _SharedProfileChatGPTControl | None = None
        self._lock = threading.RLock()

    def _control(self) -> _SharedProfileChatGPTControl:
        with self._lock:
            if self._delegate is None:
                account_id = _resolve_x_account_id(
                    self.state_root,
                    self.requested_account_id,
                )
                self._delegate = _SharedProfileChatGPTControl(
                    state_root=self.state_root,
                    account_id=account_id,
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
