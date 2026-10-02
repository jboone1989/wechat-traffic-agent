"""Verify authenticated noVNC proxy against the real persistent X desktop."""
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from starlette.testclient import TestClient
from wechat_traffic_agent.live_agent import LiveAgentService

os.environ["CONTENT_OPERATOR_CONSOLE_ENABLED"] = "1"
os.environ["CONTENT_OPERATOR_PUBLIC_URL"] = "http://testserver"
runtime_path = Path.home()/".local/state/agent-runtime/browser/profiles/x/runtime.json"
before = json.loads(runtime_path.read_text())

def frame(client):
    with client.websocket_connect("/operator/browser/x/websockify", subprotocols=["binary"]) as ws:
        buffer = bytearray()
        def read(n):
            while len(buffer) < n:
                buffer.extend(ws.receive_bytes())
            data = bytes(buffer[:n]);del buffer[:n]
            return data
        version = read(12)
        assert version.startswith(b"RFB "), version
        ws.send_bytes(b"RFB 003.008\n")
        count = read(1)[0]
        methods = read(count)
        assert 1 in methods, methods
        ws.send_bytes(b"\x01")
        assert read(4) == b"\x00"*4
        ws.send_bytes(b"\x01")
        init = read(24)
        width, height = struct.unpack("!HH", init[:4])
        name = read(struct.unpack("!I", init[20:24])[0])
        ws.send_bytes(struct.pack("!BBHi", 2, 0, 1, 0)) # raw framebuffer encoding
        ws.send_bytes(struct.pack("!BBHHHH", 3, 0, 0, 0, min(64,width), min(64,height)))
        header = read(4)
        assert header[0] == 0 and struct.unpack("!H",header[2:])[0] > 0, header
        rect = read(12)
        x,y,w,h,encoding = struct.unpack("!HHHHi", rect)
        assert encoding == 0 and w*h > 0, rect
        pixels = read(w*h*(init[4]//8))
        return {"version":version.decode().strip(),"desktop":[width,height],"frame_bytes":len(pixels)}

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    def app():
        return LiveAgentService(state_root=root,application=None,backend=None,mind=None)._app()
    with TestClient(app()) as client:
        assert client.get("/operator/browser/x/vnc.html").status_code == 401
        token = (root/"secrets/operator-console.token").read_text().strip()
        assert client.post("/operator/api/session",headers={"X-Operator-Request":"1"},json={"token":token}).status_code == 200
        opened = client.post("/operator/api/accounts/x/connect",headers={"X-Operator-Request":"1"},json={})
        assert opened.status_code == 200, opened.text
        html = client.get(opened.json()["browser"]["takeover_url"])
        assert html.status_code == 200 and "noVNC" in html.text
        for asset in ["app/ui.js","core/rfb.js","app/styles/base.css","mandatory.json"]:
            assert client.get("/operator/browser/x/"+asset).status_code == 200, asset
        print(json.dumps({"first_open":frame(client),"reopen":frame(client),"transport":opened.json()["browser"]["transport"]}))
        cookies = dict(client.cookies)
    with TestClient(app()) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get("/operator/api/accounts/x/browser/status").status_code == 200
        print(json.dumps({"app_restart":frame(restarted)}))
after = json.loads(runtime_path.read_text())
assert before == after, (before,after)
print(json.dumps({"persistent_runtime_unchanged":True,"chrome_pid":after["chrome_pid"],"display":after["display"]}))
