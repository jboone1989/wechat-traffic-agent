from __future__ import annotations

import asyncio
import json

from wechat_traffic_agent.browser_session_stream import BrowserSessionStream


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
            "metadata": {"timestamp": 1.0},
        },
    }])
    stream = BrowserSessionStream(FakeControl())

    asyncio.run(stream._pump_frames(client, cdp, lambda: True))

    assert client.sent[0]["type"] == "status"
    assert client.sent[1]["type"] == "frame"
    assert client.sent[1]["data"] == "ZmFrZS1qcGVn"
    methods = [row["method"] for row in cdp.sent]
    assert methods == [
        "Page.enable",
        "Page.startScreencast",
        "Page.screencastFrameAck",
    ]
    assert cdp.sent[-1]["params"]["sessionId"] == 7


def test_live_input_is_constrained_to_allowed_cdp_events():
    messages = [
        {"type": "click", "x_ratio": 0.5, "y_ratio": 0.25},
        {"type": "wheel", "x_ratio": 0.5, "y_ratio": 0.5, "delta_y": 120},
        {"type": "text", "text": "user@example.com"},
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
    assert methods.count("Input.dispatchKeyEvent") == 2
    assert all("webSocketDebuggerUrl" not in json.dumps(row) for row in sent)
