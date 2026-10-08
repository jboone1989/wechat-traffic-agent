"""Read-only X mention discovery through Agent Runtime's persistent X profile."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any

from agent_runtime.integrations import BROWSER_NAVIGATE

from wechat_traffic_agent.browser_runtime import (
    BrowserRuntimeUnavailable,
    BrowserTaskResult,
    BrowserTelemetryLedger,
)
from wechat_traffic_agent.x_browser import XBrowserSessionUnavailable
from wechat_traffic_agent.x_local_browser import build_agent_runtime_x_browser

X_MENTIONS_URL = "https://x.com/notifications/mentions"


def _normalize_text(value: str) -> str:
    return " ".join(str(value).split())


def browser_mention_post(*, text: str, href: str, own_handle: str) -> dict[str, Any] | None:
    match = re.match(r"^/([^/?#]+)/status/(\d+)(?:[/?#]|$)", str(href or ""))
    if not match:
        return None
    author, post_id = match.group(1), match.group(2)
    if author.casefold() == own_handle.casefold():
        return None
    body = _normalize_text(text)[:1000]
    if not body:
        return None
    return {
        "id": post_id,
        "text": body,
        "username": author,
        "url": f"https://x.com/{author}/status/{post_id}",
    }


class LocalHarnessXMentionsObserver:
    """Read mentions only through Agent Runtime's persistent X browser."""

    provider_name = "AGENT_RUNTIME_BROWSER"

    def __init__(self, *, state_root: Path, timeout_ms: int = 30_000) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.telemetry = BrowserTelemetryLedger(self.state_root)
        self.browser = build_agent_runtime_x_browser()

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "LocalHarnessXMentionsObserver":
        return cls(state_root=state_root)

    async def observe(self, *, now: datetime, max_results: int = 20) -> list[dict[str, Any]]:
        del now
        started = time.perf_counter()
        try:
            rows = await asyncio.to_thread(self._observe_sync, max_results)
        except Exception as exc:
            self.telemetry.append(
                task_type="X_MENTIONS_READ",
                site="x.com",
                result=BrowserTaskResult(
                    provider=self.provider_name,
                    status="FAILED",
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    action_count=0,
                    failure_reason=type(exc).__name__,
                    failure_detail=str(exc),
                ),
            )
            raise
        self.telemetry.append(
            task_type="X_MENTIONS_READ",
            site="x.com",
            result=BrowserTaskResult(
                provider=self.provider_name,
                status="COMPLETED",
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                action_count=0,
                final_url=X_MENTIONS_URL,
            ),
        )
        return rows

    def _observe_sync(self, max_results: int) -> list[dict[str, Any]]:
        session_id = "ferro-x-mentions"
        limit = max(1, min(int(max_results), 50))
        try:
            self.browser.invoke(
                BROWSER_NAVIGATE,
                {"session_id": session_id, "url": X_MENTIONS_URL},
            )
            body = self.browser.run_session_script(
                session_id,
                f"""
import time
deadline = time.monotonic() + 15.0
profile = ''
while time.monotonic() < deadline:
    profile = js('''(() => document.querySelector('[data-testid="AppTabBar_Profile_Link"]')?.getAttribute('href') || '')()''') or ''
    if profile:
        break
    time.sleep(0.15)
assert profile, 'Agent Runtime X profile is not authenticated'
handle = str(profile).strip('/').split('/', 1)[0]
deadline = time.monotonic() + 15.0
while time.monotonic() < deadline:
    if js('''(() => Boolean(document.querySelector('article')))()'''):
        break
    time.sleep(0.15)
rows = js('''(() => [...document.querySelectorAll('article')].slice(0,{limit}).map(a => ({{
  text:a.innerText || '',
  hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(x => x.getAttribute('href') || '')
}})))()''') or []
result = {{'handle': handle, 'rows': rows}}
""",
                timeout_seconds=max(30.0, self.timeout_ms / 1000 + 15.0),
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            raise BrowserRuntimeUnavailable(
                f"Agent Runtime X mentions failed: {exc}"
            ) from exc

        own_handle = str(body.get("handle") or "")
        raw_rows = body.get("rows") if isinstance(body.get("rows"), list) else []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_rows[:limit]:
            if not isinstance(raw, dict):
                continue
            for href in raw.get("hrefs") or []:
                row = browser_mention_post(
                    text=str(raw.get("text") or ""),
                    href=str(href),
                    own_handle=own_handle,
                )
                if row is not None and row["id"] not in seen:
                    seen.add(row["id"])
                    rows.append(row)
                    break
        return rows


class XBrowserMentionsObserverRouter:
    """Single read-only X route through Agent Runtime."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)

    async def observe(self, *, now: datetime, max_results: int = 20) -> list[dict[str, Any]]:
        try:
            observer = LocalHarnessXMentionsObserver.from_environment(
                state_root=self.state_root
            )
            return await observer.observe(now=now, max_results=max_results)
        except BrowserRuntimeUnavailable as exc:
            raise XBrowserSessionUnavailable(str(exc)) from exc


__all__ = [
    "LocalHarnessXMentionsObserver",
    "XBrowserMentionsObserverRouter",
    "browser_mention_post",
]
