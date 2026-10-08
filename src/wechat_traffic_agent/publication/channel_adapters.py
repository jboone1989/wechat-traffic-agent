"""Connect independent platform adapters to Ferro's existing publication outbox."""
from __future__ import annotations

from pathlib import Path

from wechat_traffic_agent.platforms.binance_square import (
    publish_square, square_enabled, square_writes_enabled,
)


def active_distribution_adapters(state_root: Path) -> dict:
    """Only claim tasks whose platform is currently ready to accept writes."""
    adapters = {}
    from wechat_traffic_agent.x_browser import x_browser_writes_enabled
    if x_browser_writes_enabled():
        from wechat_traffic_agent.publication.x_distribution import publish_x
        adapters["x"] = lambda event, task: publish_x(event, task, state_root=state_root)
    if square_enabled() and square_writes_enabled():
        adapters["binance_square"] = publish_square
    return adapters
