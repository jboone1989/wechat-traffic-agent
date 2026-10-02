"""Check deployed public operator noVNC without exposing operator credentials."""
import json
from pathlib import Path
import struct
from urllib.parse import urlsplit
import httpx
from websockets.sync.client import connect

base = "https://content.livingruntime.com"
runtime_path = Path.home()/".local/state/agent-runtime/browser/profiles/x/runtime.json"
before = json.loads(runtime_path.read_text())
token_path = Path("/home/ubuntu/content-agent-state/secrets/operator-console.token")
with httpx.Client(timeout=20, trust_env=False) as client:
    assert client.get(base+"/operator/browser/x/vnc.html").status_code == 401
    token = token_path.read_text().strip()
    login = client.post(base+"/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token})
    assert login.status_code == 200, login.status_code
    page = client.get(base+"/operator/")
    assert page.status_code == 200 and 'id="browserTakeoverFrame"' in page.text
    opened = client.post(base+"/operator/api/accounts/x/connect",headers={"X-Operator-Request":"1"},json={})
    assert opened.status_code == 200, opened.text
    url = opened.json()["browser"]["takeover_url"]
    html = client.get(base+url)
    assert html.status_code == 200 and "noVNC" in html.text
    assert html.headers.get("x-frame-options","").upper() != "DENY", dict(html.headers)
    mandatory = client.get(base+"/operator/browser/x/mandatory.json").json()
    assert mandatory["encrypt"] and mandatory["host"] == "content.livingruntime.com", mandatory
    for path in ["app/ui.js","core/rfb.js","app/styles/base.css"]:
        assert client.get(base+"/operator/browser/x/"+path).status_code == 200, path
    cookie = "; ".join(f"{k}={v}" for k,v in client.cookies.items())
    frames = []
    for iteration in range(2):
        with connect(
            base.replace("https://","wss://")+"/operator/browser/x/websockify",
            origin=base, additional_headers={"Cookie":cookie}, subprotocols=["binary"],
            open_timeout=10,
        ) as ws:
            buffer = bytearray()
            def read(n):
                while len(buffer)<n: buffer.extend(ws.recv(timeout=10))
                data=bytes(buffer[:n]);del buffer[:n];return data
            assert read(12).startswith(b"RFB ")
            ws.send(b"RFB 003.008\n")
            methods=read(read(1)[0]);assert 1 in methods
            ws.send(b"\x01");assert read(4)==b"\0"*4
            ws.send(b"\x01");init=read(24);w,h=struct.unpack("!HH",init[:4])
            read(struct.unpack("!I",init[20:24])[0])
            ws.send(struct.pack("!BBHi",2,0,1,0))
            ws.send(struct.pack("!BBHHHH",3,0,0,0,64,64))
            header=read(4);assert header[0]==0 and struct.unpack("!H",header[2:])[0]>0
            x,y,rw,rh,encoding=struct.unpack("!HHHHi",read(12));assert encoding==0
            pixels=read(rw*rh*(init[4]//8))
            frames.append({"desktop":[w,h],"frame_bytes":len(pixels)})
    status=client.get(base+"/operator/api/accounts/x/browser/status")
    assert status.status_code==200,status.text
    browser=status.json()["browser"]
    location=urlsplit(browser["url"])
after=json.loads(runtime_path.read_text())
assert before==after
print(json.dumps({
    "public_http":"PASS","public_wss_frames":frames,"iframe_headers":"PASS",
    "reconnect":"PASS","persistent_browser_unchanged":True,
    "x_authenticated":browser["x_authenticated"],
    "x_location":location.hostname+location.path,
}))
