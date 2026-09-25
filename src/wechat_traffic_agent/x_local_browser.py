"""Deterministic X Web UI skill over Ferro's local Chrome Browser Harness."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .browser_runtime import (
    BrowserRuntimeUnavailable,
    BrowserTaskResult,
    BrowserTelemetryLedger,
)
from .x_browser import (
    TINYFISH_BASE_URL,
    TinyFishProfileClient,
    TinyFishProfileUnavailable,
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
        self,
        *,
        state_root: Path,
        timeout_ms: int = 30_000,
        browser_factory=None,
        expected_handle: str = "",
    ) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.browser_factory = browser_factory
        self.expected_handle = str(expected_handle).strip().lstrip("@")
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

    def _publish_sync(
        self,
        text: str,
        *,
        action_state: dict[str, bool] | None = None,
    ) -> XBrowserPublishResult:
        if not text.strip() or len(text) > 25_000:
            raise ValueError("X post must contain 1-25000 characters")
        state = action_state if action_state is not None else {"attempted": False}
        state.setdefault("attempted", False)
        browser = self._factory()(X_HOME_URL)
        try:
            self._wait_ready(browser)
            handle = self._authenticated_handle(browser)
            if self.expected_handle and handle.lower() != self.expected_handle.lower():
                raise BrowserRuntimeUnavailable(
                    f"account JEV profile authenticated as @{handle}, expected @{self.expected_handle}"
                )
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
            # Mark ambiguous before sending the click sequence: a transport failure
            # between mousePressed and mouseReleased must never trigger a provider retry.
            state["attempted"] = True
            self._click(browser, float(target["x"]), float(target["y"]))
            time.sleep(1.0)

            try:
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
            except XBrowserPublicationUnverified:
                raise
            except Exception as exc:
                raise XBrowserPublicationUnverified(
                    "X write may have reached the platform but verification failed"
                ) from exc
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

    def _authenticated_handle(self, browser: Any) -> str:
        selector = '[data-testid="AppTabBar_Profile_Link"]'
        if not self._wait_selector(browser, selector):
            raise BrowserRuntimeUnavailable(
                "local X browser profile is not authenticated"
            )
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
        payload = json.dumps(text)
        inserted = browser.evaluate(
            """(() => {
              const e=document.querySelector('[data-testid="tweetTextarea_0"]');
              if(!e) return false;
              e.focus();
              document.execCommand('selectAll',false,null);
              document.execCommand('delete',false,null);
              return document.execCommand('insertText',false,"""
            + payload
            + """);
            })()"""
        )
        if not inserted:
            raise BrowserRuntimeUnavailable(
                "X editor rejected browser-context text insertion"
            )
        time.sleep(0.15)

    @staticmethod
    def _click(browser: Any, x: float, y: float) -> None:
        clicked = browser.evaluate(
            """(() => {
              const b=document.querySelector('[data-testid="tweetButton"]');
              if(!b || b.disabled || b.getAttribute('aria-disabled')==='true') return false;
              b.click();
              return true;
            })()"""
        )
        if not clicked:
            raise XBrowserPublicationUnverified(
                "X publish button became unavailable before click"
            )



class AccountJevXTransport:
    """Deterministic X publishing on one account-scoped JEV/Browser Harness profile."""

    def __init__(
        self,
        *,
        state_root: Path,
        registry=None,
        profile_manager=None,
        runner: Callable[..., Any] | None = None,
        timeout_seconds: float = 75.0,
    ) -> None:
        self.state_root = Path(state_root)
        if registry is None:
            from .account_registry import AccountRegistry
            registry = AccountRegistry(self.state_root)
        if profile_manager is None:
            from .account_browser_profiles import AccountBrowserProfileManager
            profile_manager = AccountBrowserProfileManager(self.state_root)
        self.registry = registry
        self.profile_manager = profile_manager
        self.runner = runner or subprocess.run
        self.timeout_seconds = float(timeout_seconds)
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "AccountJevXTransport":
        if not _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED"):
            raise BrowserRuntimeUnavailable("CONTENT_LOCAL_X_BROWSER_ENABLED is false")
        installed = importlib.util.find_spec("jev_ultrafast") is not None
        harness = importlib.util.find_spec("browser_harness") is not None
        if not installed or not harness:
            raise BrowserRuntimeUnavailable("jev-ultrafast/browser-harness is not installed")
        return cls(
            state_root=state_root,
            timeout_seconds=float(
                os.environ.get("CONTENT_LOCAL_X_BROWSER_TIMEOUT_SECONDS", "75")
            ),
        )

    def _account(self):
        explicit = os.environ.get("CONTENT_X_ACCOUNT_ID", "").strip()
        if explicit:
            try:
                record = self.registry.get(explicit)
            except (KeyError, ValueError) as exc:
                raise BrowserRuntimeUnavailable(
                    "configured X account instance does not exist"
                ) from exc

            if record.provider_id != "x":
                raise BrowserRuntimeUnavailable(
                    "configured account instance is not an X account"
                )
            return record
        rows = self.registry.list(provider_id="x")
        if not rows:
            raise BrowserRuntimeUnavailable(
                "no X account instance is registered for account-scoped JEV"
            )
        if len(rows) != 1:
            raise BrowserRuntimeUnavailable(
                "multiple X account instances are registered; "
                "CONTENT_X_ACCOUNT_ID is required"
            )
        return rows[0]

    @staticmethod
    def _worker_env(*, account_id: str, cdp_url: str) -> dict[str, str]:
        env = dict(os.environ)
        env["BU_CDP_URL"] = cdp_url
        env["BU_NAME"] = ("x_" + account_id[-24:])[:64]
        src_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = (

            src_root
            if not env.get("PYTHONPATH")
            else src_root + os.pathsep + env["PYTHONPATH"]
        )
        return env

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        return await asyncio.to_thread(self._publish_sync, text)

    def _publish_sync(self, text: str) -> XBrowserPublishResult:
        record = self._account()
        try:
            runtime = self.profile_manager.ensure(record.account_id)
        except Exception as exc:
            raise BrowserRuntimeUnavailable(
                f"account JEV browser could not start: {type(exc).__name__}"
            ) from exc
        started = time.perf_counter()
        try:
            completed = self.runner(
                [sys.executable, "-m", "wechat_traffic_agent.account_x_publish_worker"],
                input=json.dumps(
                    {
                        "state_root": str(self.state_root),
                        "text": text,

                        "expected_handle": record.identity,
                    }
                ),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._worker_env(
                    account_id=record.account_id,
                    cdp_url=runtime.cdp_url,
                ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            result = BrowserTaskResult(
                provider="JEV_ACCOUNT",
                status="FAILED",
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                action_count=0,
                failure_reason="WORKER_TIMEOUT",
            )
            self.telemetry.append(
                task_type="X_PUBLISH",
                site="x.com",
                result=result,
                account_id=record.account_id,
            )

            raise XBrowserPublicationUnverified(
                "account JEV publish worker timed out; refusing cross-provider retry"
            ) from exc

        output = completed.stdout.strip().splitlines()
        try:
            body = json.loads(output[-1]) if output else {}
        except json.JSONDecodeError:
            body = {}
        status = str(body.get("status") or "FAILED")
        elapsed_ms = int(
            body.get("elapsed_ms")
            or round((time.perf_counter() - started) * 1000)
        )
        result = BrowserTaskResult(
            provider="JEV_ACCOUNT",
            status="COMPLETED" if status == "POSTED" else "FAILED",
            elapsed_ms=elapsed_ms,
            action_count=int(body.get("action_count") or 0),
            final_url=str(body.get("url") or "") or None,
            failure_reason=(
                None

                if status == "POSTED"
                else str(body.get("error_type") or status)
            ),
        )
        self.telemetry.append(
            task_type="X_PUBLISH",
            site="x.com",
            result=result,
            account_id=record.account_id,
        )
        if status == "POSTED":
            return XBrowserPublishResult(
                post_id=str(body.get("post_id") or ""),
                url=str(body.get("url") or ""),
                handle=str(body.get("handle") or ""),
                duplicate_found=bool(body.get("duplicate_found")),
                provider="JEV_ACCOUNT",
            )
        detail = str(
            body.get("message") or body.get("error_type") or status
        )[:300]
        if status == "SESSION_UNAVAILABLE":
            raise BrowserRuntimeUnavailable(detail)
        if status == "INVALID_DRAFT":
            raise ValueError(detail)

        raise XBrowserPublicationUnverified(
            detail or "account JEV publication was not verified"
        )



class ExistingProfileJevXTransport:
    """Run JEV inside the already-authenticated TinyFish X browser context."""

    def __init__(
        self,
        *,
        state_root: Path,
        client=None,
        registry=None,
        runner: Callable[..., Any] | None = None,
        timeout_seconds: float = 150.0,
    ) -> None:
        self.state_root = Path(state_root)
        self.client = client or TinyFishProfileClient(
            api_key=os.environ.get("TINYFISH_API_KEY", ""),
            profile_id=os.environ.get("TINYFISH_X_PROFILE_ID", ""),
            base_url=os.environ.get("TINYFISH_BASE_URL", TINYFISH_BASE_URL),
        )
        if registry is None:
            from .account_registry import AccountRegistry
            registry = AccountRegistry(self.state_root)
        self.registry = registry
        self.runner = runner or subprocess.run
        self.timeout_seconds = float(timeout_seconds)
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "ExistingProfileJevXTransport":
        if not _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED"):
            raise BrowserRuntimeUnavailable("CONTENT_LOCAL_X_BROWSER_ENABLED is false")
        if importlib.util.find_spec("jev_ultrafast") is None:
            raise BrowserRuntimeUnavailable("jev-ultrafast is not installed")
        if importlib.util.find_spec("browser_harness") is None:
            raise BrowserRuntimeUnavailable("browser-harness is not installed")
        try:
            return cls(
                state_root=state_root,
                timeout_seconds=float(
                    os.environ.get(
                        "CONTENT_EXISTING_PROFILE_JEV_TIMEOUT_SECONDS",
                        "150",
                    )
                ),
            )
        except TinyFishProfileUnavailable as exc:
            raise BrowserRuntimeUnavailable(str(exc)) from exc

    def _account(self):
        explicit = os.environ.get("CONTENT_X_ACCOUNT_ID", "").strip()
        if explicit:
            try:
                record = self.registry.get(explicit)
            except (KeyError, ValueError) as exc:
                raise BrowserRuntimeUnavailable(
                    "configured X account instance does not exist"
                ) from exc
            if record.provider_id != "x":
                raise BrowserRuntimeUnavailable(
                    "configured account instance is not an X account"
                )
            return record
        rows = self.registry.list(provider_id="x")
        if not rows:
            raise BrowserRuntimeUnavailable(
                "no X account instance is registered for existing-session JEV"
            )
        if len(rows) != 1:
            raise BrowserRuntimeUnavailable(
                "multiple X accounts are registered; CONTENT_X_ACCOUNT_ID is required"
            )
        return rows[0]

    @staticmethod
    def _worker_env(*, account_id: str, cdp_ws: str) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("BU_CDP_URL", None)
        env["BU_CDP_WS"] = cdp_ws
        env["BU_NAME"] = ("x_existing_" + account_id[-20:])[:64]
        src_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = (
            src_root
            if not env.get("PYTHONPATH")
            else src_root + os.pathsep + env["PYTHONPATH"]
        )
        return env

    def _run_worker(
        self,
        *,
        account_id: str,
        expected_handle: str,
        cdp_ws: str,
        text: str,
        mode: str,
    ) -> dict[str, Any]:
        completed = self.runner(
            [
                sys.executable,
                "-m",
                "wechat_traffic_agent.existing_session_x_publish_worker",
            ],
            input=json.dumps(
                {
                    "text": text,
                    "expected_handle": expected_handle,
                    "mode": mode,
                }
            ),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=self._worker_env(
                account_id=account_id,
                cdp_ws=cdp_ws,
            ),
            check=False,
        )
        rows = [
            line for line in completed.stdout.splitlines()
            if line.strip().startswith("{")
        ]
        if not rows:
            return {
                "status": "SESSION_UNAVAILABLE",
                "attempted": False,
                "error_type": "WORKER_OUTPUT_MISSING",
            }
        try:
            body = json.loads(rows[-1])
        except json.JSONDecodeError:
            return {
                "status": "SESSION_UNAVAILABLE",
                "attempted": False,
                "error_type": "WORKER_OUTPUT_INVALID",
            }
        return body if isinstance(body, dict) else {}

    def _start_session(self) -> tuple[str, str]:
        try:
            setup = self.client.start_setup(
                url=X_HOME_URL,
                timeout_seconds=180,
            )
        except TinyFishProfileUnavailable as exc:
            raise BrowserRuntimeUnavailable(str(exc)) from exc
        session_id = str(setup.get("session_id") or "")
        cdp_ws = str(setup.get("cdp_url") or "")
        if not session_id or not cdp_ws:
            raise BrowserRuntimeUnavailable(
                "existing X profile session did not return session_id/cdp_url"
            )
        return session_id, cdp_ws

    def _cancel(self, session_id: str) -> None:
        try:
            self.client.cancel(session_id=session_id)
        except Exception:
            pass

    def _save(self, session_id: str) -> None:
        try:
            self.client.save(session_id=session_id)
        except Exception:
            pass
    def _record(
        self,
        *,
        record,
        body: dict[str, Any],
        started: float,
        status: str,
    ) -> None:
        self.telemetry.append(
            task_type="X_PUBLISH",
            site="x.com",
            result=BrowserTaskResult(
                provider="JEV_EXISTING_PROFILE",
                status=status,
                elapsed_ms=int(
                    body.get("elapsed_ms")
                    or round((time.perf_counter() - started) * 1000)
                ),
                action_count=int(body.get("action_count") or 0),
                final_url=str(body.get("url") or "") or None,
                failure_reason=(
                    None
                    if status == "COMPLETED"
                    else str(body.get("error_type") or body.get("status") or "")
                ),
            ),
            account_id=record.account_id,
        )

    @staticmethod
    def _result(body: dict[str, Any]) -> XBrowserPublishResult:
        return XBrowserPublishResult(
            post_id=str(body.get("post_id") or ""),
            url=str(body.get("url") or ""),
            handle=str(body.get("handle") or ""),
            duplicate_found=bool(body.get("duplicate_found")),
            provider="JEV_EXISTING_PROFILE",
        )

    def _fresh_reconcile(
        self,
        *,
        record,
        text: str,
    ) -> dict[str, Any]:
        session_id, cdp_ws = self._start_session()
        try:
            try:
                return self._run_worker(
                    account_id=record.account_id,
                    expected_handle=record.identity,
                    cdp_ws=cdp_ws,
                    text=text,
                    mode="reconcile",
                )
            except subprocess.TimeoutExpired:
                return {
                    "status": "NOT_FOUND",
                    "attempted": False,
                    "action_count": 0,
                    "error_type": "RECONCILE_TIMEOUT",
                }
        finally:
            self._cancel(session_id)
    async def publish(self, *, text: str) -> XBrowserPublishResult:
        return await asyncio.to_thread(self._publish_sync, text)

    def _publish_sync(self, text: str) -> XBrowserPublishResult:
        if not text.strip() or len(text) > 25_000:
            raise ValueError("X post must contain 1-25000 characters")
        record = self._account()
        started = time.perf_counter()
        session_id, cdp_ws = self._start_session()
        body: dict[str, Any]
        try:
            try:
                body = self._run_worker(
                    account_id=record.account_id,
                    expected_handle=record.identity,
                    cdp_ws=cdp_ws,
                    text=text,
                    mode="publish",
                )
            except subprocess.TimeoutExpired:
                body = {
                    "status": "PUBLICATION_UNVERIFIED",
                    "attempted": True,
                    "action_count": 1,
                    "error_type": "WORKER_TIMEOUT",
                }
            except OSError as exc:
                self._cancel(session_id)
                raise BrowserRuntimeUnavailable(
                    f"existing-session JEV worker could not start: {type(exc).__name__}"
                ) from exc

            status = str(body.get("status") or "")
            attempted = bool(body.get("attempted")) or int(
                body.get("action_count") or 0
            ) > 0

            if status == "POSTED":
                self._save(session_id)
                self._record(
                    record=record,
                    body=body,
                    started=started,
                    status="COMPLETED",
                )
                return self._result(body)

            if attempted:
                self._save(session_id)
            else:
                self._cancel(session_id)

            if attempted:
                try:
                    reconciled = self._fresh_reconcile(
                        record=record,
                        text=text,
                    )
                except Exception as exc:
                    self._record(
                        record=record,
                        body=body,
                        started=started,
                        status="FAILED",
                    )
                    raise XBrowserPublicationUnverified(
                        "JEV write may have reached X and fresh-session "
                        f"reconciliation failed: {type(exc).__name__}"
                    ) from exc
                if str(reconciled.get("status") or "") == "POSTED":
                    reconciled["action_count"] = 1
                    self._record(
                        record=record,
                        body=reconciled,
                        started=started,
                        status="COMPLETED",
                    )
                    return self._result(reconciled)
                self._record(
                    record=record,
                    body=body,
                    started=started,
                    status="FAILED",
                )
                raise XBrowserPublicationUnverified(
                    str(
                        body.get("message")
                        or "JEV write may have reached X but fresh-session "
                           "reconciliation found no public status URL"
                    )[:400]
                )

            self._record(
                record=record,
                body=body,
                started=started,
                status="FAILED",
            )
            detail = str(
                body.get("message")
                or body.get("error_type")
                or status
                or "existing-session JEV unavailable"
            )[:400]
            if status == "INVALID_DRAFT":
                raise ValueError(detail)
            raise BrowserRuntimeUnavailable(detail)
        finally:
            # Successful/ambiguous writes save above; pre-write failures cancel above.
            # This finalizer only cleans up if an exception happened before classification.
            pass


__all__ = ["AccountJevXTransport", "ExistingProfileJevXTransport", "LocalHarnessXTransport"]
