"""Governed external-source contracts and adapters for Content Agent."""

from .contracts import (
    AuthorReader, AuthorSnapshot, Comment, CommentReader, ContentReader,
    ContentSnapshot, EngagementReader, EngagementSnapshot, HotFeedProvider,
    HotItem, SourceResult, SourceStatus,
)
from .trading_network import (
    CAP_TRADING_PUBLIC_RESEARCH,
    TradingResearchEnvelope,
    TradingResearchItem,
    TradingResearchNetworkSource,
)

__all__ = [
    "AuthorReader", "AuthorSnapshot", "Comment", "CommentReader",
    "ContentReader", "ContentSnapshot", "EngagementReader",
    "EngagementSnapshot", "HotFeedProvider", "HotItem", "SourceResult",
    "SourceStatus", "CAP_TRADING_PUBLIC_RESEARCH", "TradingResearchEnvelope",
    "TradingResearchItem", "TradingResearchNetworkSource",
]
