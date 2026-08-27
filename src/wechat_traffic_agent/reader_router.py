"""ReaderRouter: dispatch deep-fetch calls to the right per-platform reader.

PR7 production data plane. A scan touches several platforms whose deep
capabilities differ wildly (bilibili has metadata/comments/author via
bilibili-cli; other URLs fall back to generic Jina reading). The router
keeps one ``reader`` object for ViralRadar/ViralPatternAnalyzer while
routing every call by platform string.

Ground rules inherited from the source contracts:

1. live 模式绝不回退 fixture —— 未配置的能力按 UNAVAILABLE 暴露，
   由上层把 sub-score 剔除权重，绝不拿测试数据填空位;
2. routing 是纯转发：状态、错误信息、items 形状全部原样透传。
"""
from __future__ import annotations


class ReaderRouter:
    """Platform-keyed dispatcher satisfying the reader protocol family."""

    def __init__(self, *, default, routes: dict[str, object] | None = None) -> None:
        self.default = default
        self.routes: dict[str, object] = dict(routes or {})

    def _pick(self, platform: str):
        return self.routes.get(platform, self.default)

    async def read(self, url: str, *, platform: str = "generic_web"):
        return await self._pick(platform).read(url, platform=platform)

    async def comments(self, content_id: str, *, platform: str):
        return await self._pick(platform).comments(content_id, platform=platform)

    async def author(self, author_id: str, *, platform: str):
        return await self._pick(platform).author(author_id, platform=platform)

    async def engagement(self, content_id: str, *, platform: str):
        return await self._pick(platform).engagement(content_id, platform=platform)
