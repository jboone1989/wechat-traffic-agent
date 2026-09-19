"""JEV publisher that reuses an already-authenticated remote X browser context."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp
from jev_ultrafast.browser import Browser

from .x_browser import _normalize_text, _post_id_from_url


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _wait_selector(browser, selector: str, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    expr = "Boolean(document.querySelector(" + repr(selector) + "))"
    while time.monotonic() < deadline:
        try:
            if browser.evaluate(expr):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False
def _context_id() -> str:
    targets = cdp("Target.getTargets").get("targetInfos", [])
    for target in targets:
        if (
            target.get("type") == "page"
            and "x.com" in str(target.get("url") or "")
            and target.get("browserContextId")
        ):
            return str(target["browserContextId"])
    return ""


class ContextBrowser(Browser):
    def __init__(self, url: str, *, context_id: str) -> None:
        self.target = cdp(
            "Target.createTarget",
            url="about:blank",
            background=True,
            browserContextId=context_id,
        )["targetId"]
        self.session = cdp(
            "Target.attachToTarget",
            targetId=self.target,
            flatten=True,
        )["sessionId"]
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=1120,
            height=780,
            deviceScaleFactor=1,
            mobile=False,
        )
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    return
            except Exception:
                pass
            time.sleep(0.1)


def _authenticated_handle(browser, expected_handle: str) -> str:
    deadline = time.monotonic() + 20
    href = ""
    while time.monotonic() < deadline:
        try:
            href = str(
                browser.evaluate(
                    "(() => document.querySelector("
                    "'[data-testid=\"AppTabBar_Profile_Link\"]'"
                    ")?.getAttribute('href') || '')()"
                )
                or ""
            )
            if href:
                break
        except Exception:
            pass
        time.sleep(0.2)
    handle = href.strip("/").split("/", 1)[0]
    if not handle or handle in {"home", "i"}:
        raise RuntimeError("existing X browser context is not authenticated")
    if expected_handle and handle.lower() != expected_handle.lower():
        raise RuntimeError(
            f"existing X browser context authenticated as @{handle}, "
            f"expected @{expected_handle}"
        )
    return handle
def _find_existing(browser, *, handle: str, text: str, duplicate: bool):
    browser.call("Page.navigate", url=f"https://x.com/{handle}")
    if not _wait_selector(browser, "article", 20):
        return None
    rows = browser.evaluate(
        """(() => [...document.querySelectorAll('article')].slice(0,20).map(a => ({
          text:a.innerText || '',
          hrefs:[...a.querySelectorAll('a[href*="/status/"]')]
            .map(x=>x.getAttribute('href')||'')
        })))()"""
    )
    needle = _normalize_text(text)[:160]
    for row in rows if isinstance(rows, list) else []:
        if not needle or needle not in _normalize_text(str((row or {}).get("text") or "")):
            continue
        for href in (row or {}).get("hrefs") or []:
            raw = "https://x.com" + href if str(href).startswith("/") else str(href)
            post_id = _post_id_from_url(raw)
            if post_id:
                return {
                    "post_id": post_id,
                    "url": f"https://x.com/{handle}/status/{post_id}",
                    "handle": handle,
                    "duplicate_found": duplicate,
                }
    return None


def _observe_action(page: dict, *, kind: str, labels: set[str] | None = None):
    for action in page.get("actions", []):
        if action.get("kind") != kind:
            continue
        if labels is None or str(action.get("label") or "").strip() in labels:
            return action
    return None
def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _emit({"status": "INVALID_DRAFT", "message": "invalid request"})
        return 2

    text = str(request.get("text") or "")
    expected_handle = str(request.get("expected_handle") or "").strip().lstrip("@")
    mode = str(request.get("mode") or "publish")
    started = time.perf_counter()
    attempted = False
    browser = None

    if not text.strip() or len(text) > 25_000:
        _emit({"status": "INVALID_DRAFT", "message": "X post must contain 1-25000 characters"})
        return 2

    try:
        ensure_daemon()
        context_id = _context_id()
        if not context_id:
            raise RuntimeError("authenticated X browser context not found")
        browser = ContextBrowser("https://x.com/home", context_id=context_id)
        handle = _authenticated_handle(browser, expected_handle)
        existing = _find_existing(
            browser,
            handle=handle,
            text=text,
            duplicate=(mode == "publish"),
        )
        if existing is not None:
            _emit({
                "status": "POSTED",
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "action_count": 0,
                **existing,
            })
            return 0
        if mode == "reconcile":
            _emit({
                "status": "NOT_FOUND",
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "action_count": 0,
            })
            return 1

        browser.call("Page.navigate", url="https://x.com/compose/post")
        if not _wait_selector(browser, '[data-testid="tweetTextarea_0"]', 20):
            raise RuntimeError("X composer editor did not mount")

        page = browser.observe(screenshot=False)
        fill = next(
            (
                action for action in page.get("actions", [])
                if action.get("kind") == "fill"
                and (
                    action.get("role") == "textbox"
                    or "帖子文本" in str(action.get("label") or "")
                )
            ),
            None,
        )
        if fill is None:
            raise RuntimeError("JEV did not observe composer textbox")
        browser.act(fill, page, text=text)
        settled = browser.observe(screenshot=False)

        rendered = str(
            browser.evaluate(
                "(() => document.querySelector("
                "'[data-testid=\"tweetTextarea_0\"]'"
                ")?.innerText || '')()"
            )
            or ""
        )
        if _normalize_text(rendered) != _normalize_text(text):
            raise RuntimeError("JEV fill verification mismatch")
        post = _observe_action(
            settled,
            kind="click",
            labels={"发帖", "Post"},
        )
        if post is None:
            settled = browser.observe(screenshot=False)
            post = _observe_action(
                settled,
                kind="click",
                labels={"发帖", "Post"},
            )
        if post is None:
            raise RuntimeError("JEV did not observe enabled publish button")

        attempted = True
        browser.act(post, settled)
        time.sleep(1.0)

        for _ in range(8):
            existing = _find_existing(
                browser,
                handle=handle,
                text=text,
                duplicate=False,
            )
            if existing is not None:
                _emit({
                    "status": "POSTED",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "action_count": 1,
                    **existing,
                })
                return 0
            time.sleep(0.8)

        _emit({
            "status": "PUBLICATION_UNVERIFIED",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 1,
            "attempted": True,
            "message": "post click occurred but public status URL was not verified",
        })
        return 1
    except Exception as exc:
        _emit({
            "status": "PUBLICATION_UNVERIFIED" if attempted else "SESSION_UNAVAILABLE",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "action_count": 1 if attempted else 0,
            "attempted": attempted,
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        })
        return 1
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
