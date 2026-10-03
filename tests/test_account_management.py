from __future__ import annotations

from wechat_traffic_agent.account_management import AccountManager
import wechat_traffic_agent.browser_runtime as browser_runtime
import wechat_traffic_agent.x_local_browser as x_local_browser


def _ready_browser_status():
    return {
        "status": "READY",
        "profiles": [
            {"profile_id": "chatgpt", "status": "READY", "cdp_url": "http://127.0.0.1:9600"},
            {"profile_id": "x", "status": "READY", "cdp_url": "http://127.0.0.1:9601"},
        ],
    }


def test_account_catalog_exposes_x_and_future_adapter_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_runtime, "browser_runtime_status", _ready_browser_status)
    status = AccountManager(tmp_path, xurl_status_provider=lambda: "").status()
    ids = {row["provider_id"] for row in status["accounts"]}
    assert {"x", "youtube", "email"} <= ids


def test_x_instances_bind_web_automation_to_agent_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_runtime, "browser_runtime_status", _ready_browser_status)
    manager = AccountManager(tmp_path, xurl_status_provider=lambda: "")
    manager.registry.ensure(
        provider_id="x",
        identity="alice",
        oauth_app="content-agent",
        oauth_user="alice",
        oauth_status="CONNECTED",
    )

    status = manager.status()
    instance = next(row for row in status["instances"] if row["identity"] == "alice")
    web = next(
        row for row in instance["bindings"]
        if row["binding_id"] == "agent_runtime_browser"
    )
    assert web["status"] == "NEEDS_LOGIN"
    assert web["browser_context"] == instance["account_id"]
    assert web["profile_id"] == "x"
    assert web["automation_engine"] == "AGENT_RUNTIME_BROWSER"
    assert instance["automation"]["compatibility_fallback"] == ""


class _VerifyBrowser:
    def invoke(self, capability_id, payload):
        return {"ok": True}

    def run_session_script(self, session_id, script, *, timeout_seconds):
        return {
            "profile": "/alice",
            "url": "https://x.com/home",
        }


def test_x_verification_uses_agent_runtime_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(
        x_local_browser,
        "build_agent_runtime_x_browser",
        lambda: _VerifyBrowser(),
    )
    manager = AccountManager(tmp_path, xurl_status_provider=lambda: "")
    result = manager.verify("x")
    assert result["status"] == "CONNECTED"
    assert result["identity"] == "alice"


class _BrokenVerifyBrowser:
    def invoke(self, capability_id, payload):
        raise RuntimeError("browser unavailable")


def test_x_verification_surfaces_agent_runtime_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        x_local_browser,
        "build_agent_runtime_x_browser",
        lambda: _BrokenVerifyBrowser(),
    )
    manager = AccountManager(tmp_path, xurl_status_provider=lambda: "")
    result = manager.verify("x")
    assert result["status"] == "DEGRADED"
    assert result["last_error"] == "RuntimeError"


def test_deleted_x_identity_is_not_reimported(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_runtime, "browser_runtime_status", _ready_browser_status)
    manager = AccountManager(
        tmp_path,
        xurl_status_provider=lambda: "content-agent\noauth2: alice\n",
    )
    first = manager.status()
    instance = next(row for row in first["instances"] if row["identity"] == "alice")
    manager.registry.remove(instance["account_id"])

    second = manager.status()
    assert not any(row["identity"] == "alice" for row in second["instances"])
