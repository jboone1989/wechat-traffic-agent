"""X publishing through Agent Runtime's persistent X browser profile."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

from agent_runtime.integrations import (
    BROWSER_NAVIGATE,
    BrowserHarnessProvider,
    BrowserHarnessRuntime,
    BrowserPolicy,
)

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
)


class LocalHarnessXTransport:
    """Single X Web transport over Agent Runtime; never starts its own Chrome."""

    provider_name = "AGENT_RUNTIME_BROWSER"

    def __init__(
        self,
        *,
        state_root: Path,
        timeout_ms: int = 30_000,
        expected_handle: str = "",
    ) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.expected_handle = str(expected_handle).strip().lstrip("@")
        self.telemetry = BrowserTelemetryLedger(self.state_root)
        self.browser = self._provider()

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "LocalHarnessXTransport":
        return cls(
            state_root=state_root,
            timeout_ms=int(os.environ.get("CONTENT_X_BROWSER_TIMEOUT_MS", "30000")),
            expected_handle=str(os.environ.get("CONTENT_X_USERNAME") or ""),
        )

    @staticmethod
    def _profile_state_path() -> Path:
        return Path(
            os.environ.get(
                "AGENT_RUNTIME_X_PROFILE_STATE",
                "~/.local/state/agent-runtime/browser/profiles/x/runtime.json",
            )
        ).expanduser()

    @classmethod
    def _cdp_url(cls) -> str:
        try:
            state = json.loads(cls._profile_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowserRuntimeUnavailable(
                "Agent Runtime X browser profile is unavailable"
            ) from exc
        port = state.get("cdp_port") if isinstance(state, dict) else None
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise BrowserRuntimeUnavailable(
                "Agent Runtime X browser profile has no valid CDP port"
            )
        return f"http://127.0.0.1:{port}"

    @staticmethod
    def _harness_executable() -> str:
        explicit = str(os.environ.get("AGENT_RUNTIME_BROWSER_HARNESS") or "").strip()
        if explicit:
            return explicit
        found = shutil.which("browser-harness")
        if found:
            return found
        candidate = Path.home() / "browser-use-venv" / "bin" / "browser-harness"
        if candidate.is_file():
            return str(candidate)
        raise BrowserRuntimeUnavailable("browser-harness executable is unavailable")

    @classmethod
    def _provider(cls) -> BrowserHarnessProvider:
        return BrowserHarnessProvider(
            policy=BrowserPolicy(
                max_sessions=2,
                timeout_seconds=30.0,
                max_text_chars=100_000,
                max_elements=300,
                max_input_chars=25_000,
                allowed_hosts=frozenset({"x.com", "www.x.com"}),
            ),
            runtime=BrowserHarnessRuntime(
                executable=cls._harness_executable(),
                state_dir=Path(
                    os.environ.get(
                        "AGENT_RUNTIME_BROWSER_STATE",
                        "~/.local/state/agent-runtime/browser-harness",
                    )
                ).expanduser(),
                cdp_url=cls._cdp_url(),
            ),
        )

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(self._publish_sync, text)
        except Exception as exc:
            self.telemetry.append(
                task_type="X_PUBLISH",
                site="x.com",
                result=BrowserTaskResult(
                    provider=self.provider_name,
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
                provider=self.provider_name,
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
        session_id = "ferro-x-publish"
        try:
            self.browser.invoke(
                BROWSER_NAVIGATE,
                {"session_id": session_id, "url": X_HOME_URL},
            )
            body = self.browser.run_session_script(
                session_id,
                self._transaction_script(text),
                timeout_seconds=max(45.0, min(180.0, self.timeout_ms / 1000 + 45.0)),
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            raise BrowserRuntimeUnavailable(
                f"Agent Runtime X transaction failed: {exc}"
            ) from exc

        status = str(body.get("status") or "")
        if status != "POSTED":
            raise XBrowserPublicationUnverified(
                str(body.get("detail") or "X publication was not verified")
            )
        handle = str(body.get("handle") or "")
        if self.expected_handle and handle.casefold() != self.expected_handle.casefold():
            raise BrowserRuntimeUnavailable(
                f"Agent Runtime X profile authenticated as @{handle}, expected @{self.expected_handle}"
            )
        return XBrowserPublishResult(
            post_id=str(body.get("post_id") or ""),
            url=str(body.get("url") or ""),
            handle=handle,
            duplicate_found=bool(body.get("duplicate_found")),
            provider=self.provider_name,
        )

    @staticmethod
    def _transaction_script(text: str) -> str:
        outbound = json.dumps(text, ensure_ascii=False)
        return f"""
import re, time
outbound = {outbound}
def norm(value):
    return ' '.join(str(value or '').split())
def wait_expr(expr, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = js(expr)
        if value:
            return value
        time.sleep(0.15)
    return None

profile = wait_expr('''(() => document.querySelector('[data-testid="AppTabBar_Profile_Link"]')?.getAttribute('href') || '')()''')
assert profile, 'Agent Runtime X profile is not authenticated'
handle = str(profile).strip('/').split('/', 1)[0]
assert handle and handle not in {{'home','i'}}, 'Agent Runtime X profile handle is unavailable'
needle = norm(outbound)[:160]

def find_existing():
    goto_url('https://x.com/' + handle)
    wait_for_load(timeout=20.0)
    wait_expr('''(() => Boolean(document.querySelector('article')))()''', 12.0)
    rows = js('''(() => [...document.querySelectorAll('article')].slice(0,15).map(a => ({{
      text:a.innerText || '',
      hrefs:[...a.querySelectorAll('a[href*="/status/"]')].map(x => x.getAttribute('href') || '')
    }})))()''') or []
    for row in rows:
        if needle and needle not in norm((row or {{}}).get('text') or ''):
            continue
        for href in (row or {{}}).get('hrefs') or []:
            match = re.search(r'^/([^/?#]+)/status/(\\d+)(?:[/?#]|$)', str(href))
            if match:
                return match.group(2)
    return ''

existing = find_existing()
if existing:
    result = {{
        'status':'POSTED',
        'post_id':existing,
        'url':f'https://x.com/{{handle}}/status/{{existing}}',
        'handle':handle,
        'duplicate_found':True,
    }}
else:
    goto_url('https://x.com/compose/post')
    wait_for_load(timeout=20.0)
    editor = wait_expr('''(() => Boolean(document.querySelector('[data-testid="tweetTextarea_0"]')))()''', 15.0)
    assert editor, 'X composer editor is unavailable'
    focused = js('''(() => {{
      const el=document.querySelector('[data-testid="tweetTextarea_0"]');
      if(!el)return false;
      el.focus();
      document.execCommand('selectAll', false, null);
      return true;
    }})()''')
    assert focused, 'X composer could not be focused'
    type_text(outbound)
    rendered = js('''(() => document.querySelector('[data-testid="tweetTextarea_0"]')?.innerText || '')()''') or ''
    assert norm(rendered) == norm(outbound), 'X composer did not preserve the draft text'
    point = js('''(() => {{
      const b=document.querySelector('[data-testid="tweetButton"]');
      if(!b || b.disabled || b.getAttribute('aria-disabled')==='true') return null;
      const r=b.getBoundingClientRect();
      if(!r.width || !r.height)return null;
      return {{x:r.x+r.width/2,y:r.y+r.height/2}};
    }})()''')
    assert point, 'X publish button is unavailable'
    click_at_xy(point['x'], point['y'])
    time.sleep(1.0)
    post_id = ''
    for _ in range(8):
        post_id = find_existing()
        if post_id:
            break
        time.sleep(0.7)
    assert post_id, 'X submit could not be tied to a public post URL'
    result = {{
        'status':'POSTED',
        'post_id':post_id,
        'url':f'https://x.com/{{handle}}/status/{{post_id}}',
        'handle':handle,
        'duplicate_found':False,
    }}
"""


def build_agent_runtime_x_browser() -> BrowserHarnessProvider:
    return LocalHarnessXTransport._provider()


__all__ = ["LocalHarnessXTransport", "build_agent_runtime_x_browser"]
