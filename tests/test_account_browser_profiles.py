from __future__ import annotations

from wechat_traffic_agent.account_browser_profiles import AccountBrowserProfileManager
from wechat_traffic_agent.account_registry import AccountRegistry


class FakeProcess:
    def __init__(self):
        self.pid = 4242
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0


def test_account_browser_profile_is_account_scoped_and_persistent(tmp_path, monkeypatch):
    registry = AccountRegistry(tmp_path)
    account = registry.ensure(provider_id="x", identity="alice_one")
    launched = []
    process = FakeProcess()

    def fake_popen(command, **kwargs):
        launched.append((list(command), dict(kwargs)))
        return process

    manager = AccountBrowserProfileManager(tmp_path, popen_factory=fake_popen)
    monkeypatch.setattr(manager, "_chrome_binary", lambda: "/fake/chrome")
    monkeypatch.setattr(manager, "_port_open", lambda port: False)
    monkeypatch.setattr(manager, "_cdp_ready", lambda port: bool(launched))

    runtime = manager.ensure(account.account_id)
    assert runtime.running is True
    assert runtime.port == 9400
    assert runtime.profile_id == f"browser_{account.account_id}"
    command = launched[0][0]
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9400" in command
    profile_arg = next(x for x in command if x.startswith("--user-data-dir="))
    assert account.account_id in profile_arg
    assert registry.get(account.account_id).browser_profile_id == runtime.profile_id

    state = tmp_path / "accounts" / "browser-profiles" / account.account_id / "runtime.json"
    profile = tmp_path / "accounts" / "browser-profiles" / account.account_id / "chrome-profile"
    assert state.exists()
    assert profile.exists()
    assert oct(state.stat().st_mode & 0o777) == "0o600"
    assert oct(profile.stat().st_mode & 0o777) == "0o700"
