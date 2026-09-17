"""Read-only X mention discovery through TinyFish Browser Context Profiles."""
from __future__ import annotations

import asyncio
from datetime import datetime
import os
import re
from typing import Any

from .x_browser import TINYFISH_BASE_URL, TinyFishProfileClient, TinyFishProfileUnavailable

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


__all__ = ["TinyFishXMentionsObserver", "browser_mention_post"]