"""Browser Use CLI adapter over Ferro's native persistent Chromium.

Browser Use is deliberately isolated in its own virtualenv. Ferro owns browser
processes and profiles; Browser Use attaches through localhost CDP and only
performs page operations.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

from .account_browser_profiles import AccountBrowserProfileManager


class BrowserUseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserUsePage:
    account_id: str
    url: str
    title: str
    elapsed_ms: int
    provider: str = "BROWSER_USE_CLI"


class AccountBrowserUseExecutor:
    """Attach Browser Use to one account's already-running native Chromium."""

    def __init__(
        self,
        *,
        state_root: Path,
        profile_manager=None,
        cli_path: str | Path | None = None,
        runner=None,
    ) -> None:
        self.state_root = Path(state_root)
        self.profile_manager = (
            profile_manager
            if profile_manager is not None
            else AccountBrowserProfileManager(self.state_root)
        )
        self.cli_path = Path(
            cli_path
            or os.environ.get("CONTENT_BROWSER_USE_CLI")
            or "/home/ubuntu/browser-use-venv/bin/browser-use"
        )
        self.runner = runner or subprocess.run

    def readiness(self) -> dict[str, Any]:
        return {
            "provider": "BROWSER_USE_CLI",
            "status": "READY" if self.cli_path.is_file() else "NOT_INSTALLED",
            "cli_path": str(self.cli_path),
            "browser_owner": "FERRO_NATIVE_CHROMIUM",
        }

    @staticmethod
    def _validate_url(url: str) -> str:
        value = str(url or "").strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("browser URL must be absolute http(s)")
        return value

    @staticmethod
    def _worker_env(*, account_id: str, cdp_url: str) -> dict[str, str]:
        parsed = urlparse(str(cdp_url))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None
        ):
            raise BrowserUseUnavailable("unsafe native Chromium CDP endpoint")
        env = dict(os.environ)
        env["BU_CDP_URL"] = cdp_url
        env["BU_NAME"] = ("ferro_" + account_id[-24:])[:64]
        env["ANONYMIZED_TELEMETRY"] = "false"
        return env

    def run_script(
        self,
        *,
        account_id: str,
        script: str,
        timeout_seconds: float = 30.0,
    ) -> str:
        if not self.cli_path.is_file():
            raise BrowserUseUnavailable(
                f"Browser Use CLI is not installed: {self.cli_path}"
            )
        runtime = self.profile_manager.ensure(account_id)
        if not runtime.running or not runtime.cdp_url:
            raise BrowserUseUnavailable("native Chromium account session unavailable")
        completed = self.runner(
            [str(self.cli_path)],
            input=str(script),
            text=True,
            capture_output=True,
            timeout=float(timeout_seconds),
            env=self._worker_env(
                account_id=account_id,
                cdp_url=runtime.cdp_url,
            ),
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise BrowserUseUnavailable(
                "Browser Use CLI failed"
                + (f": {detail[-500:]}" if detail else "")
            )
        return str(completed.stdout or "")

    def execute_operations(
        self,
        *,
        account_id: str,
        operations: Sequence[Mapping[str, Any]],
        timeout_seconds: float = 45.0,
    ) -> BrowserUsePage:
        """Execute a bounded Browser Use operation list.

        No arbitrary Python/JavaScript is accepted from callers.  Ferro owns
        cognition; this adapter only translates structured operations to the
        Browser Use CLI helpers.
        """
        if not operations or len(operations) > 12:
            raise ValueError("browser operations must contain 1-12 steps")
        lines = ["import json"]
        for raw in operations:
            op = str(raw.get("action") or "").strip().lower()
            if op in {"open", "goto"}:
                url = self._validate_url(str(raw.get("url") or ""))
                helper = "new_tab" if op == "open" else "goto_url"
                lines.append(f"{helper}({json.dumps(url)})")
                lines.append("wait_for_load()")
            elif op == "click":
                x = float(raw.get("x"))
                y = float(raw.get("y"))
                if not (0 <= x <= 5000 and 0 <= y <= 5000):
                    raise ValueError("click coordinates are out of range")
                lines.append(f"click_at_xy({x!r}, {y!r})")
            elif op == "fill":
                selector = str(raw.get("selector") or "").strip()
                value = str(raw.get("text") or "")
                if not selector or len(selector) > 300:
                    raise ValueError("fill selector must contain 1-300 characters")
                if len(value) > 4000:
                    raise ValueError("fill text exceeds 4000 characters")
                lines.append(
                    "fill_input("
                    + json.dumps(selector)
                    + ", "
                    + json.dumps(value)
                    + ")"
                )
            elif op == "type":
                value = str(raw.get("text") or "")
                if not value or len(value) > 4000:
                    raise ValueError("typed text must contain 1-4000 characters")
                lines.append(f"type_text({json.dumps(value)})")
            elif op == "press":
                key = str(raw.get("key") or "").strip()
                if not key or len(key) > 40:
                    raise ValueError("press key must contain 1-40 characters")
                lines.append(f"press_key({json.dumps(key)})")
            elif op == "scroll":
                x = int(raw.get("x") or 0)
                y = int(raw.get("y") or 0)
                if abs(x) > 4000 or abs(y) > 4000:
                    raise ValueError("scroll delta is out of range")
                lines.append(f"scroll({x}, {y})")
            elif op == "wait":
                selector = str(raw.get("selector") or "").strip()
                if not selector or len(selector) > 300:
                    raise ValueError("wait selector must contain 1-300 characters")
                lines.append(f"wait_for_element({json.dumps(selector)})")
            elif op == "inspect":
                continue
            else:
                raise ValueError(f"unsupported browser operation: {op}")
        lines.append("print(json.dumps(page_info(), ensure_ascii=False))")
        started = time.perf_counter()
        output = self.run_script(
            account_id=account_id,
            script="\n".join(lines) + "\n",
            timeout_seconds=timeout_seconds,
        )
        rows = [row.strip() for row in output.splitlines() if row.strip()]
        try:
            body = json.loads(rows[-1]) if rows else {}
        except json.JSONDecodeError as exc:
            raise BrowserUseUnavailable(
                "Browser Use CLI returned invalid page info"
            ) from exc
        if not isinstance(body, dict):
            raise BrowserUseUnavailable("Browser Use CLI returned invalid page info")
        return BrowserUsePage(
            account_id=account_id,
            url=str(body.get("url") or ""),
            title=str(body.get("title") or ""),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    def inspect(
        self,
        *,
        account_id: str,
        url: str,
        timeout_seconds: float = 30.0,
    ) -> BrowserUsePage:
        return self.execute_operations(
            account_id=account_id,
            operations=[{"action": "open", "url": url}],
            timeout_seconds=timeout_seconds,
        )


__all__ = [
    "AccountBrowserUseExecutor",
    "BrowserUsePage",
    "BrowserUseUnavailable",
]
