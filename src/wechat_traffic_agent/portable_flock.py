"""Cross-platform advisory file locking.

POSIX delegates to fcntl.flock directly, keeping server behavior identical.
Windows maps the same operations onto msvcrt.locking over a fixed byte
range and converts non-blocking conflicts into BlockingIOError so existing
callers keep their error handling unchanged.
"""
from __future__ import annotations

import sys

if not sys.platform.startswith("win"):
    import fcntl

    LOCK_EX = fcntl.LOCK_EX
    LOCK_NB = fcntl.LOCK_NB
    LOCK_UN = fcntl.LOCK_UN
    flock = fcntl.flock

else:
    import msvcrt

    # Self-consistent constants; never mixed with fcntl on this platform.
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8
    _REGION = 0x000FFFFF  # fixed byte range: unlock must match the lock range

    def flock(fd, operation):
        fileno = fd.fileno() if hasattr(fd, "fileno") else fd
        try:
            if operation & LOCK_UN:
                msvcrt.locking(fileno, msvcrt.LK_UNLCK, _REGION)
                return
            if operation & LOCK_EX:
                mode = msvcrt.LK_NBLCK if operation & LOCK_NB else msvcrt.LK_LOCK
                msvcrt.locking(fileno, mode, _REGION)
        except OSError as exc:
            if operation & LOCK_NB:
                raise BlockingIOError(*exc.args) from exc
            raise

__all__ = ["flock", "LOCK_EX", "LOCK_NB", "LOCK_UN"]
