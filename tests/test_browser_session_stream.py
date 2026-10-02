from __future__ import annotations

import asyncio
import json

from wechat_traffic_agent.browser_session_stream import (
    BrowserSessionStream,
    _cdp_target_websocket,
)
import wechat_traffic_agent.browser_session_stream as stream_module


class FakeControl:
    def target_id(self):
        return "target-1"

    def status(self):
        return {
            "url": "https://x.com/i/flow/login",
            "title": "X",
            "x_authenticated": False,
            "account_identity": "",
        }


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeCDP:
    def __init__(self, incoming=None):
        self.sent = []
        self.incoming = list(incoming or [])

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return json.dumps(self.incoming.pop(0))
def test_screencast_pump_sends_live_frame_and_ack():
    client = FakeClient()
    cdp = FakeCDP([{
        "method": "Page.screencastFrame",
        "params": {
            "data": "ZmFrZS1qcGVn",
            "sessionId": 7,
            "metadata": {
                "timestamp": 1.0,
                "deviceWidth": 800,
                "deviceHeight": 600,
            },
        },
    }])
    stream = BrowserSessionStream(FakeControl())

    asyncio.run(stream._pump_frames(client, cdp, lambda: True))

    assert client.sent[0]["type"] == "status"
    assert client.sent[1]["type"] == "frame"
    assert client.sent[1]["data"] == "ZmFrZS1qcGVn"
    methods = [row["method"] for row in cdp.sent]
    assert methods == [
        "Page.bringToFront",
        "Page.enable",
        "Page.startScreencast",
        "Page.screencastFrameAck",
    ]
    assert cdp.sent[-1]["params"]["sessionId"] == 7
    assert stream._viewport_width == 800
    assert stream._viewport_height == 600


def test_live_input_is_constrained_to_allowed_cdp_events():
    messages = [
        {"type": "click", "x_ratio": 0.5, "y_ratio": 0.25},
        {"type": "wheel", "x_ratio": 0.5, "y_ratio": 0.5, "delta_y": 120},
        {"type": "text", "text": "user@example.com"},
        {"type": "click_text", "text": "Try another method"},
        {"type": "key", "key": "Enter"},
    ]

    class InputClient:
        async def receive_json(self):
            if messages:
                return messages.pop(0)
            await asyncio.Event().wait()

        async def send_json(self, payload):
            pass

    async def run():
        cdp = FakeCDP()
        stream = BrowserSessionStream(FakeControl())
        task = asyncio.create_task(stream._handle_input(InputClient(), cdp))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return cdp.sent

    sent = asyncio.run(run())
    methods = [row["method"] for row in sent]
    assert methods.count("Input.dispatchMouseEvent") == 3
    assert "Input.insertText" in methods
    assert "Runtime.evaluate" in methods
    assert methods.count("Input.dispatchKeyEvent") == 2
    assert all("webSocketDebuggerUrl" not in json.dumps(row) for row in sent)


def test_click_uses_live_screencast_viewport_dimensions():
    messages = [{"type": "click", "x_ratio": 0.5, "y_ratio": 0.25}]

    class InputClient:
        async def receive_json(self):
            if messages:
                return messages.pop(0)
            await asyncio.Event().wait()

    async def run():
        cdp = FakeCDP()
        stream = BrowserSessionStream(FakeControl())
        stream._viewport_width = 800
        stream._viewport_height = 600
        task = asyncio.create_task(stream._handle_input(InputClient(), cdp))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return cdp.sent

    sent = asyncio.run(run())
    clicks = [row for row in sent if row["method"] == "Input.dispatchMouseEvent"]
    assert len(clicks) == 2
    assert all(row["params"]["x"] == 400 for row in clicks)
    assert all(row["params"]["y"] == 150 for row in clicks)


def test_status_supports_generic_non_x_authentication_shape():
    class CortexControl:
        def status(self):
            return {
                "url": "https://chatgpt.com/",
                "title": "ChatGPT",
                "authenticated": True,
                "identity": "ChatGPT",
            }

    client = FakeClient()
    asyncio.run(BrowserSessionStream(CortexControl())._send_status(client))
    assert client.sent == [{
        "type": "status",
        "url": "https://chatgpt.com/",
        "title": "ChatGPT",
        "authenticated": True,
        "identity": "ChatGPT",
        "width": 1120,
        "height": 780,
    }]


def test_cortex_cdp_port_range_is_allowed(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    pages = [{
        "id": "cortex-target",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9500/devtools/page/cortex-target",
    }]

    def fake_urlopen(url, timeout=2):
        assert url == "http://127.0.0.1:9500/json/list"
        response = Response()
        stream_module.json.load = lambda _response: pages
        return response

    monkeypatch.setattr(stream_module.urllib.request, "urlopen", fake_urlopen)
    assert _cdp_target_websocket(
        "cortex-target",
        "http://127.0.0.1:9500",
    ) == "ws://127.0.0.1:9500/devtools/page/cortex-target"
