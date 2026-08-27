"""Bilibili platform-complete deep reader over bilibili-cli templates.

PR3 documented only ``comments``/``author`` shapes; deep fetch could
produce rank_score but never breakout/engagement/freshness, because the
ContentSnapshot carried no author_id/published_at and ``engagement`` had
no producer. PR7 closes that gap for this one platform first:

- ``read``      -> ``bili video {video_id} --json`` gives title/desc,
  owner.mid and pubdate, filling ContentSnapshot.author_id/.published_at;
  non-video URLs (专栏、作者页…) fall back to the generic Jina reader —
  degraded capability, not fabricated data;
- ``engagement``-> same video payload's ``stat``: view/like/reply/share/
  favorite map onto views/likes/comments/shares/saves (coin 投币无对应
  契约字段，暂不落库);
- ``comments`` / ``author`` delegate to the wrapped generic provider,
  reusing its documented templates unchanged.

Parsing follows the shared defensive convention: unexpected shapes become
FAILED results, missing fields stay None (missing field ≠ failure), and
only structural breakage downgrades status. Tests stay offline via the
same injectable runner contract as AgentReachProvider.
"""
from __future__ import annotations

import functools
import json
import re
from datetime import datetime, timezone
from typing import Any

from .agent_reach import (
    DEFAULT_TIMEOUT,
    Runner,
    _coerce_int,
    _envelope_error,
)
from .sources import (
    ContentSnapshot,
    EngagementSnapshot,
    SourceResult,
    SourceStatus,
)

VIDEO_COMMAND_TEMPLATE = ["bili", "video", "{video_id}", "--json"]

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"av(\d+)")


def extract_video_id(url: str | None) -> str | None:
    """Pull BV/av id out of any bilibili URL shape; None when not a video page."""
    if not url:
        return None
    match = _BV_RE.search(url)
    if match:
        return match.group(1)
    match = _AV_RE.search(url)
    return f"av{match.group(1)}" if match else None


def _degenerate_content(platform: str) -> ContentSnapshot:
    return ContentSnapshot(content_id="", platform=platform, url="", text="")


class BilibiliCliReader:
    """Full-platform bilibili reader: metadata, author, comments, engagement."""

    def __init__(
        self,
        *,
        inner=None,
        runner: Runner | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        video_command: list[str] | None = None,
    ) -> None:
        self.inner = inner
        self.video_command = list(video_command or VIDEO_COMMAND_TEMPLATE)
        # 与 inner 共用一个 runner（测试注入一份假 runner 即可覆盖全部通道）。
        self._video_runner: Runner = (
            runner
            or getattr(self.inner, "runner", None)
            or _default_runner(timeout)
        )
        if self.inner is None:
            from .agent_reach import AgentReachProvider

            self.inner = AgentReachProvider(
                runner=self._video_runner, timeout=timeout
            )

    def _failed(self, error: str, observed_at: datetime, items):
        return SourceResult(
            source="bilibili-cli",
            platform="bilibili",
            status=SourceStatus.FAILED,
            items=items,
            error=error,
            observed_at=observed_at,
        )

    async def _fetch_video(self, video_id: str) -> tuple[SourceStatus, str, dict[str, Any] | None]:
        """Run the metadata template once; (status, error, data-dict)."""
        observed_at = datetime.now(timezone.utc)
        cmd: list[str] = []
        try:
            for part in self.video_command:
                cmd.append(part.format(video_id=video_id))
        except (KeyError, ValueError) as exc:
            return SourceStatus.FAILED, f"video template has invalid placeholders: {exc}", None
        returncode, stdout, stderr = await self._video_runner(cmd)
        if returncode != 0:
            return (
                SourceStatus.FAILED,
                stderr.strip() or f"exit code {returncode}",
                None,
            )
        try:
            payload = json.loads(stdout)
        except ValueError:
            return SourceStatus.FAILED, "video output is not JSON", None
        env_error = _envelope_error(payload)
        if env_error is not None:
            return SourceStatus.FAILED, env_error, None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return SourceStatus.FAILED, "unexpected payload: no data object", None
        return SourceStatus.OK, "", data

    async def read(self, url: str, *, platform: str = "bilibili") -> SourceResult[ContentSnapshot]:
        video_id = extract_video_id(url)
        if video_id is None:
            # 非视频页退回通用正文读取：能力降级，不伪造元数据。
            return await self.inner.read(url, platform=platform)
        status, error, data = await self._fetch_video(video_id)
        if status is not SourceStatus.OK:
            return self._failed(error, datetime.now(timezone.utc), _degenerate_content(platform))
        owner = data.get("owner")
        mid = owner.get("mid") if isinstance(owner, dict) else None
        pubdate = _coerce_int(data.get("pubdate"))
        title = str(data.get("title") or "").strip()
        text = str(data.get("desc") or "").strip() or title
        return SourceResult(
            source="bilibili-cli",
            platform=platform,
            status=SourceStatus.OK,
            items=ContentSnapshot(
                content_id=str(data.get("bvid") or video_id),
                platform=platform,
                url=url,
                title=title or None,
                text=text,
                author_id=str(mid) if mid is not None else None,
                published_at=(
                    datetime.fromtimestamp(pubdate, tz=timezone.utc)
                    if pubdate is not None
                    else None
                ),
            ),
            observed_at=datetime.now(timezone.utc),
        )

    async def engagement(self, content_id: str, *, platform: str) -> SourceResult[EngagementSnapshot]:
        degenerate = EngagementSnapshot(content_id="", platform=platform)
        status, error, data = await self._fetch_video(content_id)
        if status is not SourceStatus.OK:
            return self._failed(error, datetime.now(timezone.utc), degenerate)
        stat = data.get("stat")
        if not isinstance(stat, dict):
            return self._failed(
                "unexpected payload: no stat object",
                datetime.now(timezone.utc),
                degenerate,
            )
        return SourceResult(
            source="bilibili-cli",
            platform=platform,
            status=SourceStatus.OK,
            items=EngagementSnapshot(
                content_id=content_id,
                platform=platform,
                views=_coerce_int(stat.get("view")),
                likes=_coerce_int(stat.get("like")),
                comments=_coerce_int(stat.get("reply")),
                shares=_coerce_int(stat.get("share")),
                saves=_coerce_int(stat.get("favorite")),
                observed_at=datetime.now(timezone.utc),
            ),
            observed_at=datetime.now(timezone.utc),
        )

    async def comments(self, content_id: str, *, platform: str):
        return await self.inner.comments(content_id, platform=platform)

    async def author(self, author_id: str, *, platform: str):
        return await self.inner.author(author_id, platform=platform)


def _default_runner(timeout: float):
    from .agent_reach import _subprocess_runner

    return functools.partial(_subprocess_runner, timeout=timeout)
