"""Provider-neutral browser runtime for Ferro/Content Agent.

Known, deterministic site skills should use CDP/Playwright directly. Jev is the
adaptive controller for unfamiliar or changed pages. TinyFish remains a
temporary provider fallback while the local browser runtime is being adopted.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import urlparse


class BrowserRuntimeUnavailable(RuntimeError):
    """A browser provider is not configured or cannot be started."""


@dataclass(frozen=True)
class BrowserTaskResult:
    provider: str
    status: str
    elapsed_ms: int
    action_count: int
    final_url: str | None = None
    failure_reason: str | None = None


def _enabled(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def jev_readiness() -> dict[str, Any]:
    enabled = _enabled("CONTENT_JEV_BROWSER_ENABLED", default=True)
    typesafe = bool(os.environ.get("TYPESAFE_API_KEY", "").strip())
    explicit_text_key = bool(os.environ.get("TEXT_MODEL_API_KEY", "").strip())
    deepseek_key = bool(os.environ.get("VIRTUALBRAIN_LLM_API_KEY", "").strip())
    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    openrouter_model = bool(os.environ.get("CONTENT_JEV_TEXT_MODEL", "").strip())
    text_key = explicit_text_key or deepseek_key or (openrouter_key and openrouter_model)
    installed = _module_available("jev_ultrafast")
    harness = _module_available("browser_harness")
    if not enabled:
        status, reason = "DISABLED", "CONTENT_JEV_BROWSER_ENABLED is false"
    elif not typesafe:
        status, reason = "NOT_CONFIGURED", "TYPESAFE_API_KEY is not configured"
    elif not text_key:
        status, reason = "NOT_CONFIGURED", "No text helper API key is configured"
    elif not installed or not harness:
        status, reason = "NOT_INSTALLED", "jev-ultrafast/browser-harness is not installed"
    else:
        status, reason = "READY", ""
    return {
        "provider": "JEV",
        "status": status,
        "reason": reason,
        "adaptive_default": True,
        "typesafe_configured": typesafe,
        "text_helper_configured": text_key,
        "text_helper_source": (
            "EXPLICIT"
            if explicit_text_key
            else "DEEPSEEK_STABLE"
            if deepseek_key
            else "OPENROUTER"
            if openrouter_key and openrouter_model
            else "MISSING"
        ),
        "package_installed": installed,
        "browser_harness_installed": harness,
    }


def local_cdp_readiness() -> dict[str, Any]:
    enabled = _enabled("CONTENT_LOCAL_X_BROWSER_ENABLED")
    installed = _module_available("jev_ultrafast")
    harness = _module_available("browser_harness")
    if not enabled:
        status, reason = "DISABLED", "CONTENT_LOCAL_X_BROWSER_ENABLED is false"
    elif not installed or not harness:
        status, reason = "NOT_INSTALLED", "jev-ultrafast/browser-harness is not installed"
    else:
        status, reason = "READY", ""
    return {
        "provider": "LOCAL_CDP",
        "status": status,
        "reason": reason,
        "deterministic_default": True,
        "package_installed": installed,
        "browser_harness_installed": harness,
    }


def browser_runtime_status() -> dict[str, Any]:
    tinyfish = bool(
        os.environ.get("TINYFISH_API_KEY", "").strip()
        and os.environ.get("TINYFISH_X_PROFILE_ID", "").strip()
    )
    return {
        "schema_version": "content-browser-runtime-status.v1",
        "routing_policy": "DETERMINISTIC_SKILL_THEN_JEV_THEN_TINYFISH",
        "x_fallback_policy": "OFFICIAL_API_THEN_TINYFISH_THEN_LOCAL_CDP",
        "adaptive_default": "JEV",
        "providers": [
            local_cdp_readiness(),
            jev_readiness(),
            {
                "provider": "TINYFISH",
                "status": "READY" if tinyfish else "NOT_CONFIGURED",
                "fallback_only": True,
            },
        ],
    }


class BrowserTelemetryLedger:
    """Persist provider economics without recording goals, page text, or secrets."""

    def __init__(self, state_root: Path) -> None:
        self.path = Path(state_root) / "browser" / "runtime-events.jsonl"

    def append(
        self,
        *,
        task_type: str,
        site: str,
        result: BrowserTaskResult,
        account_id: str | None = None,
    ) -> None:
        row = {
            "schema_version": "content-browser-runtime-event.v1",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "task_type": str(task_type)[:80],
            "site": str(site)[:160],
            **({"account_id": str(account_id)[:80]} if account_id else {}),
            **asdict(result),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


class JevBrowserExecutor:
    """Adaptive browser controller. Jev is never treated as outcome evidence."""

    def __init__(
        self,
        *,
        state_root: Path,
        agent_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.agent_factory = agent_factory
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "JevBrowserExecutor":
        readiness = jev_readiness()
        if readiness["status"] != "READY":
            raise BrowserRuntimeUnavailable(str(readiness["reason"] or readiness["status"]))
        if not os.environ.get("TEXT_MODEL_API_KEY", "").strip():
            deepseek = os.environ.get("VIRTUALBRAIN_LLM_API_KEY", "").strip()
            openrouter = os.environ.get("OPENROUTER_API_KEY", "").strip()
            openrouter_model = os.environ.get("CONTENT_JEV_TEXT_MODEL", "").strip()
            if deepseek:
                os.environ["TEXT_MODEL_API_KEY"] = deepseek
                os.environ.setdefault("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
                os.environ.setdefault("TEXT_MODEL", "deepseek-chat")
            elif openrouter and openrouter_model:
                os.environ["TEXT_MODEL_API_KEY"] = openrouter
                os.environ.setdefault("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
                os.environ.setdefault("TEXT_MODEL", openrouter_model)
        return cls(state_root=state_root)

    def _factory(self) -> Callable[[str, str], Any]:
        if self.agent_factory is not None:
            return self.agent_factory
        try:
            from jev_ultrafast import Agent
        except ImportError as exc:
            raise BrowserRuntimeUnavailable("jev-ultrafast is not installed") from exc
        return Agent

    async def run(self, *, url: str, goal: str, task_type: str = "GENERAL") -> BrowserTaskResult:
        site = urlparse(url).hostname or "unknown"
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(self._run_sync, url, goal)
        except Exception as exc:
            result = BrowserTaskResult(
                provider="JEV",
                status="FAILED",
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                action_count=0,
                failure_reason=type(exc).__name__,
            )
            self.telemetry.append(task_type=task_type, site=site, result=result)
            raise
        self.telemetry.append(task_type=task_type, site=site, result=result)
        return result

    def _run_sync(self, url: str, goal: str) -> BrowserTaskResult:
        if not url.startswith(("https://", "http://")):
            raise ValueError("browser URL must be http(s)")
        if not goal.strip():
            raise ValueError("browser goal must be non-empty")
        started = time.perf_counter()
        final: dict[str, Any] = {}
        factory = self._factory()
        with factory(url, goal) as agent:
            for state in agent.run():
                final = dict(state)
            if not final and hasattr(agent, "snapshot"):
                final = dict(agent.snapshot())
        status = str(final.get("status") or "blocked").upper()
        page = final.get("page") if isinstance(final.get("page"), dict) else {}
        history = final.get("history") if isinstance(final.get("history"), list) else []
        return BrowserTaskResult(
            provider="JEV",
            status="COMPLETED" if status == "DONE" else "BLOCKED",
            elapsed_ms=int(final.get("elapsed_ms") or round((time.perf_counter() - started) * 1000)),
            action_count=len(history),
            final_url=str(page.get("url") or "") or None,
            failure_reason=None if status == "DONE" else status,
        )


class AccountJevExecutor:
    """Run Jev against one account's isolated persistent Chromium profile."""

    def __init__(self, *, state_root: Path, profile_manager=None) -> None:
        self.state_root = Path(state_root)
        if profile_manager is None:
            from .account_browser_profiles import AccountBrowserProfileManager
            profile_manager = AccountBrowserProfileManager(self.state_root)
        self.profile_manager = profile_manager
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "AccountJevExecutor":
        readiness = jev_readiness()
        if readiness["status"] != "READY":
            raise BrowserRuntimeUnavailable(str(readiness["reason"] or readiness["status"]))
        return cls(state_root=state_root)

    @staticmethod
    def _worker_env(*, account_id: str, cdp_url: str) -> dict[str, str]:
        env = dict(os.environ)
        if not env.get("TEXT_MODEL_API_KEY", "").strip():
            deepseek = env.get("VIRTUALBRAIN_LLM_API_KEY", "").strip()
            openrouter = env.get("OPENROUTER_API_KEY", "").strip()
            model = env.get("CONTENT_JEV_TEXT_MODEL", "").strip()
            if deepseek:
                env["TEXT_MODEL_API_KEY"] = deepseek
                env.setdefault("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
                env.setdefault("TEXT_MODEL", "deepseek-chat")
            elif openrouter and model:
                env["TEXT_MODEL_API_KEY"] = openrouter
                env.setdefault("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
                env.setdefault("TEXT_MODEL", model)
        env["BU_CDP_URL"] = cdp_url
        env["BU_NAME"] = ("acct_" + account_id[-20:])[:64]
        src_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = (
            src_root
            if not env.get("PYTHONPATH")
            else src_root + os.pathsep + env["PYTHONPATH"]
        )
        return env

    async def run(
        self,
        *,
        account_id: str,
        url: str,
        goal: str,
        task_type: str = "GENERAL",
        timeout_seconds: float = 75.0,
    ) -> BrowserTaskResult:
        return await asyncio.to_thread(
            self._run_sync,
            account_id,
            url,
            goal,
            task_type,
            timeout_seconds,
        )

    def _run_sync(
        self,
        account_id: str,
        url: str,
        goal: str,
        task_type: str,
        timeout_seconds: float,
    ) -> BrowserTaskResult:
        if not url.startswith(("https://", "http://")):
            raise ValueError("browser URL must be http(s)")
        if not goal.strip():
            raise ValueError("browser goal must be non-empty")
        runtime = self.profile_manager.ensure(account_id)
        site = urlparse(url).hostname or "unknown"
        started = time.perf_counter()
        env = self._worker_env(account_id=account_id, cdp_url=runtime.cdp_url)
        completed = subprocess.run(
            [sys.executable, "-m", "wechat_traffic_agent.account_jev_worker"],
            input=json.dumps({"url": url, "goal": goal}),
            capture_output=True,
            text=True,
            timeout=float(timeout_seconds),
            env=env,
            check=False,
        )
        output = completed.stdout.strip().splitlines()
        try:
            body = json.loads(output[-1]) if output else {}
        except json.JSONDecodeError:
            body = {}
        result = BrowserTaskResult(
            provider="JEV_ACCOUNT",
            status=str(body.get("status") or "FAILED"),
            elapsed_ms=int(
                body.get("elapsed_ms")
                or round((time.perf_counter() - started) * 1000)
            ),
            action_count=int(body.get("action_count") or 0),
            final_url=str(body.get("final_url") or "") or None,
            failure_reason=(
                str(body.get("failure_reason") or "")
                or (None if completed.returncode == 0 else "WORKER_FAILED")
            ),
        )
        self.telemetry.append(
            task_type=task_type,
            site=site,
            result=result,
            account_id=account_id,
        )
        return result


__all__ = [
    "BrowserRuntimeUnavailable",
    "BrowserTaskResult",
    "BrowserTelemetryLedger",
    "AccountJevExecutor",
    "JevBrowserExecutor",
    "browser_runtime_status",
    "jev_readiness",
    "local_cdp_readiness",
]
