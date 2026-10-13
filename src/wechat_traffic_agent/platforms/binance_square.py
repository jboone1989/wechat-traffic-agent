"""Binance Square channel gates and draft limits; posting is delegated to omnipost."""
from __future__ import annotations

import os

MAX_POST_CHARS = 1500


def square_enabled() -> bool:
    return os.environ.get("CONTENT_BINANCE_SQUARE_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def square_writes_enabled() -> bool:
    return os.environ.get("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "").lower() in {"1", "true", "yes", "on"}
