"""Retired website subscriber: website approval is not Square editorial approval.

The native SquareDraftWorker owns new posts. Keep old events and receipts for
reconciliation without consuming or backfilling website/X queues.
"""
from wechat_traffic_agent.publication.event_store import PublicationEventStore


class SquareEventConsumer:
    def __init__(self, state_root, *, publisher=None):
        self.store = PublicationEventStore(state_root)

    def enqueue_events(self, *, now=None):
        return 0

    def run_once(self, *, now=None):
        return None
