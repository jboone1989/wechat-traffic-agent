"""Small independent pulse for an external Ferro process guardian."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any


class FerroHeartbeat:
    def __init__(self, state_root: Path, *, interval_seconds: float = 10.0) -> None:
        self.path = Path(state_root) / "application" / "ferro-heartbeat.json"
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.task: str | None = None

    def pulse(self, status: str = "RUNNING") -> dict[str, Any]:
        payload = {
            "schema_version": "ferro-heartbeat.v1",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "status": status,
            "task": self.task,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return payload

    def start(self) -> None:
        if self._thread is not None:
            return
        self.pulse()
        self._thread = threading.Thread(target=self._run, name="ferro-guardian-heartbeat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.pulse()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.pulse("STOPPED")
