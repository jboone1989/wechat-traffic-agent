from __future__ import annotations

from wechat_traffic_agent.account_browser_profiles import (
    AccountBrowserProfileError,
    AccountBrowserProfileManager,
)
from wechat_traffic_agent.account_registry import AccountRegistry


def test_account_browser_profile_is_account_scoped_and_persistent(tmp_path, monkeypatch):
    registry = AccountRegistry(tmp_path)
    account = registry.ensure(provider_id="x", identity="alice_one")
    manager = AccountBrowserProfileManager(tmp_path)
    session_id = "11111111-1111-4111-8111-111111111111"
    calls = []

    def fake_request(method, path, body=None, timeout=8.0):
        calls.append((method, path, body))
        if method == "GET" and path == "health":
            return {"status": "ok"}
        if method == "POST" and path == "sessions":
            return {
                "id": session_id,
                "status": "live",
                "websocketUrl": "ws://127.0.0.1:3000/",
                "debugUrl": "http://127.0.0.1:3000/v1/sessions/debug",
            }
        if method == "GET" and path == f"sessions/{session_id}":
            return {
                "id": session_id,
                "status": "live",
                "websocketUrl": "ws://127.0.0.1:3000/",
                "debugUrl": "http://127.0.0.1:3000/v1/sessions/debug",
            }
        if method == "POST" and path == f"sessions/{session_id}/release":
            return {"success": True, "id": session_id, "status": "released"}
        raise AssertionError((method, path, body))

    monkeypatch.setattr(manager, "_request_json", fake_request)

    runtime = manager.ensure(account.account_id)
    assert runtime.running is True
    assert runtime.session_id == session_id
    assert runtime.cdp_ws == "ws://127.0.0.1:3000/"
    assert runtime.provider == "STEEL_SELF_HOSTED"
    assert runtime.profile_id == f"steel_{account.account_id}"
    create = next(
        row for row in calls
        if row[0] == "POST" and row[1] == "sessions"
    )
    assert create[2]["persist"] is True
    assert create[2]["headless"] is True
    assert create[2]["userDataDir"].endswith(
        f"accounts/browser-profiles/{account.account_id}/chrome-profile"
    )

    state = tmp_path / "accounts" / "browser-profiles" / account.account_id / "runtime.json"
    profile = tmp_path / "accounts" / "browser-profiles" / account.account_id / "chrome-profile"
    active = tmp_path / "browser" / "steel-active.json"
    assert state.exists()
    assert active.exists()
    assert profile.exists()
    assert oct(state.stat().st_mode & 0o777) == "0o600"
    assert oct(profile.stat().st_mode & 0o777) == "0o700"

    stopped = manager.stop(account.account_id)
    assert stopped.running is False
    assert not state.exists()
    assert not active.exists()
    assert any(
        method == "POST" and path == f"sessions/{session_id}/release"
        for method, path, _ in calls
    )


def test_steel_runtime_refuses_to_steal_another_account_session(tmp_path, monkeypatch):
    registry = AccountRegistry(tmp_path)
    first = registry.ensure(provider_id="x", identity="alice_one")
    second = registry.ensure(provider_id="x", identity="bob_two")
    manager = AccountBrowserProfileManager(tmp_path)
    session_id = "22222222-2222-4222-8222-222222222222"

    manager._write_private_json(
        manager._active_path(),
        {
            "schema_version": 1,
            "account_id": first.account_id,
            "session_id": session_id,
        },
    )

    def fake_request(method, path, body=None, timeout=8.0):
        if method == "GET" and path == "health":
            return {"status": "ok"}
        assert method == "GET"
        assert path == f"sessions/{session_id}"
        return {
            "id": session_id,
            "status": "live",
            "websocketUrl": "ws://127.0.0.1:3000/",
        }

    monkeypatch.setattr(manager, "_request_json", fake_request)

    try:
        manager.ensure(second.account_id)
    except AccountBrowserProfileError as exc:
        assert "busy with account" in str(exc)
    else:
        raise AssertionError("Steel active session was silently stolen")


def test_delete_profile_removes_only_target_account_directory(tmp_path, monkeypatch):
    registry = AccountRegistry(tmp_path)
    first = registry.ensure(provider_id="x", identity="alice_one")
    second = registry.ensure(provider_id="x", identity="bob_two")
    manager = AccountBrowserProfileManager(tmp_path)

    first_dir = tmp_path / "accounts" / "browser-profiles" / first.account_id
    second_dir = tmp_path / "accounts" / "browser-profiles" / second.account_id
    (first_dir / "chrome-profile").mkdir(parents=True)
    (second_dir / "chrome-profile").mkdir(parents=True)
    (first_dir / "chrome-profile" / "cookie-state").write_text("first")
    (second_dir / "chrome-profile" / "cookie-state").write_text("second")
    monkeypatch.setattr(manager, "stop", lambda account_id: None)

    manager.delete_profile(first.account_id)

    assert first_dir.exists() is False
    assert second_dir.exists() is True
    assert (second_dir / "chrome-profile" / "cookie-state").read_text() == "second"
