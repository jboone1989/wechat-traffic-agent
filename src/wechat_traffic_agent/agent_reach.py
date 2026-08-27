"""Agent-Reach adapter: a CLI capability layer, not an HTTP service.

`agent-reach doctor --json` reports per-channel health and the active
backend; content is fetched by invoking the tools Agent-Reach manages.
This adapter runs configured command templates through an injectable
runner, so tests stay offline.

PR3: comments/author ship with documented bilibili-cli templates
(`bili video {id} --comments --json` / `bili user {author_id} --json`),
parsed defensively against the `{ok, data, error}` envelope. Other
platforms have no documented CLI output shape yet — inject them via
``commands={"comments_<platform>": [...]}``. ``engagement`` has no
documented producer and stays UNAVAILABLE. Deep fetch can share one
httpx client by passing ``http_client``: ``read`` then goes through
``https://r.jina.ai/{url}`` over that client instead of one curl
subprocess per item.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from .sources import (
    AuthorSnapshot,
    Comment,
    ContentSnapshot,
    EngagementSnapshot,
    SourceResult,
    SourceStatus,
)

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
"""Runner contract: (returncode, stdout, stderr); callers branch on nonzero returncode.
A timeout returns returncode 124 (curl's convention) with the message in stderr."""

JINA_READER_PREFIX = "https://r.jina.ai/"

DEFAULT_TIMEOUT = 30.0

DEFAULT_COMMANDS: dict[str, list[str]] = {
    "doctor": ["agent-reach", "doctor", "--json"],
    "read_web": ["curl", "-s", f"{JINA_READER_PREFIX}{{url}}"],
    # 只有 bilibili-cli 文档化了输出形状；其余平台按需注入模板。
    "comments_bilibili": ["bili", "video", "{content_id}", "--comments", "--json"],
    "author_bilibili": ["bili", "user", "{author_id}", "--json"],
}


def _comment_rows(payload: object) -> object:
    """Accept both a bare list and the {ok, data: {comments: []}} envelope."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data.get("comments")
    return None


def _envelope_error(payload: object) -> str | None:
    """bilibili-cli style envelope error: {ok: false, error: {code, message}}."""
    if isinstance(payload, dict) and payload.get("ok") is False:
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "unknown CLI error")
        return "CLI reported failure without an error detail"
    return None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.commands = {**DEFAULT_COMMANDS, **(commands or {})}
        self.runner = runner or functools.partial(_subprocess_runner, timeout=timeout)
        self.http_client = http_client

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
        degenerate = ContentSnapshot(content_id="", platform=platform, url="", text="")
        if self.http_client is not None:
            try:
                response = await self.http_client.get(f"{JINA_READER_PREFIX}{url}")
                response.raise_for_status()
            except httpx.HTTPError as exc:
                return self._failed(
                    platform, f"{type(exc).__name__}: {exc}", observed_at, degenerate
                )
            text = response.text.strip()
            if not text:
                return self._failed(platform, "empty content", observed_at, degenerate)
            return SourceResult(
                source="agent-reach",
                platform=platform,
                status=SourceStatus.OK,
                items=ContentSnapshot(content_id=url, platform=platform, url=url, text=text),
                observed_at=observed_at,
            )
        try:
            outcome = await self._run("read_web", {"url": url})
        except ValueError as exc:
            return self._failed(
                platform,
                str(exc),
                observed_at,
                degenerate,
            )
        if outcome is None:
            return self._failed(
                platform,
                "read_web template not configured",
                observed_at,
                degenerate,
            )
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                platform,
                stderr.strip() or f"exit code {returncode}",
                observed_at,
                degenerate,
            )
        text = stdout.strip()
        if not text:
            return self._failed(
                platform,
                "empty content",
                observed_at,
                degenerate,
            )
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.OK,
            items=ContentSnapshot(content_id=url, platform=platform, url=url, text=text),
            observed_at=observed_at,
        )

    async def comments(self, content_id: str, *, platform: str) -> SourceResult[tuple[Comment, ...]]:
        observed_at = datetime.now(timezone.utc)
        name = f"comments_{platform}"
        try:
            outcome = await self._run(name, {"content_id": content_id})
        except ValueError as exc:
            return self._failed(platform, str(exc), observed_at, ())
        if outcome is None:
            return self._unavailable(
                platform,
                f"{name} template not configured (inject via commands={{{name!r}: [...]}})",
                (),
            )
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                platform, stderr.strip() or f"exit code {returncode}", observed_at, ()
            )
        try:
            payload = json.loads(stdout)
        except ValueError:
            return self._failed(
                platform, f"{name} output is not JSON", observed_at, ()
            )
        env_error = _envelope_error(payload)
        if env_error is not None:
            return self._failed(platform, env_error, observed_at, ())
        rows = _comment_rows(payload)
        if not isinstance(rows, list):
            return self._failed(
                platform, "unexpected payload: no comment list", observed_at, ()
            )
        comments: list[Comment] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = ""
            nested = row.get("content")
            if isinstance(nested, dict):
                text = str(nested.get("message") or "").strip()
            if not text:
                text = str(row.get("text") or "").strip()
            if not text:
                continue
            raw_id = row.get("rpid", row.get("id", row.get("comment_id")))
            comments.append(
                Comment(
                    content_id=content_id,
                    platform=platform,
                    comment_id=str(raw_id) if raw_id is not None else None,
                    author_id=(
                        str(row["author_id"]) if row.get("author_id") is not None else None
                    ),
                    likes=_coerce_int(row.get("like", row.get("likes"))),
                    text=text,
                )
            )
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.OK,
            items=tuple(comments),
            observed_at=observed_at,
        )

    async def author(self, author_id: str, *, platform: str) -> SourceResult[AuthorSnapshot]:
        observed_at = datetime.now(timezone.utc)
        name = f"author_{platform}"
        degenerate = AuthorSnapshot(author_id="", platform=platform)
        try:
            outcome = await self._run(name, {"author_id": author_id})
        except ValueError as exc:
            return self._failed(platform, str(exc), observed_at, degenerate)
        if outcome is None:
            return self._unavailable(
                platform,
                f"{name} template not configured (inject via commands={{{name!r}: [...]}})",
                degenerate,
            )
        returncode, stdout, stderr = outcome
        if returncode != 0:
            return self._failed(
                platform, stderr.strip() or f"exit code {returncode}", observed_at, degenerate
            )
        try:
            payload = json.loads(stdout)
        except ValueError:
            return self._failed(platform, f"{name} output is not JSON", observed_at, degenerate)
        env_error = _envelope_error(payload)
        if env_error is not None:
            return self._failed(platform, env_error, observed_at, degenerate)
        data = payload.get("data") if isinstance(payload, dict) else None
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            return self._failed(
                platform, "unexpected payload: no user object", observed_at, degenerate
            )
        raw_id = user.get("mid", user.get("user_id"))
        followers = _coerce_int(user.get("follower", user.get("followers")))
        # 调用成功但字段缺失 ≠ 失败：缺失保持 None（unavailable 数据）
        resolved_id = str(raw_id) if raw_id is not None else author_id
        return SourceResult(
            source="agent-reach",
            platform=platform,
            status=SourceStatus.OK,
            items=AuthorSnapshot(author_id=resolved_id, platform=platform, followers=followers),
            observed_at=observed_at,
        )

    async def engagement(self, content_id: str, *, platform: str) -> SourceResult[EngagementSnapshot]:
        """No documented CLI producer yet; deep fetch fills engagement from readers."""
        return self._unavailable(
            platform,
            "engagement backend not wired (no documented producer; see PR3)",
            EngagementSnapshot(content_id="", platform=platform),
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
