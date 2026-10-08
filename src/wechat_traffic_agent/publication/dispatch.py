"""Publication receipt and channel distribution are separate from website rendering.

The website only emits a PublicationEvent; this service owns downstream channel
tasks. Swap this sink for a message-bus adapter if deployed as a separate process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from wechat_traffic_agent.editorial.fingerprint import digest
from .event_store import PublicationEventStore
from .models import DistributionTask, PublicationEvent


class PublicationSink(Protocol):
    def __call__(self, event: PublicationEvent) -> None: ...


def default_distribution_channels() -> tuple[str, ...]:
    from wechat_traffic_agent.platforms.binance_square import square_enabled
    return ("x", "binance_square") if square_enabled() else ("x",)


@dataclass
class DurablePublicationDispatch:
    store: PublicationEventStore
    channels: tuple[str, ...] = field(default_factory=default_distribution_channels)

    def record(self, event: PublicationEvent) -> None:
        self.store.save_event(event)
        for channel in self.channels:
            self.store.enqueue_distribution(DistributionTask(
                distribution_id=digest({"event_id": event.event_id, "channel": channel}),
                event_id=event.event_id,
                channel=channel,
            ))
