"""Publication failures that must not be silently retried."""


class NonRetryablePublication(RuntimeError):
    """The outbox must retain this failed task for review, not resubmit it."""
