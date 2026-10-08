"""Public pages and observational read-only endpoints.

These handlers depend on narrow observability ports, not operator sessions or
content publishing. The Ferro server still hosts them in the same process.
"""
from __future__ import annotations

import asyncio
from starlette.responses import HTMLResponse, JSONResponse

from wechat_traffic_agent.channels.agent_live_page import live_agent_document
from wechat_traffic_agent.channels.agent_talk_page import talk_agent_document
from wechat_traffic_agent.channels.neural_life_page import neural_life_document
from wechat_traffic_agent.channels.brain_lab_page import brain_lab_document
from wechat_traffic_agent.channels.screeps_live_page import screeps_live_document
from wechat_traffic_agent.screeps_observatory import ScreepsObservatory
from wechat_traffic_agent.website.website_engagement_client import first_party_engagement_script


class PublicViews:
    def __init__(self, service, *, telemetry, circuit, engagement_enabled: bool):
        self.service = service
        self.telemetry = telemetry
        self.circuit = circuit
        self.engagement_enabled = engagement_enabled

    def _document(self, document: str):
        if self.engagement_enabled:
            document = document.replace(
                "</body>", first_party_engagement_script() + "</body>", 1,
            )
        return HTMLResponse(document, headers={"Cache-Control": "no-cache"})

    async def live_page(self, request):
        return self._document(live_agent_document())

    async def talk_page(self, request):
        return self._document(talk_agent_document())

    async def neural_life_page(self, request):
        return self._document(neural_life_document())

    async def brain_lab_page(self, request):
        return self._document(brain_lab_document())

    async def screeps_page(self, request):
        return self._document(screeps_live_document())

    async def screeps_status(self, request):
        include_frames = str(request.query_params.get("include") or "").strip().lower() in {
            "frames", "replay", "all",
        }
        payload = await asyncio.to_thread(
            ScreepsObservatory(self.service.state_root).snapshot,
            include_frames=include_frames,
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def neural_telemetry_status(self, request):
        detail = str(request.query_params.get("detail") or "full").strip().lower()
        payload = await asyncio.to_thread(
            self.telemetry.snapshot, compact=detail in {"compact", "mobile", "low"},
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def brain_lab_status(self, request):
        view = str(request.query_params.get("view") or "").strip().lower()
        if view == "live":
            return self._brain_live()
        if view == "telemetry":
            return await self.neural_telemetry_status(request)
        if view == "circuit-catalog":
            payload = await asyncio.to_thread(self.circuit.catalog)
        elif view == "circuit":
            detail = str(request.query_params.get("detail") or "full").strip().lower()
            compact = detail in {"compact", "mobile", "low"}
            selector = str(request.query_params.get("selector") or "").strip()
            payload = await asyncio.to_thread(self.circuit.circuit, selector, compact=compact)
        elif view == "neuron":
            neuron_id = str(request.query_params.get("id") or "").strip()
            payload = await asyncio.to_thread(self.circuit.neuron, neuron_id)
        else:
            return await self._brain_projection()
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    def _brain_live(self):
        wakeful = self.service.wakeful
        if wakeful is None or not hasattr(wakeful, "brain_lab_live_projection"):
            return JSONResponse({"error": "brain_lab_unavailable"}, status_code=503)
        return JSONResponse(
            dict(wakeful.brain_lab_live_projection()),
            headers={"Cache-Control": "no-store"},
        )

    async def _brain_projection(self):
        wakeful = self.service.wakeful
        if wakeful is None or not hasattr(wakeful, "brain_lab_projection"):
            return JSONResponse({"error": "brain_lab_unavailable"}, status_code=503)
        payload = dict(await asyncio.to_thread(wakeful.brain_lab_projection))
        if hasattr(wakeful, "dream_projection"):
            payload["dreaming"] = await asyncio.to_thread(wakeful.dream_projection)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
