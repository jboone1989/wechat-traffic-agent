"""Provider-routed, read-only X mention discovery."""
from __future__ import annotations

import asyncio
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import re
import time
from typing import Any

from .browser_runtime import BrowserRuntimeUnavailable, BrowserTaskResult, BrowserTelemetryLedger
from .x_browser import (
    TINYFISH_BASE_URL,
    TinyFishProfileClient,
    TinyFishProfileUnavailable,
    XBrowserSessionUnavailable,
    tinyfish_x_enabled,
)

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
    """Read mentions from Ferro's owned persistent Chrome runtime."""

    def __init__(
        self, *, state_root: Path, timeout_ms: int = 30_000, browser_factory=None
    ) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.browser_factory = browser_factory
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "LocalHarnessXMentionsObserver":
        if os.environ.get("CONTENT_LOCAL_X_BROWSER_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise BrowserRuntimeUnavailable("CONTENT_LOCAL_X_BROWSER_ENABLED is false")
        if importlib.util.find_spec("jev_ultrafast") is None:
            raise BrowserRuntimeUnavailable("jev-ultrafast is not installed")
        if importlib.util.find_spec("browser_harness") is None:
            raise BrowserRuntimeUnavailable("browser-harness is not installed")
        return cls(
            state_root=state_root,
            timeout_ms=int(os.environ.get("CONTENT_LOCAL_X_BROWSER_TIMEOUT_MS", "30000")),
        )

    def _factory(self):
        if self.browser_factory is not None:
            return self.browser_factory
        try:
            from jev_ultrafast.browser import Browser
        except ImportError as exc:
            raise BrowserRuntimeUnavailable("local Browser Harness runtime is not installed") from exc
        return Browser

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
                    provider="LOCAL_CDP",
                    status="FAILED",
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    action_count=0,
                    failure_reason=type(exc).__name__,
                ),
            )
            raise
        self.telemetry.append(
            task_type="X_MENTIONS_READ",
            site="x.com",
            result=BrowserTaskResult(
                provider="LOCAL_CDP",
                status="COMPLETED",
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                action_count=0,
                final_url=X_MENTIONS_URL,
            ),
        )
        return rows

    def _wait_selector(self, browser: Any, selector: str) -> bool:
        deadline = time.monotonic() + min(15.0, max(2.0, self.timeout_ms / 1000))
        expression = "(() => Boolean(document.querySelector(" + repr(selector) + ")))()"
        while time.monotonic() < deadline:
            try:
                if browser.evaluate(expression):
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    def _observe_sync(self, max_results: int) -> list[dict[str, Any]]:
        browser = self._factory()(X_MENTIONS_URL)
        try:
            href = str(browser.evaluate(
                """(() => document.querySelector('[data-testid="AppTabBar_Profile_Link"]')?.getAttribute('href') || '')()"""
            ) or "")
            own_handle = href.strip("/").split("/", 1)[0]
            if not own_handle:
                raise BrowserRuntimeUnavailable("local X browser profile is not authenticated")
            if not self._wait_selector(browser, "article"):
                return []
            limit = max(1, min(int(max_results), 50))
            raw_rows = browser.evaluate(
                """(() => [...document.querySelectorAll('article')].slice(0,50).map(a => ({
                  text:a.innerText || '',
                  hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(x => x.getAttribute('href') || '')
                })))()"""
            )
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw in (raw_rows if isinstance(raw_rows, list) else [])[:limit]:
                for href in (raw or {}).get("hrefs") or []:
                    row = browser_mention_post(
                        text=str((raw or {}).get("text") or ""),
                        href=str(href),
                        own_handle=own_handle,
                    )
                    if row is not None and row["id"] not in seen:
                        seen.add(row["id"])
                        rows.append(row)
                        break
            return rows
        finally:
            try:
                browser.close()
            except Exception:
                pass


class TinyFishXMentionsObserver:
    """Read mentions without granting any browser write authority."""

    def __init__(self, *, client: TinyFishProfileClient, timeout_ms: int = 30_000) -> None:
        self.client = client
        self.timeout_ms = int(timeout_ms)

    @classmethod
    def from_environment(cls) -> "TinyFishXMentionsObserver":
        return cls(
            client=TinyFishProfileClient(
                api_key=os.environ.get("TINYFISH_API_KEY", ""),
                profile_id=os.environ.get("TINYFISH_X_PROFILE_ID", ""),
                base_url=os.environ.get("TINYFISH_BASE_URL", TINYFISH_BASE_URL),
            ),
            timeout_ms=int(os.environ.get("CONTENT_TINYFISH_X_TIMEOUT_MS", "30000")),
        )
    async def observe(self, *, now: datetime, max_results: int = 20) -> list[dict[str, Any]]:
        del now  # Browser rows are normalized with the caller's observation time.
        setup = await asyncio.to_thread(self.client.start_setup, url=X_MENTIONS_URL, timeout_seconds=180)
        session_id = str(setup.get("session_id") or "")
        cdp_url = str(setup.get("cdp_url") or "")
        if not session_id or not cdp_url:
            raise TinyFishProfileUnavailable("TinyFish mentions session did not return session_id/cdp_url")
        try:
            return await self._observe_in_session(cdp_url=cdp_url, max_results=max_results)
        finally:
            try:
                await asyncio.to_thread(self.client.cancel, session_id=session_id)
            except Exception:
                pass

    async def _observe_in_session(self, *, cdp_url: str, max_results: int) -> list[dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is required for TinyFish X mention reads") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            page = browser.contexts[0].pages[0]
            page.set_default_timeout(self.timeout_ms)
            await page.goto(X_MENTIONS_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            profile_link = page.locator('[data-testid="AppTabBar_Profile_Link"]').first
            if not await profile_link.count():
                raise TinyFishProfileUnavailable("TinyFish X profile is not authenticated")
            href = str(await profile_link.get_attribute("href") or "")
            own_handle = href.strip("/").split("/", 1)[0]
            if not own_handle:
                raise TinyFishProfileUnavailable("Could not resolve authenticated X handle")
            articles = page.locator("article")
            try:
                await articles.first.wait_for(state="visible", timeout=min(self.timeout_ms, 15_000))
            except Exception:
                return []
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            limit = max(1, min(int(max_results), 50))
            for index in range(min(await articles.count(), limit)):
                article = articles.nth(index)
                body = _normalize_text(await article.inner_text())
                links = article.locator('a[href*="/status/"]')
                for link_index in range(await links.count()):
                    link_href = str(await links.nth(link_index).get_attribute("href") or "")
                    row = browser_mention_post(text=body, href=link_href, own_handle=own_handle)
                    if row is not None and row["id"] not in seen:
                        seen.add(row["id"])
                        rows.append(row)
                        break
            return rows


class XBrowserMentionsObserverRouter:
    """Prefer the authenticated TinyFish profile; keep local Chrome as read-only fallback."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)

    async def observe(self, *, now: datetime, max_results: int = 20) -> list[dict[str, Any]]:
        errors: list[str] = []
        if tinyfish_x_enabled():
            try:
                return await TinyFishXMentionsObserver.from_environment().observe(
                    now=now, max_results=max_results
                )
            except Exception as exc:
                errors.append("TINYFISH:" + type(exc).__name__)

        if os.environ.get("CONTENT_LOCAL_X_BROWSER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                local = LocalHarnessXMentionsObserver.from_environment(state_root=self.state_root)
                return await local.observe(now=now, max_results=max_results)
            except Exception as exc:
                errors.append("LOCAL_CDP:" + type(exc).__name__)

        raise XBrowserSessionUnavailable(
            "; ".join(errors) or "no read-only X browser provider is configured"
        )


__all__ = [
    "LocalHarnessXMentionsObserver",
    "TinyFishXMentionsObserver",
    "XBrowserMentionsObserverRouter",
    "browser_mention_post",
]