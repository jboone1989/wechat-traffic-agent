"""Authenticated live bridge to Ferro's local Chromium CDP target.

The Chrome DevTools endpoint remains bound to localhost. Operator clients only
see a constrained same-origin WebSocket carrying screencast frames and a small
input-event allowlist.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable
from urllib.parse import urlparse
import urllib.request

from websockets.asyncio.client import connect as ws_connect


_VIEWPORT_WIDTH = 1120
_VIEWPORT_HEIGHT = 780
_ALLOWED_KEYS = {
    "Enter": ("Enter", "Enter"),
    "Tab": ("Tab", "Tab"),
    "Backspace": ("Backspace", "Backspace"),
    "Escape": ("Escape", "Escape"),
    "ArrowUp": ("ArrowUp", "ArrowUp"),
    "ArrowDown": ("ArrowDown", "ArrowDown"),
}


class BrowserStreamUnavailable(RuntimeError):
    pass


def _cdp_target_websocket(
    target_id: str,
    cdp_url: str = "http://127.0.0.1:9601",
) -> str:
    base = urlparse(str(cdp_url))
    if (
        base.scheme != "http"
        or base.hostname not in {"127.0.0.1", "localhost"}
        or base.port is None
        or base.port not in {*range(9400, 9700)}
    ):
        raise BrowserStreamUnavailable("unsafe CDP endpoint")
    root = f"http://127.0.0.1:{base.port}"
    try:
        with urllib.request.urlopen(root + "/json/list", timeout=2) as response:
            pages = json.load(response)
    except Exception as exc:
        raise BrowserStreamUnavailable("local CDP endpoint unavailable") from exc
    for page in pages if isinstance(pages, list) else []:
        if str(page.get("id") or "") != str(target_id):
            continue
        ws_url = str(page.get("webSocketDebuggerUrl") or "")
        parsed = urlparse(ws_url)
        if (
            parsed.scheme == "ws"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port == base.port
        ):
            return ws_url
        raise BrowserStreamUnavailable("unsafe CDP websocket endpoint")
    raise BrowserStreamUnavailable("browser target no longer exists")
class BrowserSessionStream:
    def __init__(self, control) -> None:
        self.control = control
        self._command_id = 0
        self._send_lock = asyncio.Lock()
        self._viewport_width = _VIEWPORT_WIDTH
        self._viewport_height = _VIEWPORT_HEIGHT

    def target_id(self) -> str:
        value = str(self.control.target_id() or "")
        if not value:
            raise BrowserStreamUnavailable("browser target unavailable")
        return value

    def cdp_url(self) -> str:
        getter = getattr(self.control, "cdp_url", None)
        if callable(getter):
            value = str(getter() or "")
            if value:
                return value
        return "http://127.0.0.1:9601"

    async def _send(self, cdp, method: str, **params: Any) -> int:
        async with self._send_lock:
            self._command_id += 1
            command_id = self._command_id
            await cdp.send(json.dumps({
                "id": command_id,
                "method": method,
                "params": params,
            }))
            return command_id

    async def _send_status(self, client) -> None:
        try:
            status = await asyncio.to_thread(self.control.status)
        except Exception:
            status = {"running": True}
        await client.send_json({
            "type": "status",
            "url": str(status.get("url") or ""),
            "title": str(status.get("title") or ""),
            "authenticated": bool(
                status.get("authenticated", status.get("x_authenticated"))
            ),
            "identity": str(
                status.get("identity") or status.get("account_identity") or ""
            ),
            "width": _VIEWPORT_WIDTH,
            "height": _VIEWPORT_HEIGHT,
        })

    async def _handle_input(self, client, cdp) -> None:
        while True:
            message = await client.receive_json()
            if not isinstance(message, dict):
                continue
            kind = str(message.get("type") or "")
            if kind == "ping":
                await client.send_json({"type": "pong"})
                continue
            if kind == "click":
                x = float(message.get("x_ratio"))
                y = float(message.get("y_ratio"))
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    continue
                for event in ("mousePressed", "mouseReleased"):
                    await self._send(
                        cdp,
                        "Input.dispatchMouseEvent",
                        type=event,
                        x=x * self._viewport_width,
                        y=y * self._viewport_height,
                        button="left",
                        clickCount=1,
                    )
                continue
            if kind == "wheel":
                dx = max(-1200.0, min(1200.0, float(message.get("delta_x") or 0)))
                dy = max(-1200.0, min(1200.0, float(message.get("delta_y") or 0)))
                await self._send(
                    cdp,
                    "Input.dispatchMouseEvent",
                    type="mouseWheel",
                    x=float(message.get("x_ratio") or 0.5) * self._viewport_width,
                    y=float(message.get("y_ratio") or 0.5) * self._viewport_height,
                    deltaX=dx,
                    deltaY=dy,
                )
                continue
            if kind == "text":
                value = str(message.get("text") or "")
                if not value or len(value) > 1024 or "\r" in value or "\n" in value:
                    continue
                await self._send(cdp, "Input.insertText", text=value)
                continue
            if kind == "click_text":
                value = str(message.get("text") or "").strip()
                if not value or len(value) > 120:
                    continue
                expression = """(() => {
                  const wanted = %s.toLowerCase();
                  const nodes = [...document.querySelectorAll(
                    'button,a,[role="button"],input[type="button"],input[type="submit"]'
                  )];
                  const node = nodes.find(el => {
                    const text = (
                      el.innerText || el.textContent || el.value || ''
                    ).trim().replace(/\\s+/g, ' ').toLowerCase();
                    return text === wanted && Boolean(el.getClientRects().length);
                  });
                  if (!node) return false;
                  node.click();
                  return true;
                })()""" % json.dumps(value)
                await self._send(
                    cdp,
                    "Runtime.evaluate",
                    expression=expression,
                    returnByValue=True,
                )
                continue
            if kind == "key":
                key = str(message.get("key") or "")
                if key not in _ALLOWED_KEYS:
                    continue
                key_name, code = _ALLOWED_KEYS[key]
                for event in ("keyDown", "keyUp"):
                    await self._send(
                        cdp,
                        "Input.dispatchKeyEvent",
                        type=event,
                        key=key_name,
                        code=code,
                    )
                continue

    async def _pump_frames(
        self, client, cdp, authorize: Callable[[], bool] | None = None
    ) -> None:
        await self._send_status(client)
        await self._send(cdp, "Page.bringToFront")
        await self._send(cdp, "Page.enable")
        await self._send(
            cdp,
            "Page.startScreencast",
            format="jpeg",
            quality=68,
            maxWidth=_VIEWPORT_WIDTH,
            maxHeight=_VIEWPORT_HEIGHT,
            everyNthFrame=1,
        )
        frames = 0
        next_auth_check = time.monotonic() + 5
        async for raw in cdp:
            if authorize is not None and time.monotonic() >= next_auth_check:
                if not authorize():
                    raise BrowserStreamUnavailable("operator session expired")
                next_auth_check = time.monotonic() + 5
            message = json.loads(raw)
            if message.get("method") != "Page.screencastFrame":
                continue
            params = message.get("params") or {}
            data = str(params.get("data") or "")
            session_id = params.get("sessionId")
            if not data or session_id is None:
                continue
            metadata = params.get("metadata") or {}
            device_width = metadata.get("deviceWidth")
            device_height = metadata.get("deviceHeight")
            if isinstance(device_width, (int, float)) and device_width > 0:
                self._viewport_width = float(device_width)
            if isinstance(device_height, (int, float)) and device_height > 0:
                self._viewport_height = float(device_height)
            frames += 1
            await client.send_json({
                "type": "frame",
                "data": data,
                "metadata": metadata,
                "sequence": frames,
            })
            await self._send(
                cdp,
                "Page.screencastFrameAck",
                sessionId=session_id,
            )
            if frames % 15 == 0:
                await self._send_status(client)
    async def run(
        self, client, *, authorize: Callable[[], bool] | None = None
    ) -> None:
        ws_url = await asyncio.to_thread(
            _cdp_target_websocket,
            self.target_id(),
            self.cdp_url(),
        )
        try:
            async with ws_connect(
                ws_url,
                origin=None,
                max_size=8 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            ) as cdp:
                input_task = asyncio.create_task(
                    self._handle_input(client, cdp)
                )
                frame_task = asyncio.create_task(
                    self._pump_frames(client, cdp, authorize)
                )
                done, pending = await asyncio.wait(
                    {input_task, frame_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        raise exc
        except BrowserStreamUnavailable:
            raise
        except Exception as exc:
            raise BrowserStreamUnavailable(type(exc).__name__) from exc


__all__ = [
    "BrowserSessionStream",
    "BrowserStreamUnavailable",
]
