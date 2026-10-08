"""Canonical content hashing shared by editorial and publishing adapters."""
from __future__ import annotations

import hashlib
import json


def digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
