"""Small process heartbeat primitive for independent Ferro workers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


class WorkerHeartbeat:
    def __init__(self, state_root: Path, worker_id: str) -> None:
        self.worker_id = str(worker_id).strip()
        self.path = Path(state_root) / "application" / "workers" / f"{self.worker_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *, status: str, detail: str | None = None) -> None:
        row = {
            "schema_version": "ferro-worker-heartbeat.v1",
            "worker_id": self.worker_id,
            "pid": os.getpid(),
            "status": str(status),
            "detail": detail,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


__all__ = ["WorkerHeartbeat"]
