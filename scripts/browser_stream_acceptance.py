"""Verify real X CDP frame delivery without account input or publication."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from websockets.asyncio.client import connect
from wechat_traffic_agent.browser_session_stream import _cdp_target_websocket

async def probe(session: str) -> None:
    state = json.loads((Path.home() / ".local/state/agent-runtime/browser-harness" / (session + ".json")).read_text())
    url = _cdp_target_websocket(state["target_id"], state["browser_binding"])
    async with connect(url, origin=None, max_size=8 * 1024 * 1024) as cdp:
        command_id = 0
        async def send(method, **params):
            nonlocal command_id
            command_id += 1
            await cdp.send(json.dumps({"id": command_id, "method": method, "params": params}))
            return command_id
        async def collect(label):
            deadline = time.monotonic() + 5
            frames = 0
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(cdp.recv(), deadline - time.monotonic())
                except asyncio.TimeoutError:
                    break
                row = json.loads(raw)
                if "id" in row:
                    print(json.dumps({"phase": label, "command_id": row["id"], "error": row.get("error"), "result": row.get("result")}), flush=True)
                if row.get("method") == "Page.screencastFrame":
                    p = row["params"]
                    frames += 1
                    print(json.dumps({"phase": label, "event": "FRAME", "encoded_bytes": len(p.get("data", "")), "metadata": p.get("metadata")}), flush=True)
                    await send("Page.screencastFrameAck", sessionId=p["sessionId"])
            print(json.dumps({"phase": label, "frame_count": frames}), flush=True)
        print(json.dumps({"target_id": state["target_id"], "browser_binding": state["browser_binding"]}), flush=True)
        await send("Page.enable")
        await send("Runtime.evaluate", expression="({url:location.href, visibility:document.visibilityState, title:document.title})", returnByValue=True)
        await send("Page.startScreencast", format="jpeg", quality=68, maxWidth=1120, maxHeight=780, everyNthFrame=1)
        await collect("before_activation")
        await send("Page.bringToFront")
        await collect("after_activation")
        await send("Page.stopScreencast")
        print(json.dumps({"status": "DIAGNOSTIC_COMPLETE"}), flush=True)

async def verify_bridge(session: str) -> None:
    from base64 import b64decode
    from wechat_traffic_agent.browser_session_stream import BrowserSessionStream
    state = json.loads((Path.home() / ".local/state/agent-runtime/browser-harness" / (session + ".json")).read_text())
    ws_url = _cdp_target_websocket(state["target_id"], state["browser_binding"])
    class Control:
        def status(self):
            return {"url": "https://x.com/", "title": "X", "x_authenticated": False}
    class Client:
        def __init__(self):
            self.frames = 0
            self.jpeg_frames = 0
            self.bytes = 0
        async def send_json(self, row):
            if row.get("type") == "frame":
                data = b64decode(row["data"], validate=True)
                self.frames += 1
                self.jpeg_frames += int(data.startswith(bytes([255, 216, 255])))
                self.bytes += len(data)
    client = Client()
    async with connect(ws_url, origin=None, max_size=8 * 1024 * 1024) as cdp:
        try:
            await asyncio.wait_for(BrowserSessionStream(Control())._pump_frames(client, cdp), timeout=5)
        except asyncio.TimeoutError:
            pass
    result = {"status": "PASS" if client.frames and client.frames == client.jpeg_frames else "FAIL",
              "frame_count": client.frames, "valid_jpeg_frames": client.jpeg_frames, "jpeg_bytes": client.bytes,
              "browser_binding": state["browser_binding"]}
    print(json.dumps(result), flush=True)
    if result["status"] != "PASS":
        raise RuntimeError("real bridge delivered no valid JPEG frames")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--session-id", default="ferro-operator-x")
    parser.add_argument("--mode", choices=["diagnose", "bridge"], default="diagnose")
    args=parser.parse_args()
    asyncio.run(verify_bridge(args.session_id) if args.mode == "bridge" else probe(args.session_id))
if __name__ == "__main__":
    main()
