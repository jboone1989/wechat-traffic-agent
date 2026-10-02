"""X publishing through Agent Runtime's persistent X browser profile."""
from __future__ import annotations

import asyncio
import json
import importlib.util
import os
from pathlib import Path
import shutil
import time
from typing import Any

from agent_runtime.integrations import (
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
    _normalize_text,
)
from .x_jev_transaction import jev_x_identity, jev_x_transaction


class LocalHarnessXTransport:
    """Single X Web transport over Agent Runtime; never starts its own Chrome."""

    provider_name = "JEV_AGENT_RUNTIME"

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
                    failure_detail=str(exc),
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

    def _run_jev_sync(self, script: str) -> dict[str, Any]:
        import fcntl
        self.state_root.mkdir(parents=True, exist_ok=True)
        with (self.state_root / "x_jev_session.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._run_jev_locked(script)

    def _run_jev_locked(self, script: str) -> dict[str, Any]:
        session_id = "ferro-x-publish"
        try:
            # Reuse the user's authenticated tab without forcing a home reload.
            self.browser.adopt_existing_tab(
                session_id, "https://x.com/", open_if_missing=True
            )
            spec = importlib.util.find_spec("jev_ultrafast")
            if spec is None or spec.origin is None:
                raise BrowserRuntimeUnavailable("jev-ultrafast is unavailable in the X execution environment")
            body = self.browser.run_jev_session_script(
                session_id,
                script,
                package_root=Path(spec.origin).parent.parent,
                timeout_seconds=max(45.0, min(180.0, self.timeout_ms / 1000 + 45.0)),
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            raise BrowserRuntimeUnavailable(
                f"Agent Runtime X transaction failed: {exc}"
            ) from exc
        return body

    async def publish_article(self, *, title, text, image_path, transaction_path, prepare_only=False):
        from .x_jev_article import jev_x_article, jev_x_article_reconcile
        from .x_browser import _read_json, _write_json
        import hashlib
        transaction_path=Path(transaction_path)
        digest=hashlib.sha256((title+"\n"+text+"\n"+hashlib.sha256(Path(image_path).read_bytes()).hexdigest()).encode()).hexdigest()
        previous=_read_json(transaction_path)
        if previous and previous.get("digest")!=digest:
            raise ValueError("Article transaction draft binding mismatch")
        if previous.get("status")=="POSTED":
            return self._verified_result(previous)
        if previous.get("attempted") or previous.get("status")=="SUBMIT_PENDING":
            body=await asyncio.to_thread(self._run_jev_sync,jev_x_article_reconcile(title=title,text=text,
                draft_url=str(previous.get("draft_url") or ""),cover_source=previous.get("cover_source") or [],
                expected_handle=self.expected_handle))
            _write_json(transaction_path,{**previous,**body,"digest":digest})
            return self._verified_result(body)
        _write_json(transaction_path,{**previous,"digest":digest,"status":"SUBMIT_PENDING" if not prepare_only else "PREPARING"})
        body=await asyncio.to_thread(self._run_jev_sync,jev_x_article(title=title,text=text,image_path=str(image_path),
            expected_handle=self.expected_handle,draft_url=str(previous.get("draft_url") or ""),prepare_only=prepare_only,
            checkpoint_path=str(transaction_path)))
        _write_json(transaction_path,{**body,"digest":digest})
        if prepare_only and body.get("status")=="DRAFT_PREPARED":
            return body
        return self._verified_result(body)

    async def discover(self, *, source="MENTIONS", max_results=20):
        from urllib.parse import quote
        from .x_jev_social import jev_x_discover
        urls={"MENTIONS":"https://x.com/notifications/mentions", "HOME":"https://x.com/home"}
        queries={"AGENT_RUNTIME":"persistent AI agents runtime", "LIVINGRUNTIME":"LivingRuntime Remote", "AGENT_MEMORY":"AI agent memory continuity"}
        if source not in urls and source not in queries:
            raise ValueError("Unknown X discovery source")
        url=urls.get(source) or "https://x.com/search?q="+quote(queries[source])+"&f=live"
        body=await asyncio.to_thread(self._run_jev_sync,jev_x_discover(url,max_results))
        if body.get("status")!="OBSERVED":
            raise BrowserRuntimeUnavailable(str(body.get("detail") or body))
        return body["posts"]

    async def reply(self, *, text, target_url):
        from .x_jev_social import jev_x_reply
        if not text.strip() or len(text)>280:
            raise ValueError("X reply must contain 1-280 characters")
        import hashlib
        from .x_browser import _read_json, _write_json
        digest=hashlib.sha256((target_url+"\n"+text).encode()).hexdigest()
        path=self.state_root/"content"/"x"/"reply-transactions"/(digest+".json")
        prior=_read_json(path)
        if prior.get("status")=="POSTED":
            return self._verified_result(prior)
        reconcile_only=bool(prior.get("attempted") or prior.get("status")=="SUBMIT_PENDING")
        _write_json(path,{**prior,"status":"SUBMIT_PENDING","text":text,"target_url":target_url})
        body=await asyncio.to_thread(self._run_jev_sync,jev_x_reply(text,target_url,self.expected_handle,reconcile_only=reconcile_only))
        _write_json(path,{**body,"text":text,"target_url":target_url})
        return self._verified_result(body)

    def _verified_result(self, body):
        if body.get("status")=="PUBLICATION_UNVERIFIED":
            raise XBrowserPublicationUnverified(str(body.get("detail") or body))
        if body.get("status")!="POSTED":
            raise BrowserRuntimeUnavailable(str(body.get("detail") or body))
        handle=str(body.get("handle") or "")
        if self.expected_handle and handle.casefold()!=self.expected_handle.casefold():
            raise BrowserRuntimeUnavailable("X transaction identity mismatch")
        return XBrowserPublishResult(post_id=str(body["post_id"]),url=str(body["url"]),handle=handle,
            duplicate_found=bool(body.get("duplicate_found")),provider=self.provider_name)

    async def identity(self) -> dict[str, Any]:
        body = await asyncio.to_thread(
            self._run_jev_sync, jev_x_identity(self.expected_handle)
        )
        if body.get("status") != "IDENTITY_OBSERVED":
            raise BrowserRuntimeUnavailable(str(body.get("detail") or body))
        return dict(body["user"])

    def _publish_sync(self, text: str) -> XBrowserPublishResult:
        if not text.strip() or len(text) > 25_000:
            raise ValueError("X post must contain 1-25000 characters")
        body = self._run_jev_sync(self._transaction_script(text, self.expected_handle))

        status = str(body.get("status") or "")
        if status == "PUBLICATION_UNVERIFIED":
            raise XBrowserPublicationUnverified(
                f"Jev X {body.get('stage', 'transaction')} failed "
                f"(attempted={body.get('attempted', False)}): "
                + str(body.get("detail") or "X publication was not verified")
            )
        if status != "POSTED":
            raise BrowserRuntimeUnavailable(
                f"Jev X {body.get('stage', 'transaction')} failed: "
                + str(body.get("detail") or body)
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
    def _transaction_script(text: str, expected_handle: str = "") -> str:
        return jev_x_transaction(text, expected_handle)


def build_agent_runtime_x_browser() -> BrowserHarnessProvider:
    return LocalHarnessXTransport._provider()


__all__ = ["LocalHarnessXTransport", "build_agent_runtime_x_browser"]
