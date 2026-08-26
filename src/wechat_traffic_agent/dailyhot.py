"""DailyHotApi adapter: cheap best-effort hot feeds.

DailyHotApi is a best-effort source (60-min cache, some backends scrape
pages). This adapter parses defensively: unexpected shapes become FAILED
results, missing fields stay None, and one platform's failure never
aborts a scan.
"""
from __future__ import annotations

from datetime import datetime, timezone

import math

import httpx

from .sources import HotItem, SourceResult, SourceStatus

DEFAULT_BASE_URL = "http://localhost:6688"


def _coerce_hot(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


class DailyHotApiProvider:
    """GET /{platform} against a DailyHotApi deployment (Docker default port 6688)."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    async def fetch(self, platform: str) -> SourceResult[tuple[HotItem, ...]]:
        observed_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, transport=self._transport
            ) as client:
                response = await client.get(f"/{platform}")
                response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return SourceResult(
                source="dailyhot",
                platform=platform,
                status=SourceStatus.FAILED,
                items=(),
                error=f"{type(exc).__name__}: {exc}",
                observed_at=observed_at,
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return SourceResult(
                source="dailyhot",
                platform=platform,
                status=SourceStatus.FAILED,
                items=(),
                error="unexpected payload: data is not a list",
                observed_at=observed_at,
            )
        items: list[HotItem] = []
        for rank, row in enumerate(data, start=1):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            items.append(
                HotItem(
                    source_id=str(row.get("id") or rank),  # 真实载荷恒有 id；无 id 时以原始位置兜底
                    platform=platform,
                    title=title,
                    url=row.get("url"),
                    rank=rank,
                    observed_at=observed_at,
                    raw_hot_value=_coerce_hot(row.get("hot")),
                )
            )
        # 空列表也是 OK：平台活着但当前无热点，与 FAILED 区分
        return SourceResult(
            source="dailyhot",
            platform=platform,
            status=SourceStatus.OK,
            items=tuple(items),
            observed_at=observed_at,
        )
