from __future__ import annotations

from starlette.testclient import TestClient

from wechat_traffic_agent.live_agent import LiveAgentService


def _login_operator(client: TestClient, root) -> None:
    token = (root / "secrets" / "operator-console.token").read_text().strip()
    response = client.post(
        "/operator/api/session",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert response.status_code == 200


def test_cortex_browser_login_routes_are_operator_only(tmp_path, monkeypatch):
    import wechat_traffic_agent.chatgpt_cortex_browser_control as cortex_module

    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    calls = []

    class FakeControl:
        def __init__(self, *, state_root):
            self.state_root = state_root

        def start(self):
            calls.append("start")
            return {
                "running": True,
                "url": "https://chatgpt.com/",
                "title": "ChatGPT",
                "authenticated": False,
                "identity": "",
            }

        def stop(self):
            calls.append("stop")
            return {
                "ok": True,
                "running": False,
                "authenticated": True,
                "identity": "ChatGPT",
            }

        def target_id(self):
            return "cortex-target"

        def cdp_url(self):
            return "http://127.0.0.1:9500"

    monkeypatch.setattr(
        cortex_module,
        "ChatGPTCortexBrowserControl",
        FakeControl,
    )

    client = TestClient(LiveAgentService(
        state_root=tmp_path,
        application=None,
        backend=None,
        mind=None,
    )._app())

    unauthorized = client.post(
        "/operator/api/cortex/browser/login/connect",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert unauthorized.status_code == 401

    _login_operator(client, tmp_path)

    missing_header = client.post(
        "/operator/api/cortex/browser/login/connect",
        json={},
    )
    assert missing_header.status_code == 403

    connected = client.post(
        "/operator/api/cortex/browser/login/connect",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert connected.status_code == 200
    assert connected.json()["browser"]["url"] == "https://chatgpt.com/"

    stopped = client.post(
        "/operator/api/cortex/browser/login/stop",
        headers={"X-Operator-Request": "1"},
        json={},
    )
    assert stopped.status_code == 200
    assert stopped.json()["identity"] == "ChatGPT"
    assert calls == ["start", "stop"]
