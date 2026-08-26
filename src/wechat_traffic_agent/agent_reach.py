"""Agent-Reach adapter: a CLI capability layer, not an HTTP service.

`agent-reach doctor --json` reports per-channel health and the active
backend; content is fetched by invoking the tools Agent-Reach manages
(docs show `curl -s https://r.jina.ai/{url}` for web reading, `bili` /
`twitter` CLIs per platform). This adapter runs configured command
templates through an injectable runner, so tests stay offline.

PR2 boundary: comments/author parsing needs real per-platform CLI output
shapes, which arrive in PR3 when backends are wired — until then those
protocols return UNAVAILABLE instead of guessing at parser formats.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    SourceResult,
    SourceStatus,
)

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
"""Runner contract: (returncode, stdout, stderr); callers branch on nonzero returncode.
A timeout returns returncode 124 (curl's convention) with the message in stderr."""

DEFAULT_TIMEOUT = 30.0

DEFAULT_COMMANDS: dict[str, list[str]] = {
    "doctor": ["agent-reach", "doctor", "--json"],
    "read_web": ["curl", "-s", "https://r.jina.ai/{url}"],
}


async def _subprocess_runner(cmd: list[str], timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return (124, "", f"command timed out after {timeout}s")
    returncode = process.returncode
    assert returncode is not None  # communicate() 完成后必有值
    return (
        returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class AgentReachProvider:
    def __init__(
        self,
        *,
        commands: dict[str, list[str]] | None = None,
        runner: Runner | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.commands = {**DEFAULT_COMMANDS, **(commands or {})}
        self.runner = runner or functools.partial(_subprocess_runner, timeout=timeout)

    async def _run(self, name: str, placeholders: dict[str, str]) -> tuple[int, str, str] | None:
        template = self.commands.get(name)
        if not template:
            return None
        cmd: list[str] = []
        for part in template:
            try:
                cmd.append(part.format(**placeholders))
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"command template {name!r} has invalid placeholders: {exc}"
                ) from exc
        return await self.runner(cmd)

    async def doctor(self) -> SourceResult[dict[str, Any]]:
        """Health of all channels; PR3 uses active_backend to pick backends."""
        observed_at = datetime.now(timezone.utc)
        try:
            outcome = await self._run("doctor", {})
        except ValueError as exc:
            return self._failed("doctor", str(exc), observed_at, {})
        if outcome is None:
            return self._failed("doctor", "doctor template not configured", observed_at, {})
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                "doctor", stderr.strip() or f"exit code {returncode}", observed_at, {}
            )
        try:
            data = json.loads(stdout)
        except ValueError:
            return self._failed("doctor", "doctor output is not JSON", observed_at, {})
        return SourceResult(
            source="agent-reach",
            platform="doctor",
            status=SourceStatus.OK,
            items=data,
            observed_at=observed_at,
        )

    async def read(self, url: str, *, platform: str = "generic_web") -> SourceResult[ContentSnapshot]:
        observed_at = datetime.now(timezone.utc)
        try:
            outcome = await self._run("read_web", {"url": url})
        except ValueError as exc:
            return self._failed(
                platform,
                str(exc),
                observed_at,
                ContentSnapshot(content_id="", platform=platform, url="", text=""),
            )
        if outcome is None:
            return self._failed(
                platform,
                "read_web template not configured",
                observed_at,
                ContentSnapshot(content_id="", platform=platform, url="", text=""),
            )
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                platform,
                stderr.strip() or f"exit code {returncode}",
                observed_at,
                ContentSnapshot(content_id="", platform=platform, url="", text=""),
            )
        text = stdout.strip()
        if not text:
            return self._failed(
                platform,
                "empty content",
                observed_at,
                ContentSnapshot(content_id="", platform=platform, url="", text=""),
            )
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.OK,
            items=ContentSnapshot(content_id=url, platform=platform, url=url, text=text),
            observed_at=observed_at,
        )

    async def comments(self, content_id: str, *, platform: str) -> SourceResult[tuple[Comment, ...]]:
        return self._unavailable(
            platform,
            "comments backend not wired (needs platform CLI output shape; see PR3)",
            (),
        )

    async def author(self, author_id: str, *, platform: str) -> SourceResult[AuthorSnapshot]:
        return self._unavailable(
            platform,
            "author backend not wired (needs platform CLI output shape; see PR3)",
            AuthorSnapshot(author_id="", platform=platform),
        )

    def _failed(self, platform: str, error: str, observed_at: datetime, items) -> SourceResult:
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.FAILED,
            items=items,
            error=error,
            observed_at=observed_at,
        )

    def _unavailable(self, platform: str, reason: str, items) -> SourceResult:
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.UNAVAILABLE,
            items=items,
            error=reason,
            observed_at=datetime.now(timezone.utc),
        )
