import asyncio
from types import SimpleNamespace
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from wechat_traffic_agent.live_agent import LiveAgentService
from wechat_traffic_agent.local_browser_control import LocalBrowserControl
from wechat_traffic_agent.novnc_takeover import AgentRuntimeNoVNC, NoVNCUnavailable
import wechat_traffic_agent.novnc_takeover as novnc_module


def test_takeover_only_attaches_to_existing_runtime(monkeypatch):
    class Provider:
        def takeover_status(self, profile):
            assert profile == "x"
            return SimpleNamespace(browser_running=True, active=True, url="http://127.0.0.1:6081/vnc.html")
    monkeypatch.setattr(novnc_module, "PersistentBrowserTakeoverProvider", lambda **kw: Provider())
    viewer = AgentRuntimeNoVNC()
    assert viewer.origin() == "http://127.0.0.1:6081"
    assert viewer.connection()["transport"] == "NOVNC"
    assert "reconnect=1" in viewer.connection()["takeover_url"]


def test_missing_runtime_never_starts_an_empty_profile(monkeypatch):
    class Provider:
        def takeover_status(self, profile):
            return SimpleNamespace(browser_running=False, active=False, url=None)
    monkeypatch.setattr(novnc_module, "PersistentBrowserTakeoverProvider", lambda **kw: Provider())
    with pytest.raises(NoVNCUnavailable, match="unavailable"):
        AgentRuntimeNoVNC().connection()


@pytest.mark.parametrize("path", ["../runtime.json", "%2e%2e/runtime.json", "core/../x", "core\\x"])
def test_asset_path_cannot_escape_novnc_root(path):
    viewer = object.__new__(AgentRuntimeNoVNC)
    with pytest.raises(ValueError):
        asyncio.run(viewer.asset(path))


def test_open_reattaches_without_navigating_or_losing_login(monkeypatch):
    class Browser:
        def adopt_existing_tab(self, session, prefix):
            assert prefix == "https://x.com/"
            return {"target_id": "current"}
        def run_session_script(self, session, script, **kw):
            assert "Page.bringToFront" in script
            assert "\n" in script
        def evaluate(self, session, script):
            return {"url": "https://x.com/home", "title": "Home / X", "profile": "/ferro"}
    control = LocalBrowserControl()
    monkeypatch.setattr(control, "_browser", lambda: Browser())
    control._target_id = "stale"
    assert control.open()["account_identity"] == "ferro"
    assert control._target_id == "current"


def test_novnc_routes_require_session_and_same_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_OPERATOR_PUBLIC_URL", "http://testserver")
    class Viewer:
        async def asset(self, path):
            assert path == "app/ui.js"
            return 200, b"export default {}", "application/javascript"
        async def relay(self, websocket, *, authorize):
            assert authorize()
            await websocket.accept(subprotocol="binary")
            data = await websocket.receive_bytes()
            await websocket.send_bytes(data)
            await websocket.close()
    monkeypatch.setattr(novnc_module, "AgentRuntimeNoVNC", Viewer)
    client = TestClient(LiveAgentService(state_root=tmp_path, application=None, backend=None, mind=None)._app())
    assert client.get("/operator/browser/x/app/ui.js").status_code == 401
    assert client.get("/operator/browser/x/mandatory.json").status_code == 401
    with pytest.raises(WebSocketDisconnect) as unauth:
        with client.websocket_connect("/operator/browser/x/websockify"): pass
    assert unauth.value.code == 4401
    token = (tmp_path/"secrets/operator-console.token").read_text().strip()
    assert client.post("/operator/api/session", headers={"X-Operator-Request":"1"}, json={"token":token}).status_code == 200
    assert client.get("/operator/browser/x/app/ui.js").content == b"export default {}"
    mandatory = client.get("/operator/browser/x/mandatory.json").json()
    assert mandatory["host"] == "testserver" and mandatory["path"] == "operator/browser/x/websockify"
    assert mandatory["shared"] and mandatory["reconnect"]
    with pytest.raises(WebSocketDisconnect) as cross_origin:
        with client.websocket_connect("/operator/browser/x/websockify", headers={"Origin":"https://other.example"}): pass
    assert cross_origin.value.code == 4403
    with client.websocket_connect("/operator/browser/x/websockify", subprotocols=["binary"]) as ws:
        ws.send_bytes(b"RFB 003.008\n")
        assert ws.receive_bytes() == b"RFB 003.008\n"
