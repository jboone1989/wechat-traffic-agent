"""Safe OKX Orbit publication from persisted social-expression drafts.

Orbit has no public posting API. This module intentionally automates only the
normal authenticated Web UI and never calls private/internal OKX endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from .public_activity import emit_public_activity

OKX_ORBIT_DEFAULT_URL = "https://www.okx.com/orbit"
OKX_ORBIT_MAX_SHORT_POST_CHARS = 500


class OrbitSessionUnavailable(RuntimeError):
    """The configured browser session is missing or no longer authenticated."""


class OrbitPublicationUnverified(RuntimeError):
    """The UI action finished but no public post identity could be verified."""


@dataclass(frozen=True)
class OrbitPublishResult:
    post_id: str
    url: str
    moderation_status: str = "PENDING_OR_UNKNOWN"


class OrbitPublisherTransport(Protocol):
    async def publish_short_post(self, *, text: str) -> OrbitPublishResult: ...


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def okx_orbit_enabled() -> bool:
    return _enabled("CONTENT_OKX_ORBIT_ENABLED")


def okx_orbit_writes_enabled() -> bool:
    return _enabled("CONTENT_OKX_ORBIT_WRITES_ENABLED")


def _draft_sha256(*, cycle: str, draft: dict[str, Any]) -> str:
    payload = json.dumps({"cycle": cycle, "draft": draft}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _post_id_from_url(url: str) -> str | None:
    match = re.search(r"/orbit/insight/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)", url)
    return match.group(1) if match else None


class PlaywrightOrbitTransport:
    """Publish through the normal OKX Orbit Web UI using a saved login session.

    Browser state is supplied by path and is never persisted into the repository.
    The transport verifies success only when OKX exposes a public /orbit/insight/
    URL after the UI submit. Anything less is fail-closed.
    """

    def __init__(self, *, storage_state: Path, base_url: str = OKX_ORBIT_DEFAULT_URL,
                 headless: bool = True, timeout_ms: int = 30_000) -> None:
        self.storage_state = Path(storage_state)
        self.base_url = base_url.rstrip("/")
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)

    @classmethod
    def from_environment(cls) -> "PlaywrightOrbitTransport":
        raw = os.environ.get("CONTENT_OKX_ORBIT_STORAGE_STATE", "").strip()
        if not raw:
            raise OrbitSessionUnavailable("CONTENT_OKX_ORBIT_STORAGE_STATE is not configured")
        return cls(
            storage_state=Path(raw).expanduser().resolve(),
            base_url=os.environ.get("CONTENT_OKX_ORBIT_BASE_URL", OKX_ORBIT_DEFAULT_URL),
            headless=not _enabled("CONTENT_OKX_ORBIT_HEADED"),
            timeout_ms=int(os.environ.get("CONTENT_OKX_ORBIT_TIMEOUT_MS", "30000")),
        )

    async def publish_short_post(self, *, text: str) -> OrbitPublishResult:
        if not self.storage_state.is_file():
            raise OrbitSessionUnavailable(f"Orbit storage state not found: {self.storage_state}")
        if not text.strip() or len(text) > OKX_ORBIT_MAX_SHORT_POST_CHARS:
            raise ValueError("Orbit short post must contain 1-500 characters")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - deployment dependency boundary
            raise RuntimeError("Playwright is required for OKX Orbit Web publishing") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(storage_state=str(self.storage_state))
            page = await context.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                await page.goto(self.base_url, wait_until="domcontentloaded")
                await self._assert_authenticated(page)
                await self._open_composer(page)
                editor = await self._find_editor(page)
                await editor.fill(text)
                await self._submit(page)
                url = await self._verified_post_url(page, text=text)
                post_id = _post_id_from_url(url)
                if not post_id:
                    raise OrbitPublicationUnverified("Orbit post URL did not expose a post id")
                return OrbitPublishResult(post_id=post_id, url=url)
            finally:
                await context.close()
                await browser.close()

    async def _assert_authenticated(self, page: Any) -> None:
        body = (await page.locator("body").inner_text()).lower()
        login_markers = ("log in", "sign in", "登录", "登入")
        post_markers = ("post", "publish", "发布", "發佈")
        if any(marker in body for marker in login_markers) and not any(marker in body for marker in post_markers):
            raise OrbitSessionUnavailable("OKX Orbit browser session is not authenticated")

    async def _open_composer(self, page: Any) -> None:
        patterns = (r"^post$", r"^create post$", r"^publish$", r"^发布$", r"^發佈$")
        for pattern in patterns:
            button = page.get_by_role("button", name=re.compile(pattern, re.I)).first
            if await button.count() and await button.is_visible():
                await button.click()
                return
        # Some Orbit builds expose the composer directly on the feed.
        if await page.locator("textarea:visible, [contenteditable='true']:visible").count():
            return
        raise OrbitPublicationUnverified("Could not locate the normal Orbit post composer")

    async def _find_editor(self, page: Any) -> Any:
        candidates = (
            "textarea:visible",
            "[contenteditable='true'][role='textbox']:visible",
            "[contenteditable='true']:visible",
        )
        for selector in candidates:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                return locator
        raise OrbitPublicationUnverified("Could not locate the Orbit post editor")

    async def _submit(self, page: Any) -> None:
        patterns = (r"^publish$", r"^post$", r"^发布$", r"^發佈$")
        for pattern in patterns:
            buttons = page.get_by_role("button", name=re.compile(pattern, re.I))
            for index in range(await buttons.count()):
                button = buttons.nth(index)
                if await button.is_visible() and await button.is_enabled():
                    await button.click()
                    return
        raise OrbitPublicationUnverified("Could not locate an enabled Orbit publish button")

    async def _verified_post_url(self, page: Any, *, text: str) -> str:
        try:
            await page.wait_for_url(re.compile(r"/orbit/insight/"), timeout=min(self.timeout_ms, 12_000))
        except Exception:  # UI may remain on the feed after submit.
            pass
        if "/orbit/insight/" in page.url and _post_id_from_url(page.url):
            return page.url
        prefix = text[:48].strip()
        if prefix:
            post = page.locator("a[href*='/orbit/insight/']").filter(has_text=prefix).first
            if await post.count():
                href = await post.get_attribute("href")
                if href:
                    return href if href.startswith("https://") else "https://www.okx.com" + href
        raise OrbitPublicationUnverified("Orbit submit could not be tied to a public post URL")


class OKXOrbitPublisher:
    def __init__(self, *, state_root: Path, transport: OrbitPublisherTransport | None = None,
                 enabled: bool | None = None, writes_enabled: bool | None = None) -> None:
        self.state_root = Path(state_root)
        self.transport = transport
        self.enabled = okx_orbit_enabled() if enabled is None else bool(enabled)
        self.writes_enabled = okx_orbit_writes_enabled() if writes_enabled is None else bool(writes_enabled)

    def _cycle_folder(self, cycle: str) -> Path:
        return self.state_root / "content" / "continuous" / "cycles" / cycle

    async def after_content_cycle(self, *, cycle: str, now: datetime) -> dict[str, Any]:
        if not cycle:
            return {"status": "NO_CYCLE"}
        if not self.enabled:
            return {"status": "DISABLED", "cycle": cycle}
        if not self.writes_enabled:
            return {"status": "WRITE_DISABLED", "cycle": cycle}

        folder = self._cycle_folder(cycle)
        draft_payload = _read_json(folder / "social_expression_drafts.json")
        if draft_payload.get("status") != "DRAFT_READY":
            return {"status": "NO_READY_DRAFT", "cycle": cycle}
        drafts = draft_payload.get("drafts") if isinstance(draft_payload.get("drafts"), dict) else {}
        draft = drafts.get("okx") if isinstance(drafts.get("okx"), dict) else {}
        if draft.get("channel") != "OKX":
            return {"status": "NO_OKX_DRAFT", "cycle": cycle}
        if draft.get("format") != "SHORT_POST":
            return {"status": "UNSUPPORTED_FORMAT", "cycle": cycle, "format": draft.get("format")}
        text = str(draft.get("text") or "").strip()
        if not text or len(text) > OKX_ORBIT_MAX_SHORT_POST_CHARS:
            return {"status": "INVALID_DRAFT", "cycle": cycle}

        receipt_path = folder / "okx_orbit_publication.json"
        digest = _draft_sha256(cycle=cycle, draft=draft)
        existing = _read_json(receipt_path)
        if existing.get("status") == "POSTED":
            if existing.get("draft_sha256") != digest:
                return {"status": "RECEIPT_DRAFT_MISMATCH", "cycle": cycle}
            return {"status": "ALREADY_POSTED", "cycle": cycle, "publication": existing}

        transport = self.transport
        if transport is None:
            try:
                transport = PlaywrightOrbitTransport.from_environment()
            except OrbitSessionUnavailable as exc:
                return {"status": "SESSION_UNAVAILABLE", "cycle": cycle, "detail": str(exc)}

        emit_public_activity(
            self.state_root, cycle=cycle, phase="OKX",
            title="Publishing on OKX Orbit", detail=text[:220], status="working", kind="social",
        )
        try:
            published = await transport.publish_short_post(text=text)
        except OrbitSessionUnavailable as exc:
            emit_public_activity(
                self.state_root, cycle=cycle, phase="OKX", title="OKX publishing paused",
                detail="The saved Orbit Web session is missing or no longer authenticated.",
                status="stopped", kind="social",
            )
            return {"status": "SESSION_UNAVAILABLE", "cycle": cycle, "detail": str(exc)}
        except OrbitPublicationUnverified as exc:
            emit_public_activity(
                self.state_root, cycle=cycle, phase="OKX", title="OKX post not verified",
                detail="The Web UI did not expose a verifiable public post URL, so no publication receipt was recorded.",
                status="stopped", kind="social",
            )
            return {"status": "PUBLICATION_UNVERIFIED", "cycle": cycle, "detail": str(exc)}

        payload = {
            "schema_version": "content-okx-orbit-publication.v1",
            "status": "POSTED",
            "cycle": cycle,
            "published_at": now.isoformat(),
            "draft_sha256": digest,
            "post_id": published.post_id,
            "url": published.url,
            "moderation_status": published.moderation_status,
            "source": "OKX Orbit Web UI",
            "publication_authority": True,
        }
        _write_json(receipt_path, payload)
        emit_public_activity(
            self.state_root, cycle=cycle, phase="OKX", title="Posted on OKX Orbit",
            detail=f"Published the persisted OKX draft and recorded post {published.post_id}.",
            status="completed", kind="social",
        )
        return {"status": "POSTED", "cycle": cycle, "publication": payload}


__all__ = [
    "OKXOrbitPublisher", "OrbitPublishResult", "OrbitPublisherTransport",
    "OrbitPublicationUnverified", "OrbitSessionUnavailable", "PlaywrightOrbitTransport",
    "okx_orbit_enabled", "okx_orbit_writes_enabled",
]
