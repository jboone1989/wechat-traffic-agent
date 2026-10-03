import asyncio
import json
from types import SimpleNamespace
import pytest
from wechat_traffic_agent.account_registry import AccountRegistry
from wechat_traffic_agent.local_browser_control import LocalBrowserControl, LocalBrowserControlUnavailable
from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
from wechat_traffic_agent.browser_accounts import x_account_binding

def accounts(root):
    registry = AccountRegistry(root)
    alice = registry.ensure(provider_id="x", identity="alice")
    bob = registry.ensure(provider_id="x", identity="bob")
    registry.set_browser_profile(alice.account_id, "default")
    return alice, bob

def test_operator_accounts_have_separate_contexts_and_verify_identity(tmp_path):
    alice, bob = accounts(tmp_path)
    control = LocalBrowserControl(state_root=tmp_path)
    a, b = control.for_account(alice.account_id), control.for_account(bob.account_id)
    assert a._account_id == "default"
    assert b._account_id == bob.account_id
    assert a is not b and control.for_account(bob.account_id) is b
    class Browser:
        def evaluate(self, *args):
            return {"profile":"/alice", "url":"https://x.com/home"}
    a._provider = Browser(); a._running = True
    b._provider = Browser(); b._running = True
    assert a.status()["account_identity"] == "alice"
    with pytest.raises(LocalBrowserControlUnavailable, match="selected account"):
        b.status()
    assert not (tmp_path / "accounts/browser-verifications" / (bob.account_id+".json")).exists()

def test_explicit_publishing_account_binds_context_and_expected_handle(tmp_path, monkeypatch):
    alice, bob = accounts(tmp_path)
    class Browser:
        def for_account(self, context):
            return SimpleNamespace(context=context)
    monkeypatch.setattr(LocalHarnessXTransport, "_provider", classmethod(lambda cls: Browser()))
    a = LocalHarnessXTransport.from_environment(state_root=tmp_path, account_id=alice.account_id)
    b = LocalHarnessXTransport.from_environment(state_root=tmp_path, account_id=bob.account_id)
    assert (a.browser.context, a.expected_handle) == ("default", "alice")
    assert (b.browser.context, b.expected_handle) == (bob.account_id, "bob")
    with pytest.raises(KeyError):
        x_account_binding(tmp_path, "acct_x_000000000000")

def test_status_endpoint_routes_selected_account_without_default_session(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from wechat_traffic_agent.live_agent import LiveAgentService
    from test_operator_console import _login_operator
    import wechat_traffic_agent.local_browser_control as module
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    selected=[]
    class Control:
        def __init__(self, **kwargs): pass
        def for_account(self, account_id):
            selected.append(account_id)
            return self
        def target_id(self): return "selected-account-tab"
        def status(self):
            return {"running":True, "x_authenticated":False, "account_identity":""}
    monkeypatch.setattr(module,"LocalBrowserControl",Control)
    client=TestClient(LiveAgentService(state_root=tmp_path,application=None,backend=None,mind=None)._app())
    _login_operator(client,tmp_path)
    response=client.get("/operator/api/accounts/x/browser/status?account_id=acct_x_0123456789ab")
    assert response.status_code==200
    assert selected==["acct_x_0123456789ab"]
