"""Volatile cross-worker observations: no durable archive, at most one hour."""
from hashlib import sha256
from pathlib import Path
import time


def live_cache_root(state_root):
    identity = sha256(str(Path(state_root).resolve()).encode()).hexdigest()[:20]
    root = Path("/dev/shm") / ("ferro-live-" + identity)
    if root.exists():
        for path in root.rglob("*.json"):
            try:
                if time.time() - path.stat().st_mtime > 3600:
                    path.unlink()
            except FileNotFoundError:
                pass
    return root
