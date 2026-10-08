"""Read-only public agent status and activity stream endpoints."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

from starlette.responses import JSONResponse, StreamingResponse

from .catalog import article_catalog


class PublicStatusHandlers:
    def __init__(self, *, state_root, application, mind, wakeful, started_at,
                 poll_seconds, snapshot, degraded, activity_reader, snapshot_timeout):
        self.state_root, self.application, self.mind = state_root, application, mind
        self.wakeful, self.started_at, self.poll_seconds = wakeful, started_at, poll_seconds
        self.snapshot_builder, self.degraded = snapshot, degraded
        self.activity_reader, self.snapshot_timeout = activity_reader, snapshot_timeout

    def _snapshot(self):
        return self.snapshot_builder(
            self.state_root, self.application, self.mind,
            poll_seconds=self.poll_seconds, service_started_at=self.started_at,
            wakeful=self.wakeful,
        )

    async def status(self, request):
        if request.query_params.get("content") == "articles":
            try:
                catalog = article_catalog(self.state_root)
            except (FileNotFoundError, ValueError) as exc:
                return JSONResponse(
                    {"error": "article_database_unavailable", "detail": str(exc)},
                    status_code=503, headers={"Cache-Control": "no-store"},
                )
            return JSONResponse(catalog, headers={"Cache-Control": "no-store"})
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(self._snapshot), timeout=self.snapshot_timeout,
            )
        except Exception as exc:
            payload = self.degraded(self.state_root, exc)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @staticmethod
    def _heartbeat(snapshot):
        activity = snapshot.get("activity") or {}
        wake = ((snapshot.get("life") or {}).get("presence") or {}).get("wakeful") or {}
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "run_state": wake.get("state") or activity.get("run_state"),
            "phase": activity.get("phase"), "last_phase": activity.get("last_phase"),
            "cycle": activity.get("cycle"), "current_focus": activity.get("current_focus"),
            "next_check_at": wake.get("next_wake_at") or activity.get("next_check_at"),
            "next_wake_reason": wake.get("next_wake_reason"),
        }

    async def _events(self, request):
        sent = set()
        yield "retry: 1000\n\n"
        while True:
            if await request.is_disconnected():
                break
            for event in self.activity_reader(self.state_root, limit=80):
                event_id = str(event.get("event_id") or "")
                if not event_id or event_id in sent:
                    continue
                sent.add(event_id)
                payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {event_id}\nevent: activity\ndata: {payload}\n\n"
            pulse = self._heartbeat(self._snapshot())
            yield "event: heartbeat\ndata: " + json.dumps(
                pulse, ensure_ascii=False, separators=(",", ":"),
            ) + "\n\n"
            await asyncio.sleep(1.0)

    async def stream(self, request):
        return StreamingResponse(
            self._events(request), media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no", "Connection": "keep-alive",
            },
        )
