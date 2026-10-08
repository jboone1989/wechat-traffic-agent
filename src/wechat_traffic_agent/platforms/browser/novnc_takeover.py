"""Authenticated noVNC access to Agent Runtime's existing persistent X desktop."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import httpx
from agent_runtime.integrations import PersistentBrowserTakeoverProvider
from websockets.asyncio.client import connect
from starlette.websockets import WebSocketDisconnect


class NoVNCUnavailable(RuntimeError):
    pass


class AgentRuntimeNoVNC:
    """Attach only: Agent Runtime's supervisor owns browser/profile lifecycle."""

    def __init__(self):
        self.provider = PersistentBrowserTakeoverProvider(
            state_root=Path.home() / ".local/state/agent-runtime"
        )

    def origin(self) -> str:
        status = self.provider.takeover_status("x")
        if not status.browser_running or not status.active or not status.url:
            raise NoVNCUnavailable("Agent Runtime X desktop/noVNC is unavailable")
        parts = urlsplit(status.url)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
            raise NoVNCUnavailable("Agent Runtime X noVNC has an invalid local endpoint")
        return f"http://127.0.0.1:{parts.port}"

    def connection(self) -> dict:
        self.origin()
        return {
            "transport": "NOVNC",
            "profile_id": "x",
            "takeover_url": "/operator/browser/x/vnc.html?" + urlencode({
                "autoconnect": "1", "resize": "scale", "reconnect": "1",
                "path": "operator/browser/x/websockify",
            }),
        }

    async def asset(self, path: str) -> tuple[int, bytes, str]:
        decoded = unquote(path)
        if not decoded or any(part in {".", ".."} for part in decoded.split("/")) or "\\" in decoded:
            raise ValueError("invalid noVNC asset path")
        origin = await asyncio.to_thread(self.origin)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.get(origin + "/" + path)
        except httpx.RequestError as exc:
            raise NoVNCUnavailable(f"Agent Runtime X noVNC asset failed: {exc}") from exc
        return response.status_code, response.content, response.headers.get("content-type", "application/octet-stream")

    async def relay(self, websocket, *, authorize):
        origin = await asyncio.to_thread(self.origin)
        upstream_url = origin.replace("http://", "ws://", 1) + "/websockify"
        async with connect(
            upstream_url, subprotocols=websocket.scope.get("subprotocols") or None,
            open_timeout=5, ping_interval=None, max_size=None,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def client_to_desktop():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if not authorize():
                        await websocket.close(code=4401)
                        return
                    payload = message.get("bytes")
                    if payload is None:
                        payload = message.get("text")
                    if payload is not None:
                        await upstream.send(payload)

            async def desktop_to_client():
                async for payload in upstream:
                    if not authorize():
                        await websocket.close(code=4401)
                        return
                    if isinstance(payload, bytes):
                        await websocket.send_bytes(payload)
                    else:
                        await websocket.send_text(payload)

            async def session_watch():
                while True:
                    await asyncio.sleep(5)
                    if not authorize():
                        await websocket.close(code=4401)
                        return

            tasks = [asyncio.create_task(fn()) for fn in (
                client_to_desktop, desktop_to_client, session_watch,
            )]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except WebSocketDisconnect:
                pass
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if websocket.application_state.name == "CONNECTED":
                    await websocket.close()
        # Closing a viewer never releases takeover or stops the browser.
