"""Deterministic X Web UI skill over Ferro's local Chrome Browser Harness."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from pathlib import Path
from typing import Any

from .browser_runtime import (
    BrowserRuntimeUnavailable,
    BrowserTaskResult,
    BrowserTelemetryLedger,
)
from .x_browser import (
    XBrowserPublicationUnverified,
    XBrowserPublishResult,
    X_COMPOSE_URL,
    X_HOME_URL,
    _normalize_text,
    _post_id_from_url,
)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class LocalHarnessXTransport:
    """Exact X publishing through the persistent local Chrome profile."""

    def __init__(
        self, *, state_root: Path, timeout_ms: int = 30_000, browser_factory=None
    ) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.browser_factory = browser_factory
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "LocalHarnessXTransport":
        if not _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED"):
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
            raise BrowserRuntimeUnavailable(
                "local Browser Harness runtime is not installed"
            ) from exc
        return Browser

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(self._publish_sync, text)
        except Exception as exc:
            self.telemetry.append(
                task_type="X_PUBLISH",
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
            task_type="X_PUBLISH",
            site="x.com",
            result=BrowserTaskResult(
                provider="LOCAL_CDP",
                status="COMPLETED",
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                action_count=1,
                final_url=result.url,
            ),
        )
        return result

    def _publish_sync(self, text: str) -> XBrowserPublishResult:
        if not text.strip() or len(text) > 25_000:
            raise ValueError("X post must contain 1-25000 characters")
        browser = self._factory()(X_HOME_URL)
        try:
            self._wait_ready(browser)
            handle = self._authenticated_handle(browser)
            existing = self._find_existing(
                browser, handle=handle, text=text, duplicate_found=True
            )
            if existing is not None:
                return existing

            self._navigate(browser, X_COMPOSE_URL)
            if not self._wait_selector(browser, '[data-testid="tweetTextarea_0"]'):
                raise BrowserRuntimeUnavailable("X composer editor is unavailable")
            self._fill_exact(browser, text)
            rendered = str(
                browser.evaluate(
                    """(() => document.querySelector('[data-testid="tweetTextarea_0"]')?.innerText || '')()"""
                )
                or ""
            )
            if _normalize_text(rendered) != _normalize_text(text):
                raise XBrowserPublicationUnverified(
                    "X composer did not preserve the persisted draft text"
                )

            target = browser.evaluate(
                """(() => {
                  const b=document.querySelector('[data-testid="tweetButton"]');
                  if(!b || b.disabled || b.getAttribute('aria-disabled')==='true') return null;
                  const r=b.getBoundingClientRect();
                  if(!r.width || !r.height) return null;
                  return {x:r.x+r.width/2,y:r.y+r.height/2};
                })()"""
            )
            if not isinstance(target, dict):
                raise XBrowserPublicationUnverified("X publish button is unavailable")
            self._click(browser, float(target["x"]), float(target["y"]))
            time.sleep(1.0)

            for _ in range(8):
                existing = self._find_existing(
                    browser,
                    handle=handle,
                    text=text,
                    duplicate_found=False,
                    require_timeline=False,
                )
                if existing is not None:
                    return existing
                time.sleep(0.7)
            raise XBrowserPublicationUnverified(
                "X submit could not be tied to a public post URL"
            )
        finally:
            try:
                browser.close()
            except Exception:
                pass

    def _navigate(self, browser: Any, url: str) -> None:
        browser.call("Page.navigate", url=url)
        self._wait_ready(browser)

    def _wait_ready(self, browser: Any) -> None:
        deadline = time.monotonic() + max(3.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            try:
                if browser.evaluate("document.readyState") in {"interactive", "complete"}:
                    time.sleep(0.25)
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise BrowserRuntimeUnavailable("local Chrome page did not become ready")

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

    @staticmethod
    def _authenticated_handle(browser: Any) -> str:
        href = str(
            browser.evaluate(
                """(() => document.querySelector('[data-testid="AppTabBar_Profile_Link"]')?.getAttribute('href') || '')()"""
            )
            or ""
        )
        handle = href.strip("/").split("/", 1)[0]
        if not handle or handle in {"home", "i"}:
            raise BrowserRuntimeUnavailable(
                "local X browser profile is not authenticated"
            )
        return handle

    def _find_existing(
        self,
        browser: Any,
        *,
        handle: str,
        text: str,
        duplicate_found: bool,
        require_timeline: bool = True,
    ) -> XBrowserPublishResult | None:
        self._navigate(browser, f"https://x.com/{handle}")
        if not self._wait_selector(browser, "article"):
            if require_timeline:
                raise XBrowserPublicationUnverified(
                    "X timeline did not load; refusing to publish without idempotency check"
                )
            return None

        rows = browser.evaluate(
            """(() => [...document.querySelectorAll('article')].slice(0,15).map(a => ({
              text:a.innerText || '',
              hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(x=>x.getAttribute('href')||'')
            })))()"""
        )
        needle = _normalize_text(text)[:160]
        for row in rows if isinstance(rows, list) else []:
            if needle not in _normalize_text(str((row or {}).get("text") or "")):
                continue
            for href in (row or {}).get("hrefs") or []:
                raw = "https://x.com" + href if str(href).startswith("/") else str(href)
                post_id = _post_id_from_url(raw)
                if post_id:
                    return XBrowserPublishResult(
                        post_id=post_id,
                        url=f"https://x.com/{handle}/status/{post_id}",
                        handle=handle,
                        duplicate_found=duplicate_found,
                        provider="LOCAL_CDP",
                    )
        return None

    @staticmethod
    def _fill_exact(browser: Any, text: str) -> None:
        focused = browser.evaluate(
            """(() => {
              const e=document.querySelector('[data-testid="tweetTextarea_0"]');
              if(!e) return false;
              e.focus();
              document.execCommand('selectAll',false,null);
              document.execCommand('delete',false,null);
              return true;
            })()"""
        )
        if not focused:
            raise BrowserRuntimeUnavailable("X editor disappeared before input")
        browser.call("Input.insertText", text=text)
        time.sleep(0.15)

    @staticmethod
    def _click(browser: Any, x: float, y: float) -> None:
        for event in ("mousePressed", "mouseReleased"):
            browser.call(
                "Input.dispatchMouseEvent",
                type=event,
                x=x,
                y=y,
                button="left",
                clickCount=1,
            )


__all__ = ["LocalHarnessXTransport"]
