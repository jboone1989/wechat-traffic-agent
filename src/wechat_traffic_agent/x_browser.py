"""Provider-routed X Web publishing with independently verified outcomes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol
import urllib.error
import urllib.request

from .public_activity import emit_public_activity
from .x_social import XHistoryLedger

TINYFISH_BASE_URL = "https://agent.tinyfish.ai"
X_HOME_URL = "https://x.com/home"
X_COMPOSE_URL = "https://x.com/compose/post"
X_PREMIUM_LONG_POST_MAX_CHARS = 25_000


class XBrowserSessionUnavailable(RuntimeError):
    """No browser transport can safely start the requested X session."""


class TinyFishProfileUnavailable(XBrowserSessionUnavailable):
    """TinyFish credentials/profile are absent or cannot open an authenticated session."""


class XBrowserPublicationUnverified(RuntimeError):
    """A browser write could not be tied to a public X post URL."""


@dataclass(frozen=True)
class XBrowserPublishResult:
    post_id: str
    url: str
    handle: str
    duplicate_found: bool = False
    provider: str = "UNKNOWN"


class XBrowserTransport(Protocol):
    async def publish(self, *, text: str) -> XBrowserPublishResult: ...


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def tinyfish_x_enabled() -> bool:
    return _enabled("CONTENT_TINYFISH_X_ENABLED")


def tinyfish_x_writes_enabled() -> bool:
    return _enabled("CONTENT_TINYFISH_X_WRITES_ENABLED")


def x_browser_enabled() -> bool:
    return (
        _enabled("CONTENT_X_BROWSER_ENABLED")
        or _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED")
        or tinyfish_x_enabled()
    )


def x_browser_writes_enabled() -> bool:
    return (
        _enabled("CONTENT_X_BROWSER_WRITES_ENABLED")
        or tinyfish_x_writes_enabled()
    )


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


def _draft_sha256(*, cycle: str, draft: dict[str, Any]) -> str:
    raw = json.dumps({"cycle": cycle, "draft": draft}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _post_id_from_url(url: str) -> str | None:
    match = re.search(r"https://x\.com/[^/?#]+/status/(\d+)(?:[/?#]|$)", url)
    return match.group(1) if match else None


class TinyFishProfileClient:
    def __init__(self, *, api_key: str, profile_id: str, base_url: str = TINYFISH_BASE_URL) -> None:
        if not api_key.strip() or not profile_id.strip():
            raise TinyFishProfileUnavailable("TINYFISH_API_KEY and TINYFISH_X_PROFILE_ID are required")
        self.api_key = api_key.strip()
        self.profile_id = profile_id.strip()
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: int = 60) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise TinyFishProfileUnavailable(f"TinyFish HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise TinyFishProfileUnavailable(f"TinyFish request failed: {type(exc).__name__}") from exc
        value = json.loads(raw) if raw else {}
        if not isinstance(value, dict):
            raise TinyFishProfileUnavailable("TinyFish returned a non-object response")
        return value

    def start_setup(self, *, url: str, timeout_seconds: int = 300) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/profiles/{self.profile_id}/setup-session",
            {"url": url, "timeout_seconds": timeout_seconds}, timeout=60,
        )

    def save(self, *, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/profiles/{self.profile_id}/save", {"session_id": session_id}, timeout=60)

    def cancel(self, *, session_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/profiles/{self.profile_id}/setup-session/cancel",
            {"session_id": session_id}, timeout=60,
        )


class TinyFishXTransport:
    """Deterministic X composer automation over TinyFish's profile CDP session."""

    def __init__(self, *, client: TinyFishProfileClient, timeout_ms: int = 30_000) -> None:
        self.client = client
        self.timeout_ms = int(timeout_ms)

    @classmethod
    def from_environment(cls) -> "TinyFishXTransport":
        return cls(
            client=TinyFishProfileClient(
                api_key=os.environ.get("TINYFISH_API_KEY", ""),
                profile_id=os.environ.get("TINYFISH_X_PROFILE_ID", ""),
                base_url=os.environ.get("TINYFISH_BASE_URL", TINYFISH_BASE_URL),
            ),
            timeout_ms=int(os.environ.get("CONTENT_TINYFISH_X_TIMEOUT_MS", "30000")),
        )

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        if not text.strip() or len(text) > X_PREMIUM_LONG_POST_MAX_CHARS:
            raise ValueError("X post must contain 1-25000 characters")
        setup = await asyncio.to_thread(self.client.start_setup, url=X_HOME_URL, timeout_seconds=300)
        session_id = str(setup.get("session_id") or "")
        cdp_url = str(setup.get("cdp_url") or "")
        if not session_id or not cdp_url:
            raise TinyFishProfileUnavailable("TinyFish setup session did not return session_id/cdp_url")
        action_state = {"attempted": False}
        success = False
        try:
            result = await self._publish_in_session(cdp_url=cdp_url, text=text, action_state=action_state)
            success = True
            return result
        finally:
            # Save after a successful reconciliation/write so any auth rotation survives.
            # If a click may already have reached X, save too; a retry reconciles before writing.
            if success or action_state["attempted"]:
                try:
                    await asyncio.to_thread(self.client.save, session_id=session_id)
                except Exception:
                    pass
            else:
                try:
                    await asyncio.to_thread(self.client.cancel, session_id=session_id)
                except Exception:
                    pass

    async def _publish_in_session(self, *, cdp_url: str, text: str, action_state: dict[str, bool]) -> XBrowserPublishResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is required for TinyFish X publishing") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0]
            page = context.pages[0]
            page.set_default_timeout(self.timeout_ms)
            await page.goto(X_HOME_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            handle = await self._authenticated_handle(page)
            existing = await self._find_existing(page, handle=handle, text=text, duplicate_found=True)
            if existing is not None:
                return existing
            await page.goto(X_COMPOSE_URL, wait_until="domcontentloaded")
            editor = page.locator('[data-testid="tweetTextarea_0"]').first
            await editor.wait_for(state="visible")
            await editor.fill(text)
            rendered = _normalize_text(await editor.inner_text())
            if rendered != _normalize_text(text):
                raise XBrowserPublicationUnverified("X composer did not preserve the persisted draft text")
            button = page.locator('[data-testid="tweetButton"]').first
            await button.wait_for(state="visible")
            if not await button.is_enabled():
                raise XBrowserPublicationUnverified("X composer publish button is disabled")
            action_state["attempted"] = True
            await button.click()
            for _ in range(8):
                await page.wait_for_timeout(900)
                existing = await self._find_existing(
                    page, handle=handle, text=text, duplicate_found=False, require_timeline=False
                )
                if existing is not None:
                    return existing
            raise XBrowserPublicationUnverified("X submit could not be tied to a public post URL")

    async def _authenticated_handle(self, page: Any) -> str:
        link = page.locator('[data-testid="AppTabBar_Profile_Link"]').first
        if not await link.count():
            raise TinyFishProfileUnavailable("TinyFish X profile is not authenticated")
        href = str(await link.get_attribute("href") or "")
        handle = href.strip("/").split("/", 1)[0]
        if not handle or handle in {"home", "i"}:
            raise TinyFishProfileUnavailable("Could not resolve authenticated X handle")
        return handle

    async def _find_existing(self, page: Any, *, handle: str, text: str, duplicate_found: bool,
                             require_timeline: bool = True) -> XBrowserPublishResult | None:
        await page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded")
        articles = page.locator("article")
        try:
            await articles.first.wait_for(state="visible", timeout=min(self.timeout_ms, 15_000))
        except Exception as exc:
            if require_timeline:
                raise XBrowserPublicationUnverified(
                    "X profile timeline did not load; refusing to publish without an idempotency check"
                ) from exc
            return None
        await page.wait_for_timeout(500)
        needle = _normalize_text(text)[:160]
        for index in range(min(await articles.count(), 15)):
            article = articles.nth(index)
            body = _normalize_text(await article.inner_text())
            if not needle or needle not in body:
                continue
            links = article.locator(f'a[href^="/{handle}/status/"]')
            for link_index in range(await links.count()):
                href = str(await links.nth(link_index).get_attribute("href") or "")
                raw_url = "https://x.com" + href if href.startswith("/") else href
                post_id = _post_id_from_url(raw_url)
                if post_id:
                    url = f"https://x.com/{handle}/status/{post_id}"
                    return XBrowserPublishResult(
                        post_id=post_id,
                        url=url,
                        handle=handle,
                        duplicate_found=duplicate_found,
                        provider="TINYFISH",
                    )
        return None


class XBrowserTransportRouter:
    """Prefer account-scoped JEV; retain TinyFish and legacy local CDP as safe-start fallbacks."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        errors: list[str] = []

        if _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED"):
            from .browser_runtime import BrowserRuntimeUnavailable
            from .x_local_browser import AccountJevXTransport

            try:
                account_jev = AccountJevXTransport.from_environment(
                    state_root=self.state_root
                )
                return await account_jev.publish(text=text)
            except BrowserRuntimeUnavailable as exc:
                # Safe fallback is allowed only before a write was attempted.
                # XBrowserPublicationUnverified deliberately propagates.
                errors.append("JEV_ACCOUNT: " + str(exc))

        if _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED") and tinyfish_x_enabled():
            from .browser_runtime import BrowserRuntimeUnavailable
            from .x_local_browser import ExistingProfileJevXTransport

            try:
                existing_jev = ExistingProfileJevXTransport.from_environment(
                    state_root=self.state_root
                )
                return await existing_jev.publish(text=text)
            except BrowserRuntimeUnavailable as exc:
                # This path reuses an authenticated remote profile but executes
                # all page actions through JEV. Fallback is safe only pre-write.
                errors.append("JEV_EXISTING_PROFILE: " + str(exc))

        if tinyfish_x_enabled():
            try:
                return await TinyFishXTransport.from_environment().publish(text=text)
            except TinyFishProfileUnavailable as exc:
                # Safe to try another provider only when TinyFish failed before a write
                # could have become ambiguous. XBrowserPublicationUnverified propagates.
                errors.append("TINYFISH: " + str(exc))

        if _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED"):
            from .browser_runtime import BrowserRuntimeUnavailable
            from .x_local_browser import LocalHarnessXTransport

            try:
                local = LocalHarnessXTransport.from_environment(state_root=self.state_root)
                return await local.publish(text=text)
            except BrowserRuntimeUnavailable as exc:
                errors.append("LOCAL_CDP: " + str(exc))

        detail = "; ".join(errors) or "no X browser provider is configured"
        raise XBrowserSessionUnavailable(detail)


class XBrowserPublisher:
    def __init__(
        self,
        *,
        state_root: Path,
        transport: XBrowserTransport | None = None,
        enabled: bool | None = None,
        writes_enabled: bool | None = None,
        growth_mission: Any | None = None,
        mission_baseline_observer: Any | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.transport = transport
        self.enabled = x_browser_enabled() if enabled is None else bool(enabled)
        self.writes_enabled = x_browser_writes_enabled() if writes_enabled is None else bool(writes_enabled)
        self.growth_mission = growth_mission
        self.mission_baseline_observer = mission_baseline_observer

    def _folder(self, cycle: str) -> Path:
        return self.state_root / "content" / "continuous" / "cycles" / cycle

    def _start_mission_experiment(
        self,
        *,
        cycle: str,
        post_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        if self.growth_mission is None or not str(post_id).strip():
            return None
        receipt_id = f"x-browser-publication:{cycle}:{str(post_id).strip()}"
        try:
            return self.growth_mission.start_experiment_from_publication(
                cycle=cycle,
                publication_receipt_id=receipt_id,
                post_id=str(post_id).strip(),
                now=now,
            )
        except Exception as exc:
            return {
                "status": "DEGRADED",
                "error_type": type(exc).__name__,
                "message": str(exc)[:300],
            }

    async def after_content_cycle(self, *, cycle: str, now: datetime) -> dict[str, Any]:
        if not cycle:
            return {"status": "NO_CYCLE"}
        if not self.enabled:
            return {"status": "DISABLED", "cycle": cycle}
        if not self.writes_enabled:
            return {"status": "WRITE_DISABLED", "cycle": cycle}
        folder = self._folder(cycle)
        payload = _read_json(folder / "social_expression_drafts.json")
        if payload.get("status") != "DRAFT_READY":
            return {"status": "NO_READY_DRAFT", "cycle": cycle}
        strategy = (
            payload.get("x_strategy")
            if isinstance(payload.get("x_strategy"), dict)
            else {}
        )
        if not strategy:
            return {
                "status": "L1_X_STRATEGY_MISSING",
                "cycle": cycle,
            }
        if (
            str(strategy.get("post_timing") or "") == "HOLD"
            or str(strategy.get("interaction_mode") or "ORIGINAL") != "ORIGINAL"
        ):
            return {
                "status": "SKIPPED",
                "cycle": cycle,
                "reason": "L1_X_STRATEGY",
                "x_strategy": strategy,
            }
        drafts = payload.get("drafts") if isinstance(payload.get("drafts"), dict) else {}
        draft = drafts.get("x") if isinstance(drafts.get("x"), dict) else {}
        if draft.get("channel") != "X":
            return {"status": "NO_X_DRAFT", "cycle": cycle}
        fmt = str(draft.get("format") or "")
        if fmt not in {"SHORT_POST", "LONG_POST"}:
            return {"status": "UNSUPPORTED_FORMAT", "cycle": cycle, "format": fmt}
        text = str(draft.get("text") or "")
        if not text.strip() or len(text) > X_PREMIUM_LONG_POST_MAX_CHARS:
            return {"status": "INVALID_DRAFT", "cycle": cycle}
        receipt_path = folder / "x_browser_publication.json"
        digest = _draft_sha256(cycle=cycle, draft=draft)
        existing = _read_json(receipt_path)
        if existing.get("status") == "POSTED":
            if existing.get("draft_sha256") != digest:
                return {"status": "RECEIPT_DRAFT_MISMATCH", "cycle": cycle}
            transition = self._start_mission_experiment(
                cycle=cycle,
                post_id=str(existing.get("post_id") or ""),
                now=now,
            )
            if transition is not None:
                existing["mission_experiment_transition"] = transition
                _write_json(receipt_path, existing)
            return {
                "status": "ALREADY_POSTED",
                "cycle": cycle,
                "publication": existing,
                "mission_experiment_transition": transition,
            }
        guardrail = getattr(self.growth_mission, "publication_guardrail", None)
        if callable(guardrail):
            guard = guardrail()
            if not bool(guard.get("allowed", True)):
                return {
                    "status": "HELD_BY_MISSION_GUARDRAIL",
                    "cycle": cycle,
                    "guardrail": guard,
                }
        binding = None
        binding_method = getattr(self.growth_mission, "publication_binding", None)
        if callable(binding_method):
            binding = binding_method(cycle=cycle)
        if isinstance(binding, dict) and bool(binding.get("bound")):
            observer = self.mission_baseline_observer
            if observer is None:
                return {
                    "status": "HELD_MISSION_BASELINE_UNAVAILABLE",
                    "cycle": cycle,
                    "binding": binding,
                }
            try:
                baseline = await observer(cycle=cycle, now=now)
            except Exception as exc:
                return {
                    "status": "HELD_MISSION_BASELINE_UNAVAILABLE",
                    "cycle": cycle,
                    "binding": binding,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            if str((baseline or {}).get("status") or "") != "RECORDED":
                return {
                    "status": "HELD_MISSION_BASELINE_UNAVAILABLE",
                    "cycle": cycle,
                    "binding": binding,
                    "baseline": baseline,
                }
        transport = self.transport
        if transport is None:
            transport = XBrowserTransportRouter(state_root=self.state_root)
        emit_public_activity(self.state_root, cycle=cycle, phase="X", title="Publishing on X",
                             detail=text[:220], status="working", kind="social")
        try:
            published = await transport.publish(text=text)
        except XBrowserSessionUnavailable as exc:
            return {"status": "SESSION_UNAVAILABLE", "cycle": cycle, "detail": str(exc)}
        except XBrowserPublicationUnverified as exc:
            return {"status": "PUBLICATION_UNVERIFIED", "cycle": cycle, "detail": str(exc)}
        publication = {
            "schema_version": "content-x-browser-publication.v1", "status": "POSTED",
            "cycle": cycle, "published_at": now.isoformat(), "draft_sha256": digest,
            "post_id": published.post_id, "url": published.url, "handle": published.handle,
            "duplicate_found": published.duplicate_found,
            "source": f"{published.provider} + X Web UI", "publication_authority": True,
        }
        _write_json(receipt_path, publication)
        mission_experiment_transition = self._start_mission_experiment(
            cycle=cycle,
            post_id=published.post_id,
            now=now,
        )
        if mission_experiment_transition is not None:
            publication["mission_experiment_transition"] = mission_experiment_transition
            _write_json(receipt_path, publication)
        history = XHistoryLedger(self.state_root)
        if not history.has_event(cycle=cycle, event_type="EXPRESSION"):
            cognition = _read_json(folder / "cognition.json")
            worldview = _read_json(folder / "worldview_update.json")
            prediction = _read_json(folder / "prediction.json")
            plan = _read_json(folder / "mind_plan.json")
            history.append({
                "event_type": "EXPRESSION", "cycle": cycle,
                "cognition": cognition, "expression_kind": cognition.get("expression_kind") or fmt,
                "text": text, "x_post_id": published.post_id, "x_url": published.url,
                "runtime_receipt_id": None, "worldview": worldview, "prediction": prediction,
                "inquiry_refs": list((plan.get("goal") or {}).get("inquiry_refs") or ()) if isinstance(plan.get("goal"), dict) else [],
                "source_evidence_refs": [],
                "lineage": {
                    "cycle": cycle,
                    "source": "social_expression_drafts.json",
                    "browser_publication_receipt": "x_browser_publication.json",
                    "mission_experiment_transition": mission_experiment_transition,
                    "future_cognition": True,
                },
            })
        emit_public_activity(self.state_root, cycle=cycle, phase="X", title="Posted on X",
                             detail=f"Published the persisted X draft as {published.post_id}.",
                             status="completed", kind="social")
        return {
            "status": "POSTED",
            "cycle": cycle,
            "publication": publication,
            "mission_experiment_transition": mission_experiment_transition,
        }


__all__ = [
    "TinyFishProfileClient", "TinyFishProfileUnavailable", "TinyFishXTransport",
    "XBrowserPublicationUnverified", "XBrowserPublishResult", "XBrowserPublisher",
    "XBrowserSessionUnavailable", "XBrowserTransportRouter",
    "tinyfish_x_enabled", "tinyfish_x_writes_enabled", "x_browser_enabled",
    "x_browser_writes_enabled", "_post_id_from_url",
]
